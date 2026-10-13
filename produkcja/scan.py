"""Stanowisko produkcji: pracownik skanuje swoją plakietkę, potem kod QR zlecenia
i wpisuje się w wybrany etap. Dostęp przez wspólne konto stanowiska (rola produkcja)."""
import re
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import render_template, request, jsonify, session, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from models import db, ProdEmployee, ProdRow, ProdSheet, ProdStageColumn
from . import produkcja_bp, excel_io
from .entries import apply_entry, employee_allowed
from .routes import _data, _display
from .staff import BADGE_PREFIX

UTC = timezone.utc
SESSION_KEY = 'prod_emp'
SESSION_HOURS = 12


def station_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_production_station:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _current_employee():
    """Pracownik zeskanowany na tym urządzeniu (ważny SESSION_HOURS godzin)."""
    data = session.get(SESSION_KEY)
    if not data:
        return None
    try:
        at = datetime.fromisoformat(data['at'])
    except (KeyError, ValueError):
        session.pop(SESSION_KEY, None)
        return None
    if datetime.now(UTC) - at > timedelta(hours=SESSION_HOURS):
        session.pop(SESSION_KEY, None)
        return None
    emp = db.session.get(ProdEmployee, data.get('id'))
    if not emp or not emp.is_active:
        session.pop(SESSION_KEY, None)
        return None
    return emp


def _emp_json(emp):
    return {'id': emp.id, 'name': emp.name, 'initials': emp.initials}


def _norm(s):
    return ' '.join(str(s or '').upper().split())


@produkcja_bp.route('/skan')
@login_required
@station_required
def scan_page():
    emp = _current_employee()
    return render_template('produkcja/scan.html', employee=_emp_json(emp) if emp else None,
                           badge_prefix=BADGE_PREFIX)


@produkcja_bp.route('/skan/pracownik', methods=['POST'])
@login_required
@station_required
def scan_employee():
    code = ((request.get_json(silent=True) or {}).get('code') or '').strip()
    token = code[len(BADGE_PREFIX):] if code.startswith(BADGE_PREFIX) else code
    emp = ProdEmployee.query.filter_by(token=token).first() if token else None
    if not emp or not emp.is_active:
        return jsonify({'error': 'Nieznany lub nieaktywny kod pracownika.'}), 404
    session[SESSION_KEY] = {'id': emp.id, 'at': datetime.now(UTC).isoformat()}
    return jsonify({'ok': True, 'employee': _emp_json(emp)})


@produkcja_bp.route('/skan/wyloguj', methods=['POST'])
@login_required
@station_required
def scan_logout():
    session.pop(SESSION_KEY, None)
    return jsonify({'ok': True})


@produkcja_bp.route('/skan/zlecenie', methods=['POST'])
@login_required
@station_required
def scan_order():
    emp = _current_employee()
    if not emp:
        return jsonify({'error': 'Najpierw zeskanuj swoją plakietkę.', 'need_employee': True}), 401
    payload = request.get_json(silent=True) or {}
    zo = (payload.get('o') or '').strip()
    product = _norm(payload.get('p'))
    if not zo:
        return jsonify({'error': 'Kod QR nie zawiera numeru ZO.'}), 400
    rows = (ProdRow.query.filter(func.upper(ProdRow.zo) == zo.upper())
            .order_by(ProdRow.sheet_id, ProdRow.position).limit(60).all())
    if not rows:
        return jsonify({'error': f'Zlecenia „{zo}” nie ma na liście produkcyjnej.'}), 404
    exact = [r for r in rows if product and _norm(excel_io.row_fields(
        _data(r, len(r.sheet.columns)), r.sheet.mapping).get('product')) == product]
    rows = exact or rows
    stages_by_sheet = {}
    for st in ProdStageColumn.query.filter(ProdStageColumn.is_active.is_(True)).order_by(
            ProdStageColumn.col_index).all():
        stages_by_sheet.setdefault(st.sheet_id, []).append(st)
    result = []
    for r in rows[:20]:
        stages = stages_by_sheet.get(r.sheet_id, [])
        if not stages:
            continue
        data = _data(r, len(r.sheet.columns))
        f = excel_io.row_fields(data, r.sheet.mapping)
        result.append({
            'row_id': r.id, 'sheet': r.sheet.name, 'position': r.position,
            'product': _display(f.get('product')), 'client': _display(f.get('client')),
            'qty': _display(f.get('qty')), 'zo': r.zo,
            'stages': [{
                'id': st.id, 'label': st.label, 'value': _display(data[st.col_index]),
                'mine': emp.initials.lower() in [p.strip().lower() for p in re.split(r'[/,+;]', _display(data[st.col_index]))],
                'allowed': employee_allowed(emp, st),
            } for st in stages],
        })
    if not result:
        return jsonify({'error': 'Dla tego zlecenia nie skonfigurowano kolumn etapów.'}), 404
    return jsonify({'ok': True, 'orders': result})


@produkcja_bp.route('/skan/wpis', methods=['POST'])
@login_required
@station_required
def scan_entry():
    emp = _current_employee()
    if not emp:
        return jsonify({'error': 'Najpierw zeskanuj swoją plakietkę.', 'need_employee': True}), 401
    payload = request.get_json(silent=True) or {}
    row = db.session.get(ProdRow, payload.get('row_id') or 0)
    stage = db.session.get(ProdStageColumn, payload.get('stage_id') or 0)
    if not row or not stage or stage.sheet_id != row.sheet_id or not stage.is_active:
        return jsonify({'error': 'Nieprawidłowe zlecenie lub etap.'}), 400
    res = apply_entry(row, stage, emp, user_id=current_user.id, confirm=bool(payload.get('confirm')))
    code = 403 if res['status'] == 'forbidden' else 200
    return jsonify({'ok': res['status'] in ('ok', 'already'), **res}), code
