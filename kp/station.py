"""Stanowisko: wpis na bufor, przejęcie etapu, wpis po etapie, składanie, pakowanie i wycofanie ruchu
(etapy 3 i 4 planu śledzenia produkcji), a także widoki buforów."""
import json
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (render_template, redirect, url_for, request, flash, abort, jsonify,
                   session, current_app)
from flask_login import login_required, current_user
from sqlalchemy import func

from models import (db, get_or_404, AuditLog, Buffer, DepartmentEmployee, Movement, ProductionStage,
                    StockPosition, WorkInProgress, WorkOrder, WorkOrderComponent)
from ustawienia.lookup import find_buffer, find_department_employee, parse_badge
from . import kp_bp, service, stock, flow

UTC = timezone.utc
SESSION_KEY = 'kp_employee'
SESSION_TTL = timedelta(hours=12)


# ── Dostęp i pracownik stanowiska ─────────────────────────────────────────────

def stock_required(f):
    """admin + kontroler + operator + stanowisko (rola produkcja)"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_enter_stock:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def current_employee():
    """Pracownik działu zidentyfikowany plakietką na tym urządzeniu (sesja 12 godzin) albo None."""
    data = session.get(SESSION_KEY)
    if not data:
        return None
    try:
        at = datetime.fromisoformat(data['at'])
        if datetime.now(UTC) - at > SESSION_TTL:
            session.pop(SESSION_KEY, None)
            return None
        emp = db.session.get(DepartmentEmployee, data['id'])
    except (KeyError, ValueError, TypeError):
        session.pop(SESSION_KEY, None)
        return None
    if not emp or not emp.is_active:
        session.pop(SESSION_KEY, None)
        return None
    return emp


def _set_employee(emp):
    session[SESSION_KEY] = {'id': emp.id, 'at': datetime.now(UTC).isoformat()}


def _emp_json(emp):
    if not emp:
        return None
    return {'id': emp.id, 'name': emp.name, 'department': emp.department.name if emp.department else ''}


def _audit(action, target_id=None, detail=None):
    try:
        db.session.add(AuditLog(user_id=current_user.id, action=action, target_type='movement',
                                target_id=target_id, detail=detail, ip=request.remote_addr))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Stan buforów audit error: %s', exc)


def _recent_json(emp):
    return [{'id': m.id, 'zo': m.order.zo_number, 'product': m.order.product_name,
             'kind': m.kind_label, 'component': m.component.name, 'quantity': m.quantity,
             'buffer': (m.to_buffer.code if m.to_buffer else (m.from_buffer.code if m.from_buffer else '')),
             'at': current_app.jinja_env.filters['localdt'](m.created_at, '%d.%m %H:%M')}
            for m in stock.recent_entries(emp)]


def _wip_list(emp, order=None):
    """Pozycje w realizacji: pracownika zidentyfikowanego plakietką oraz (gdy podano) zlecenia."""
    rows = {w.id: w for w in stock.employee_wip(emp)}
    if order is not None:
        rows.update({w.id: w for w in stock.order_wip(order)})
    return [stock.wip_json(rows[k]) for k in sorted(rows)]


def _reply(msg, order, buffer_id, emp):
    """Odpowiedź po akcji: zlecenie i bufor w kontekście stanowiska, pozycje w realizacji, ostatnie ruchy."""
    buf = db.session.get(Buffer, buffer_id) if buffer_id else None
    return jsonify({'ok': True, 'msg': msg, 'order': stock.order_summary(order, buf),
                    'buffer': stock.buffer_summary(buf) if buf else None,
                    'wip': _wip_list(emp, order), 'recent': _recent_json(emp)})


def _int(raw):
    """Liczba całkowita z JSON-a (także z napisu), inaczej None. Odrzuca bool i ułamki."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


# ── Stanowisko ────────────────────────────────────────────────────────────────

@kp_bp.route('/stanowisko')
@login_required
@stock_required
def station():
    return render_template('kp/station.html', employee=_emp_json(current_employee()),
                           recent=_recent_json(current_employee()),
                           needs_badge=not current_user.is_kontroler)


