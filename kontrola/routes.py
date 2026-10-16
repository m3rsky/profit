from datetime import datetime, timezone
from functools import wraps

from flask import (render_template, redirect, url_for, request,
                   flash, abort, current_app, jsonify)
from flask_login import login_required, current_user
from sqlalchemy import func, or_

from models import (db, get_or_404, AuditLog, Alert, User, QARReport,
                    ControlPoint, ControlRecord, ControlResult,
                    CONTROL_STATE_LABELS)
from . import kontrola_bp

UTC = timezone.utc


# ── Dekoratory dostępu ────────────────────────────────────────────────────────

def view_required(f):
    """admin + kontroler + operator"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_view_control:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def controller_required(f):
    """admin + kontroler"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_kontroler:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def operator_required(f):
    """admin + operator"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not (current_user.is_admin or current_user.is_operator):
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


# ── Pomocnicze ────────────────────────────────────────────────────────────────

def _audit(action, target_id=None, detail=None):
    try:
        uid = current_user.id if current_user.is_authenticated else None
        db.session.add(AuditLog(
            user_id=uid, action=action, target_type='control_record',
            target_id=target_id, detail=detail, ip=request.remote_addr))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Kontrola audit error: %s', exc)


def _clean_zo(raw):
    """Usuwa query string, który mógł zostać doklejony przez serwer proxy."""
    return str(raw or '').split('?')[0].strip()


def _active_points():
    return (ControlPoint.query.filter_by(is_active=True)
            .order_by(ControlPoint.order, ControlPoint.id).all())


def _add_results(rec, rows):
    """Dodaje do kontroli wiersze punktów (kopia tytułu i opisu). `rows` to
    ControlPoint albo ControlResult z poprzedniej kontroli. Bez commitu."""
    for i, p in enumerate(rows, 1):
        point_id = p.id if isinstance(p, ControlPoint) else p.point_id
        db.session.add(ControlResult(record=rec, point_id=point_id, position=i,
                                     title=p.title, description=p.description))


def _notify(roles, message, alert_type='info'):
    for u in User.query.filter(User.role.in_(roles)).all():
        db.session.add(Alert(recipient_id=u.id, message=message[:512], alert_type=alert_type))


def _norm(text):
    return ' '.join((text or '').upper().split())


def _label(rec):
    """Opis zamówienia do komunikatów: pod jednym ZO może być kilka produktów."""
    return f'ZO {rec.zo_number}' + (f' ({rec.product_name})' if rec.product_name else '')


def _latest_for_order(zo, product_name):
    """Najnowszy etap cyklu dla pary ZO + produkt: kontrola sprawdzająca, jeśli istnieje,
    inaczej wstępna. Ten sam ZO z innym produktem to osobna kontrola."""
    wanted = _norm(product_name)
    candidates = (ControlRecord.query.filter_by(zo_number=zo, round=1)
                  .order_by(ControlRecord.id.desc()).all())
    first = next((r for r in candidates if _norm(r.product_name) == wanted), None)
    if not first:
        return None
    return first.followup or first


def _create_qar_for_result(rec, res):
    """Tworzy raport QAR dla punktu ocenionego jako NG i wiąże go z wynikiem."""
    from qar.routes import _next_qar_number
    extra = ' | '.join(x for x in (rec.product_name, rec.client) if x)
    kind = 'kontroli sprawdzającej' if rec.round == 2 else 'kontroli zamówienia'
    description = res.notes or f'Niezgodność (NG) stwierdzona podczas {kind}, punkt: {res.title}.'
    if extra:
        description += ' | Zlecenie: ' + extra
    qar = QARReport(
        number=_next_qar_number(),
        zo_number=rec.zo_number,
        title=f'Kontrola zamówienia NG: {res.title}, ZO {rec.zo_number}'[:256],
        location=res.title[:128],
        description=description,
        user_id=current_user.id,
    )
    db.session.add(qar)
    db.session.flush()
    res.qar_report_id = qar.id
    return qar


def _apply_form(rec, form):
    """Zapisuje oceny punktów z formularza. Zwraca listę numerów utworzonych QAR."""
    created = []
    now = datetime.now(UTC)
    for res in rec.results:
        result = form.get(f'result_{res.id}') or None
        if result not in (None, 'OK', 'NG'):
            result = None
        notes = (form.get(f'notes_{res.id}') or '').strip() or None
        if (res.result, res.notes) != (result, notes):
            res.checked_at = now if result else None
        res.result, res.notes = result, notes
        if result == 'NG' and not res.qar_report_id:
            created.append(_create_qar_for_result(rec, res).number)
    return created


# ── Lista ─────────────────────────────────────────────────────────────────────

@kontrola_bp.route('/')
@login_required
@view_required
def list_records():
    is_op = current_user.is_operator
    zo_filter = request.args.get('zo', '').strip()
    flt = request.args.get('s') or ('to_do' if is_op else 'all')

    q = ControlRecord.query.filter_by(round=1).order_by(ControlRecord.created_at.desc())
    if zo_filter:
        like = f'%{zo_filter}%'
        q = q.filter(or_(ControlRecord.zo_number.ilike(like),
                         ControlRecord.product_name.ilike(like),
                         ControlRecord.client.ilike(like)))
    records = q.all()
    if is_op:
        # Operator widzi dopiero zakończone kontrole.
        records = [r for r in records if r.state != 'in_control']

    counts = {k: 0 for k in CONTROL_STATE_LABELS}
    for r in records:
        counts[r.state] += 1
    if flt in CONTROL_STATE_LABELS:
        records = [r for r in records if r.state == flt]
    else:
        flt = 'all'

    return render_template('kontrola/list.html', records=records, counts=counts,
                           flt=flt, zo_filter=zo_filter, labels=CONTROL_STATE_LABELS,
                           total=sum(counts.values()))


# ── Skan QR (ten sam kod co kontrola końcowa: p, c, o, q) ────────────────────

@kontrola_bp.route('/scan')
@login_required
@controller_required
def scan_page():
    return render_template('kontrola/scan.html')


@kontrola_bp.route('/from-qr', methods=['POST'])
@login_required
@controller_required
def from_qr():
    data         = request.get_json(silent=True) or {}
    product_name = (data.get('p') or '').strip()
    client       = (data.get('c') or '').strip()
    zo           = _clean_zo(data.get('o'))
    try:
        quantity = max(1, min(99, int(data.get('q', 1))))
    except (TypeError, ValueError):
        quantity = 1

    if not zo:
        return jsonify({'error': 'Brak numeru zlecenia („o") w kodzie QR'}), 400

    existing = _latest_for_order(zo, product_name)
    if existing:
        _audit('kontrola_scan_existing', existing.id, f'ZO={zo} product={product_name}')
        return jsonify({
            'ok': True,
            'redirect': url_for('kontrola.view_record', record_id=existing.id),
            'msg': f'Kontrola dla {_label(existing)} już istnieje, otwieram.',
        })

    points = _active_points()
    if not points:
        return jsonify({'error': 'Brak punktów kontrolnych. Administrator musi je dodać w '
                                 'ustawieniach kontroli zamówień.'}), 400

    rec = ControlRecord(zo_number=zo, product_name=product_name or None,
                        client=client or None, quantity=quantity,
                        round=1, user_id=current_user.id)
    db.session.add(rec)
    db.session.flush()
    _add_results(rec, points)
    db.session.commit()
    _audit('kontrola_create', rec.id, f'ZO={zo} product={product_name} (QR)')
    return jsonify({
        'ok': True,
        'redirect': url_for('kontrola.view_record', record_id=rec.id),
        'msg': 'Utworzono kontrolę zamówienia.',
    })


# ── Podgląd i wypełnianie ─────────────────────────────────────────────────────

@kontrola_bp.route('/<int:record_id>', methods=['GET', 'POST'])
@login_required
@view_required
def view_record(record_id):
    rec = get_or_404(ControlRecord, record_id)
    if current_user.is_operator and rec.round == 1 and rec.state == 'in_control':
        abort(403)

    can_edit = current_user.is_kontroler and rec.status == 'in_progress'

    if request.method == 'POST':
        if not can_edit:
            abort(403)
        created = _apply_form(rec, request.form)
        complete = request.form.get('action') == 'complete'
        if complete and rec.rated_count < len(rec.results):
            complete = False
            flash('Oceń wszystkie punkty (OK lub NG), aby zakończyć kontrolę.', 'warning')
        elif not complete:
            flash('Zapisano.', 'success')
        if complete:
            rec.status = 'completed'
            rec.completed_at = datetime.now(UTC)
            if rec.round == 1:
                _notify(['operator'],
                        f'{_label(rec)}: kontrola zakończona ({rec.result}), '
                        f'zamówienie czeka na wykonanie.',
                        'urgent' if rec.result == 'NG' else 'info')
        db.session.commit()
        _audit('kontrola_complete' if complete else 'kontrola_save', rec.id,
               f'ZO={rec.zo_number} runda={rec.round}')
        if created:
            flash('Utworzono raporty QAR: ' + ', '.join(created) + '.', 'warning')
        if complete:
            flash('Kontrola zakończona. Wynik: ' + rec.result + '.', 'success')
            return redirect(url_for('kontrola.list_records'))
        return redirect(url_for('kontrola.view_record', record_id=rec.id))

    return render_template('kontrola/detail.html', rec=rec, can_edit=can_edit,
                           labels=CONTROL_STATE_LABELS)


# ── Operator: Wykonane -> kontrola sprawdzająca ───────────────────────────────

@kontrola_bp.route('/<int:record_id>/done', methods=['POST'])
@login_required
@operator_required
def mark_done(record_id):
    rec = get_or_404(ControlRecord, record_id)
    if rec.round != 1 or rec.status != 'completed' or rec.done_at:
        flash('To zamówienie nie czeka na wykonanie.', 'warning')
        return redirect(url_for('kontrola.list_records'))

    rec.done_at = datetime.now(UTC)
    rec.done_by_id = current_user.id
    follow = ControlRecord(zo_number=rec.zo_number, product_name=rec.product_name,
                           client=rec.client, quantity=rec.quantity, round=2,
                           previous_id=rec.id, user_id=rec.user_id)
    db.session.add(follow)
    db.session.flush()
    # Kontrola sprawdzająca używa aktualnej listy punktów; gdyby była pusta,
    # kopiujemy punkty z kontroli wstępnej.
    _add_results(follow, _active_points() or rec.results)
    _notify(['admin', 'kontroler'],
            f'{_label(rec)}: oznaczone jako wykonane przez {current_user.username}, '
            f'czeka na kontrolę sprawdzającą.')
    db.session.commit()
    _audit('kontrola_done', rec.id, f'ZO={rec.zo_number}')
    flash(f'{_label(rec)} oznaczone jako wykonane. Utworzono kontrolę sprawdzającą.', 'success')
    return redirect(url_for('kontrola.list_records'))


@kontrola_bp.route('/<int:record_id>/reopen', methods=['POST'])
@login_required
@admin_required
def reopen(record_id):
    """Admin cofa zakończenie kontroli (pomyłka kontrolera). Nie dotyczy kontroli
    wstępnej, po której operator już oznaczył wykonanie."""
    rec = get_or_404(ControlRecord, record_id)
    if rec.status != 'completed' or (rec.round == 1 and rec.done_at):
        flash('Tej kontroli nie można wznowić.', 'warning')
    else:
        rec.status = 'in_progress'
        rec.completed_at = None
        db.session.commit()
        _audit('kontrola_reopen', rec.id, f'ZO={rec.zo_number} runda={rec.round}')
        flash('Kontrola wznowiona.', 'success')
    return redirect(url_for('kontrola.view_record', record_id=rec.id))


def _delete_cycles(records):
    """Usuwa całe cykle kontroli (wstępną razem ze sprawdzającą). Raporty QAR
    utworzone z NG zostają, bo to osobne dokumenty niezgodności. Zwraca liczbę cykli."""
    roots = {r.previous or r for r in records}
    for root in roots:
        if root.followup is not None:
            db.session.delete(root.followup)
            db.session.flush()
        db.session.delete(root)
    db.session.commit()
    return len(roots)


@kontrola_bp.route('/<int:record_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_record(record_id):
    rec = get_or_404(ControlRecord, record_id)
    zo = rec.zo_number
    _delete_cycles([rec])
    _audit('kontrola_delete', None, f'ZO={zo}')
    flash(f'Usunięto kontrolę zamówienia ZO {zo}.', 'success')
    return redirect(url_for('kontrola.list_records'))


@kontrola_bp.route('/bulk-delete', methods=['POST'])
@login_required
@admin_required
def bulk_delete():
    ids = request.form.getlist('record_ids', type=int)
    records = ControlRecord.query.filter(ControlRecord.id.in_(ids)).all() if ids else []
    if not records:
        flash('Nie zaznaczono żadnych kontroli.', 'warning')
        return redirect(url_for('kontrola.list_records'))
    count = _delete_cycles(records)
    _audit('kontrola_bulk_delete', None, f'count={count}')
    flash(f'Usunięto kontrole zamówień: {count}.', 'success')
    return redirect(url_for('kontrola.list_records'))


# ── Admin: punkty kontrolne ───────────────────────────────────────────────────

@kontrola_bp.route('/points')
@login_required
@admin_required
def points():
    pts = ControlPoint.query.order_by(ControlPoint.order, ControlPoint.id).all()
    return render_template('kontrola/points.html', points=pts)


@kontrola_bp.route('/points/add', methods=['POST'])
@login_required
@admin_required
def point_add():
    title = request.form.get('title', '').strip()
    if not title:
        flash('Nazwa punktu jest wymagana.', 'error')
        return redirect(url_for('kontrola.points'))
    max_order = db.session.query(func.max(ControlPoint.order)).scalar() or 0
    db.session.add(ControlPoint(title=title[:256], order=max_order + 1,
                                description=request.form.get('description', '').strip() or None))
    db.session.commit()
    _audit('kontrola_point_add', None, title)
    flash('Dodano punkt kontrolny.', 'success')
    return redirect(url_for('kontrola.points'))


@kontrola_bp.route('/points/<int:point_id>/save', methods=['POST'])
@login_required
@admin_required
def point_save(point_id):
    pt = get_or_404(ControlPoint, point_id)
    title = request.form.get('title', '').strip()
    if not title:
        flash('Nazwa punktu jest wymagana.', 'error')
        return redirect(url_for('kontrola.points'))
    pt.title = title[:256]
    pt.description = request.form.get('description', '').strip() or None
    pt.is_active = request.form.get('is_active') == '1'
    db.session.commit()
    _audit('kontrola_point_edit', None, f'{pt.id}: {pt.title}')
    flash('Zapisano punkt kontrolny.', 'success')
    return redirect(url_for('kontrola.points'))


@kontrola_bp.route('/points/<int:point_id>/move/<direction>', methods=['POST'])
@login_required
@admin_required
def point_move(point_id, direction):
    pts = ControlPoint.query.order_by(ControlPoint.order, ControlPoint.id).all()
    idx = next((i for i, p in enumerate(pts) if p.id == point_id), None)
    if idx is None:
        abort(404)
    swap = idx - 1 if direction == 'up' else idx + 1
    if 0 <= swap < len(pts):
        pts[idx], pts[swap] = pts[swap], pts[idx]
    for i, p in enumerate(pts, 1):
        p.order = i
    db.session.commit()
    return redirect(url_for('kontrola.points'))


@kontrola_bp.route('/points/<int:point_id>/delete', methods=['POST'])
@login_required
@admin_required
def point_delete(point_id):
    pt = get_or_404(ControlPoint, point_id)
    # Historia zostaje: wyniki mają własną kopię tytułu.
    ControlResult.query.filter_by(point_id=pt.id).update({'point_id': None})
    db.session.delete(pt)
    db.session.commit()
    _audit('kontrola_point_delete', None, f'{point_id}: {pt.title}')
    flash('Usunięto punkt kontrolny.', 'success')
    return redirect(url_for('kontrola.points'))
