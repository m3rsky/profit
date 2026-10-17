"""Kontrola produkcji: zlecenia produkcyjne ze skanu kodu QR (etap 2 planu śledzenia produkcji)."""
from datetime import datetime, timezone
from functools import wraps

from flask import (render_template, redirect, url_for, request, flash, abort,
                   current_app, jsonify)
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import (db, get_or_404, AuditLog, ProductionStage, WorkOrder, WorkOrderComponent,
                    WorkOrderComponentStage)
from . import kp_bp
from . import service

UTC = timezone.utc


# ── Dostęp ────────────────────────────────────────────────────────────────────

def controller_required(f):
    """admin + kontroler"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_kontroler:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _audit(action, target_id=None, detail=None):
    try:
        uid = current_user.id if current_user.is_authenticated else None
        db.session.add(AuditLog(user_id=uid, action=action, target_type='work_order',
                                target_id=target_id, detail=detail, ip=request.remote_addr))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Kontrola produkcji audit error: %s', exc)


def _clean_zo(raw):
    """Usuwa query string, który mógł zostać doklejony przez serwer proxy."""
    return str(raw or '').split('?')[0].strip()


def _qty(raw, default=1):
    try:
        return max(1, min(service.MAX_QTY, int(raw)))
    except (TypeError, ValueError):
        return default


# ── Lista zleceń (odczyt dla wszystkich zalogowanych) ─────────────────────────

@kp_bp.route('/')
@login_required
def list_orders():
    q_text = request.args.get('q', '').strip()
    status = request.args.get('s', 'active')
    query = WorkOrder.query.order_by(WorkOrder.created_at.desc(), WorkOrder.id.desc())
    if q_text:
        like = f'%{q_text}%'
        query = query.filter(or_(WorkOrder.zo_number.ilike(like),
                                 WorkOrder.product_name.ilike(like),
                                 WorkOrder.client.ilike(like)))
    counts = {
        'active': query.filter(WorkOrder.status == 'active').count(),
        'cancelled': query.filter(WorkOrder.status == 'cancelled').count(),
    }
    if status in WorkOrder.STATUS_LABELS:
        query = query.filter(WorkOrder.status == status)
    else:
        status = 'all'
    return render_template('kp/list.html', orders=query.all(), status=status, q=q_text,
                           counts=counts, total=counts['active'] + counts['cancelled'])


# ── Skan kodu QR zlecenia ─────────────────────────────────────────────────────

@kp_bp.route('/skan')
@login_required
@controller_required
def scan_page():
    return render_template('kp/scan.html')


@kp_bp.route('/z-qr', methods=['POST'])
@login_required
@controller_required
def from_qr():
    """Kod QR zlecenia (pola `p`, `c`, `o`, `q`): otwiera istniejące zlecenie (para ZO + produkt)
    albo tworzy nowe z szablonu produktu. Bez szablonu zlecenie nie powstaje."""
    data = request.get_json(silent=True) or {}
    product = (data.get('p') or '').strip()
    client = (data.get('c') or '').strip()
    zo = _clean_zo(data.get('o'))
    quantity = _qty(data.get('q'))

    if not zo:
        return jsonify({'error': 'Brak numeru zlecenia („o") w kodzie QR'}), 400
    if not product:
        return jsonify({'error': 'Brak nazwy produktu („p") w kodzie QR'}), 400

    existing = service.find_order(zo, product)
    if existing:
        _audit('kp_scan_existing', existing.id, f'ZO={zo} product={product}')
        msg = (f'Zlecenie ZO {existing.zo_number} ({existing.product_name}) jest anulowane.'
               if existing.status == 'cancelled'
               else f'Zlecenie ZO {existing.zo_number} ({existing.product_name}) już istnieje, otwieram.')
        return jsonify({'ok': True, 'existing': True,
                        'redirect': url_for('kp.order_detail', order_id=existing.id), 'msg': msg})

    template = service.find_template(product)
    if not template:
        return jsonify({
            'error': f'Produkt „{product}" nie ma szablonu z elementami. Najpierw utwórz szablon '
                     f'produktu (Ustawienia produkcji, Szablony produktów), potem zeskanuj kod ponownie.',
            'no_template': True,
            'create_url': url_for('ustawienia.product_new', name=product),
        }), 404

    # Ilość elementów na sztukę produktu zależy od zlecenia, więc zlecenie powstaje dopiero po jej
    # podaniu (krok 2, /utworz).
    return jsonify({'ok': True, 'needs_quantities': True, 'template': template.name,
                    'components': [{'id': c.id, 'name': c.name} for c in template.components],
                    'order': {'p': product, 'c': client, 'o': zo, 'q': quantity}})


@kp_bp.route('/utworz', methods=['POST'])
@login_required
@controller_required
def create_from_qr():
    """Krok 2: tworzy zlecenie z szablonu z podanymi ilościami elementów na sztukę produktu."""
    data = request.get_json(silent=True) or {}
    product = (data.get('p') or '').strip()
    client = (data.get('c') or '').strip()
    zo = _clean_zo(data.get('o'))
    quantity = _qty(data.get('q'))
    if not zo or not product:
        return jsonify({'error': 'Brak numeru zlecenia lub nazwy produktu'}), 400

    existing = service.find_order(zo, product)
    if existing:   # np. podwójne kliknięcie albo zlecenie założone w międzyczasie
        return jsonify({'ok': True, 'existing': True,
                        'redirect': url_for('kp.order_detail', order_id=existing.id),
                        'msg': f'Zlecenie ZO {existing.zo_number} ({existing.product_name}) już istnieje, otwieram.'})

    template = service.find_template(product)
    if not template:
        return jsonify({'error': f'Produkt „{product}" nie ma szablonu z elementami.',
                        'no_template': True,
                        'create_url': url_for('ustawienia.product_new', name=product)}), 404

    per_unit, err = service.parse_per_unit(template, data.get('per_unit'))
    if err:
        return jsonify({'error': err}), 400

    order = service.create_order(zo, product, client, quantity, current_user.id, template, per_unit)
    db.session.commit()
    _audit('kp_create', order.id, f'ZO={zo} product={product} qty={quantity} szablon={template.name}')
    return jsonify({'ok': True, 'redirect': url_for('kp.order_detail', order_id=order.id),
                    'template': template.name,
                    'msg': f'Utworzono zlecenie ZO {zo} z szablonu „{template.name}".'})


# ── Szczegóły zlecenia ────────────────────────────────────────────────────────

@kp_bp.route('/<int:order_id>')
@login_required
def order_detail(order_id):
    order = get_or_404(WorkOrder, order_id)
    stages = ProductionStage.query.filter_by(is_active=True, level='element').order_by(
        ProductionStage.order, ProductionStage.id).all() if current_user.is_kontroler else []
    return render_template('kp/detail.html', order=order, all_stages=stages)


# ── Korekty (tylko admin) ─────────────────────────────────────────────────────

def _back(order):
    return redirect(url_for('kp.order_detail', order_id=order.id))


def _editable(order):
    if order.status == 'cancelled':
        flash('Zlecenie jest anulowane. Przywróć je, aby je edytować.', 'warning')
        return False
    return True


@kp_bp.route('/<int:order_id>/ilosc', methods=['POST'])
@login_required
@admin_required
def set_order_quantity(order_id):
    order = get_or_404(WorkOrder, order_id)
    if not _editable(order):
        return _back(order)
    try:
        new_q = int(request.form.get('quantity'))
    except (TypeError, ValueError):
        new_q = 0
    if not 1 <= new_q <= service.MAX_QTY:
        flash(f'Podaj ilość od 1 do {service.MAX_QTY}.', 'warning')
        return _back(order)
    old_q = order.quantity
    service.set_quantity(order, new_q)
    db.session.commit()
    _audit('kp_quantity', order.id, f'{old_q} -> {new_q}')
    flash(f'Zmieniono ilość z {old_q} na {new_q}. Wymagane ilości elementów przeliczono.', 'success')
    return _back(order)


@kp_bp.route('/<int:order_id>/anuluj', methods=['POST'])
@login_required
@admin_required
def cancel_order(order_id):
    order = get_or_404(WorkOrder, order_id)
    order.status = 'cancelled'
    order.cancelled_at = datetime.now(UTC)
    db.session.commit()
    _audit('kp_cancel', order.id, f'ZO={order.zo_number} product={order.product_name}')
    flash(f'Anulowano zlecenie ZO {order.zo_number}.', 'success')
    return _back(order)


@kp_bp.route('/<int:order_id>/przywroc', methods=['POST'])
@login_required
@admin_required
def restore_order(order_id):
    order = get_or_404(WorkOrder, order_id)
    order.status = 'active'
    order.cancelled_at = None
    db.session.commit()
    _audit('kp_restore', order.id, f'ZO={order.zo_number}')
    flash(f'Przywrócono zlecenie ZO {order.zo_number}.', 'success')
    return _back(order)


def _apply_component_form(comp, form):
    name = form.get('name', '').strip()
    if not name:
        return 'Nazwa elementu jest wymagana.'
    per_unit = form.get('per_unit', type=int)
    required = form.get('required_qty', type=int)
    if not per_unit or per_unit < 1 or required is None or required < 1:
        return 'Ilość na sztukę i wymagana ilość muszą być liczbami większymi od zera.'
    valid = {s.id for s in ProductionStage.query.filter_by(level='element').all()}
    wanted = {int(x) for x in form.getlist('stage_ids') if x.isdigit()} & valid
    comp.name = name[:128]
    comp.per_unit = per_unit
    comp.required_qty = required
    WorkOrderComponentStage.query.filter_by(component_id=comp.id).delete()
    for sid in wanted:
        db.session.add(WorkOrderComponentStage(component_id=comp.id, stage_id=sid))
    return None


@kp_bp.route('/<int:order_id>/element/nowy', methods=['POST'])
@login_required
@controller_required
def component_add(order_id):
    order = get_or_404(WorkOrder, order_id)
    if not _editable(order):
        return _back(order)
    form = request.form.copy()
    if not form.get('required_qty') and form.get('per_unit', type=int):
        form['required_qty'] = str(form.get('per_unit', type=int) * order.quantity)
    comp = WorkOrderComponent(order_id=order.id, name='x', per_unit=1, required_qty=1,
                              position=len(order.components) + 1)
    db.session.add(comp)
    db.session.flush()
    err = _apply_component_form(comp, form)
    if err:
        db.session.rollback()
        flash(err, 'warning')
        return _back(order)
    db.session.commit()
    _audit('kp_component_add', order.id, comp.name)
    flash(f'Dodano element „{comp.name}".', 'success')
    return _back(order)


@kp_bp.route('/element/<int:comp_id>/zapisz', methods=['POST'])
@login_required
@controller_required
def component_save(comp_id):
    comp = get_or_404(WorkOrderComponent, comp_id)
    order = comp.order
    if not _editable(order):
        return _back(order)
    err = _apply_component_form(comp, request.form)
    if err:
        db.session.rollback()
        flash(err, 'warning')
        return _back(order)
    db.session.commit()
    _audit('kp_component_edit', order.id, comp.name)
    flash(f'Zapisano element „{comp.name}".', 'success')
    return _back(order)


@kp_bp.route('/element/<int:comp_id>/usun', methods=['POST'])
@login_required
@controller_required
def component_delete(comp_id):
    comp = get_or_404(WorkOrderComponent, comp_id)
    order = comp.order
    if not _editable(order):
        return _back(order)
    if len(order.components) <= 1:
        flash('Zlecenie musi mieć co najmniej jeden element.', 'warning')
        return _back(order)
    name = comp.name
    db.session.delete(comp)
    db.session.commit()
    _audit('kp_component_delete', order.id, name)
    flash(f'Usunięto element „{name}".', 'success')
    return _back(order)