@kp_bp.route('/stanowisko/skan', methods=['POST'])
@login_required
@stock_required
def station_scan():
    """Rozpoznaje zeskanowany kod: plakietka pracownika, kod bufora albo kod QR zlecenia. Opcjonalne
    `order_id` i `buffer_id` to kontekst stanowiska (zlecenie i bufor zeskanowane wcześniej)."""
    body = request.get_json(silent=True) or {}
    text = (body.get('code') or '').strip()
    if not text:
        return jsonify({'error': 'Pusty kod.'}), 400
    ctx_order = db.session.get(WorkOrder, _int(body.get('order_id')) or 0)
    ctx_buffer = db.session.get(Buffer, _int(body.get('buffer_id')) or 0)

    # Plakietka pracownika działu
    if parse_badge(text) is not None:
        emp = find_department_employee(text)
        if not emp:
            return jsonify({'error': 'Nie rozpoznano plakietki (nieznany lub nieaktywny pracownik).'}), 404
        _set_employee(emp)
        return jsonify({'ok': True, 'type': 'employee', 'employee': _emp_json(emp),
                        'recent': _recent_json(emp), 'wip': _wip_list(emp, ctx_order)})

    emp = current_employee()

    # Kod QR zlecenia (JSON z polami p, c, o, q)
    if text.startswith('{'):
        try:
            obj = json.loads(text)
        except ValueError:
            return jsonify({'error': 'Nieprawidłowy kod QR zlecenia.'}), 400
        zo = str(obj.get('o') or '').split('?')[0].strip()
        product = str(obj.get('p') or '').strip()
        if not zo or not product:
            return jsonify({'error': 'Kod QR zlecenia nie zawiera numeru ZO lub produktu.'}), 400
        order = service.find_order(zo, product)
        if not order:
            return jsonify({'error': f'Zlecenia ZO {zo} ({product}) nie ma w systemie. Zlecenie zakłada '
                                     f'kontroler skanem kodu w module Kontrola produkcji.',
                            'unknown_order': True}), 404
        return jsonify({'ok': True, 'type': 'order', 'order': stock.order_summary(order, ctx_buffer),
                        'wip': _wip_list(emp, order)})

    # Kod bufora (QR lub kod kreskowy z etykiety)
    buf = find_buffer(text)
    if not buf:
        return jsonify({'error': f'Nie znaleziono bufora o kodzie „{text}”.'}), 404
    return jsonify({'ok': True, 'type': 'buffer', 'buffer': stock.buffer_summary(buf),
                    'order': stock.order_summary(ctx_order, buf) if ctx_order else None,
                    'wip': _wip_list(emp, ctx_order)})


@kp_bp.route('/stanowisko/wyloguj', methods=['POST'])
@login_required
@stock_required
def station_logout_employee():
    session.pop(SESSION_KEY, None)
    return jsonify({'ok': True})


@kp_bp.route('/stanowisko/wpis', methods=['POST'])
@login_required
@stock_required
def station_enter():
    """Wpis ilości elementu zlecenia na bufor. Wymaga pracownika zidentyfikowanego plakietką
    (admin i kontroler mogą wpisywać bez plakietki)."""
    data = request.get_json(silent=True) or {}
    emp = current_employee()
    if emp is None and not current_user.is_kontroler:
        return jsonify({'error': 'Najpierw zeskanuj swoją plakietkę.', 'need_employee': True}), 401
    comp = db.session.get(WorkOrderComponent, _int(data.get('component_id')) or 0)
    buf = db.session.get(Buffer, _int(data.get('buffer_id')) or 0)
    if not comp:
        return jsonify({'error': 'Nie znaleziono elementu zlecenia.'}), 404
    mv, err = stock.enter(comp, buf, _int(data.get('quantity')), emp, current_user, _int(data.get('stage_id')))
    if err:
        db.session.rollback()
        return jsonify({'error': err}), 400
    db.session.commit()
    _audit('kp_stock_enter', mv.id, f'ZO={comp.order.zo_number} {comp.name} x{mv.quantity} -> {buf.code}')
    wait = f' Czeka na etap „{mv.wait_stage.name}”.' if mv.wait_stage else ''
    return _reply(f'Wpisano {mv.quantity} szt. „{comp.name}” na bufor {buf.code}.{wait}', comp.order, buf.id, emp)


def _action(emp_required=True):
    """Wspólny początek akcji: pracownik (kontroler i admin mogą bez plakietki) i treść żądania."""
    emp = current_employee()
    if emp_required and emp is None and not current_user.is_kontroler:
        return None, None, (jsonify({'error': 'Najpierw zeskanuj swoją plakietkę.', 'need_employee': True}), 401)
    return emp, (request.get_json(silent=True) or {}), None


@kp_bp.route('/stanowisko/przejecie', methods=['POST'])
@login_required
@stock_required
def station_takeover():
    """Przejęcie etapu: ilość elementu schodzi z bufora do stanu „w realizacji”."""
    emp, data, fail = _action()
    if fail:
        return fail
    comp = db.session.get(WorkOrderComponent, _int(data.get('component_id')) or 0)
    buf = db.session.get(Buffer, _int(data.get('buffer_id')) or 0)
    if not comp:
        return jsonify({'error': 'Nie znaleziono elementu zlecenia.'}), 404
    mv, err = flow.takeover(comp, buf, data.get('stage_id'), _int(data.get('quantity')), emp, current_user,
                            data.get('machine_id'), data.get('recheck_stage_id'))
    if err:
        db.session.rollback()
        return jsonify({'error': err}), 400
    db.session.commit()
    _audit('kp_takeover', mv.id, f'ZO={comp.order.zo_number} {comp.name} x{mv.quantity} etap={mv.stage.name}')
    machine = f' na maszynie {mv.machine.name}' if mv.machine else ''
    return _reply(f'Przejęto etap „{mv.stage.name}”{machine}: {mv.quantity} szt. „{comp.name}”. '
                  f'Po wykonaniu odstaw na dowolny bufor.', comp.order, buf.id, emp)


@kp_bp.route('/stanowisko/zloz', methods=['POST'])
@login_required
@stock_required
def station_assemble():
    """Składanie: elementy z zeskanowanego bufora zamieniają się w sztuki gotowego produktu w realizacji."""
    emp, data, fail = _action()
    if fail:
        return fail
    order = db.session.get(WorkOrder, _int(data.get('order_id')) or 0)
    buf = db.session.get(Buffer, _int(data.get('buffer_id')) or 0)
    if not order:
        return jsonify({'error': 'Nie znaleziono zlecenia.'}), 404
    mv, err = flow.assemble(order, buf, _int(data.get('quantity')), emp, current_user, data.get('machine_id'))
    if err:
        db.session.rollback()
        return jsonify({'error': err}), 400
    db.session.commit()
    _audit('kp_assemble', mv.id, f'ZO={order.zo_number} złożono {mv.quantity} szt. produktu')
    return _reply(f'Złożono {mv.quantity} szt. produktu. Po wykonaniu odstaw na dowolny bufor.',
                  order, buf.id, emp)


@kp_bp.route('/stanowisko/przenies', methods=['POST'])
@login_required
@stock_required
def station_transfer():
    """Wpis po etapie: ilość z „w realizacji” trafia na dowolny bufor i czeka na wskazany etap trasy."""
    emp, data, fail = _action()
    if fail:
        return fail
    wip = db.session.get(WorkInProgress, _int(data.get('wip_id')) or 0)
    buf = db.session.get(Buffer, _int(data.get('buffer_id')) or 0)
    if not wip:
        return jsonify({'error': 'Ta pozycja nie jest już w realizacji.'}), 404
    order = wip.component.order
    name = wip.component.name
    mv, err = flow.transfer(wip, buf, _int(data.get('quantity')), emp, current_user, data.get('wait_stage_id'))
    if err:
        db.session.rollback()
        return jsonify({'error': err}), 400
    db.session.commit()
    _audit('kp_transfer', mv.id, f'ZO={order.zo_number} {name} x{mv.quantity} -> {buf.code}')
    wait = f' Czeka na etap „{mv.wait_stage.name}”.' if mv.wait_stage else ''
    if mv.control_id:
        wait = ' Ilość czeka na kontrolę.' + wait
    return _reply(f'Wpisano {mv.quantity} szt. „{name}” na bufor {buf.code}.{wait}', order, buf.id, emp)


@kp_bp.route('/stanowisko/spakowane', methods=['POST'])
@login_required
@stock_required
def station_pack():
    """Oznacza ilość gotowego produktu jako spakowaną. Spakowane całe `q` kończy zlecenie."""
    emp, data, fail = _action()
    if fail:
        return fail
    wip = db.session.get(WorkInProgress, _int(data.get('wip_id')) or 0)
    if not wip:
        return jsonify({'error': 'Ta pozycja nie jest już w realizacji.'}), 404
    order = wip.component.order
    mv, err = flow.pack(wip, _int(data.get('quantity')), emp, current_user)
    if err:
        db.session.rollback()
        return jsonify({'error': err}), 400
    db.session.commit()
    _audit('kp_pack', mv.id, f'ZO={order.zo_number} spakowano {mv.quantity} szt.')
    done = ' Zlecenie zakończone.' if order.status == 'done' else ''
    return _reply(f'Spakowano {mv.quantity} szt.{done}', order, _int(data.get('buffer_id')), emp)


@kp_bp.route('/ruch/<int:movement_id>/cofnij', methods=['POST'])
@login_required
@stock_required
def revert_movement(movement_id):
    m = get_or_404(Movement, movement_id)
    emp = current_employee()
    order = m.order
    body = request.get_json(silent=True) or {}
    new_mv, err = flow.revert(m, current_user, emp)
    wants_json = request.is_json or request.headers.get('X-Requested-With') == 'fetch'
    if err:
        db.session.rollback()
        if wants_json:
            return jsonify({'error': err}), 400
        flash(err, 'warning')
        return redirect(url_for('kp.order_detail', order_id=m.order_id))
    db.session.commit()
    _audit('kp_revert', new_mv.id, f'ruch={m.id} {m.kind} ZO={order.zo_number} {m.component.name} x{m.quantity}')
    msg = f'Wycofano: {m.kind_label.lower()}, {m.quantity} szt. „{m.component.name}”.'
    if wants_json:
        return _reply(msg, order, _int(body.get('buffer_id')), emp)
    flash(msg, 'success')
    return redirect(url_for('kp.order_detail', order_id=m.order_id))


# ── Widoki buforów (odczyt dla wszystkich zalogowanych) ───────────────────────

@kp_bp.route('/bufory')
@login_required
def buffers():
    code = request.args.get('code', '').strip()
    if code:
        b = find_buffer(code)
        if b:
            return redirect(url_for('kp.buffer_detail', buffer_id=b.id))
        flash(f'Nie znaleziono bufora o kodzie „{code}”.', 'warning')
    items = Buffer.query.order_by(Buffer.code).all()
    totals = dict(db.session.query(StockPosition.buffer_id, func.sum(StockPosition.quantity))
                  .filter(StockPosition.quantity > 0).group_by(StockPosition.buffer_id).all())
    positions = dict(db.session.query(StockPosition.buffer_id, func.count(StockPosition.id))
                     .filter(StockPosition.quantity > 0).group_by(StockPosition.buffer_id).all())
    return render_template('kp/buffers.html', buffers=items, totals=totals, positions=positions, code=code)


@kp_bp.route('/bufory/<int:buffer_id>')
@login_required
def buffer_detail(buffer_id):
    b = get_or_404(Buffer, buffer_id)
    history = (Movement.query.filter((Movement.to_buffer_id == b.id) | (Movement.from_buffer_id == b.id))
               .order_by(Movement.created_at.desc(), Movement.id.desc()).limit(40).all())
    stages = ProductionStage.query.filter_by(is_active=True).order_by(ProductionStage.order, ProductionStage.id).all()
    return render_template('kp/buffer_detail.html', buffer=b, contents=stock.buffer_contents(b),
                           history=history, stages=stages)
