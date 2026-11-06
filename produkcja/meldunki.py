"""Kontrola międzyetapowa, Stanowisko: zgłoszenie wykonania etapu zlecenia (ilość zgodna i niezgodna).

Pracownik loguje się plakietką działu (`PSH-DEP:`), skanuje QR zlecenia (pola p, c, o, q)
i zgłasza wykonanie. Pierwszy skan tworzy zlecenie w systemie (`ControlOrder`).
Niezgodność nie tworzy raportu QAR, tylko alert "Niezgodność do weryfikacji" i rekord
`Nonconformity`. Plan: PLAN_KONTROLA_MIEDZYETAPOWA.md."""
from datetime import datetime, timezone

from flask import request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func

from models import (db, Alert, User, ControlOrder, ProductionStage, Machine, DefectCause,
                    StageReport, Nonconformity, ProdRow, ProdStageColumn, ProdEmployee,
                    roles_matching)
from . import produkcja_bp, excel_io
from .routes import _data
from .scan import station_required, current_dep_employee, _norm

UTC = timezone.utc
MAX_QTY = 100000
DEFAULT_CAUSES = ['Wymiar', 'Spaw', 'Powierzchnia, wygląd', 'Brak elementu', 'Pomyłka operatora', 'Inne']


def seed_defect_causes():
    """Domyślna lista przyczyn wad, tylko gdy słownik jest pusty (potem edytuje ją Ustawienia produkcji)."""
    if DefectCause.query.count():
        return
    for i, name in enumerate(DEFAULT_CAUSES, start=1):
        db.session.add(DefectCause(name=name, order=i))
    db.session.commit()


def find_order(zo, product):
    """Zlecenie systemu dla pary ZO + produkt (bez wielkości liter i nadmiarowych spacji) albo None."""
    wanted = _norm(product)
    for o in ControlOrder.query.filter(func.upper(ControlOrder.zo_number) == zo.upper()) \
            .order_by(ControlOrder.id.desc()).all():
        if _norm(o.product_name) == wanted:
            return o
    return None


def stage_totals(order):
    """Sumy zgłoszeń wykonania per etap: {stage_id: (ok, ng)}. Poprawki liczą się do zgodnych."""
    totals = {}
    for r in order.stage_reports:
        ok, ng = totals.get(r.stage_id, (0, 0))
        totals[r.stage_id] = (ok + (r.qty_ok or 0), ng + (r.qty_ng or 0))
    return totals


def _order_json(order, is_new):
    totals = stage_totals(order)
    stages = ProductionStage.query.filter_by(is_active=True).order_by(
        ProductionStage.order, ProductionStage.id).all()
    return {
        'id': order.id, 'zo': order.zo_number, 'product': order.product_name or '',
        'client': order.client or '', 'qty': order.quantity, 'is_new': is_new,
        'stages': [{
            'id': s.id, 'name': s.name, 'department_id': s.department_id,
            'ok': totals.get(s.id, (0, 0))[0], 'ng': totals.get(s.id, (0, 0))[1],
            'machines': [{'id': m.id, 'name': m.name} for m in s.machines if m.is_active],
        } for s in stages],
        'open_nonconformities': order.nonconformities.filter_by(status='do_weryfikacji').count(),
    }


def _int(value, default=0):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n


@produkcja_bp.route('/skan/meldunek/zlecenie', methods=['POST'])
@login_required
@station_required
def report_order():
    """Skan QR zlecenia pracownika działu: znajduje albo tworzy zlecenie w systemie."""
    emp = current_dep_employee()
    if not emp:
        return jsonify({'error': 'Najpierw zeskanuj swoją plakietkę pracownika działu.',
                        'need_employee': True}), 401
    p = request.get_json(silent=True) or {}
    zo = str(p.get('o') or '').strip()
    product = ' '.join(str(p.get('p') or '').split())
    if not zo:
        return jsonify({'error': 'Kod QR nie zawiera numeru ZO.'}), 400
    order = find_order(zo, product)
    is_new = order is None
    if is_new:
        order = ControlOrder(
            zo_number=zo, product_name=product or None,
            client=' '.join(str(p.get('c') or '').split()) or None,
            quantity=max(1, min(MAX_QTY, _int(p.get('q'), 1))), created_by_id=current_user.id)
        db.session.add(order)
        db.session.commit()
    seed_defect_causes()
    out = _order_json(order, is_new)
    out['causes'] = [{'id': c.id, 'name': c.name} for c in
                     DefectCause.query.filter_by(is_active=True).order_by(DefectCause.order, DefectCause.id)]
    return jsonify({'ok': True, 'order': out})


def _notify_nonconformity(order, stage, emp, report, qty_ng):
    text = (f'Niezgodność do weryfikacji: ZO {order.zo_number} {order.product_name or ""}'.rstrip()
            + f', zgłoszono {qty_ng} szt. niezgodne na etapie {stage.name} ({emp.name}). '
              'Wymagana weryfikacja kontrolera.')
    for u in User.query.filter(User.role.in_(roles_matching('kontroler', 'administrator'))).all():
        db.session.add(Alert(recipient_id=u.id, message=text[:512], alert_type='urgent'))


def _sync_to_list(order, stage, emp):
    """Najlepsza próba wpisania inicjałów w Liście produkcyjnej (tylko gdy jednoznaczne:
    etap ma dział, w arkuszu jest dokładnie jedna kolumna tego działu i pracownik o tym samym imieniu).
    Zwraca komunikat albo ''."""
    from .entries import apply_entry
    if not stage.department_id:
        return ''
    prod_emp = ProdEmployee.query.filter(func.lower(ProdEmployee.name) == emp.name.lower(),
                                         ProdEmployee.is_active.is_(True)).first()
    if not prod_emp:
        return ''
    for row in ProdRow.query.filter(func.upper(ProdRow.zo) == order.zo_number.upper()).limit(20).all():
        f = excel_io.row_fields(_data(row, len(row.sheet.columns)), row.sheet.mapping)
        if _norm(f.get('product')) != _norm(order.product_name):
            continue
        cols = ProdStageColumn.query.filter_by(sheet_id=row.sheet_id, is_active=True,
                                               department_id=stage.department_id).all()
        if len(cols) != 1:
            continue
        res = apply_entry(row, cols[0], prod_emp, user_id=current_user.id, confirm=False)
        if res['status'] == 'ok':
            return f'Wpisano {prod_emp.initials} w Liście produkcyjnej ({cols[0].label}).'
    return ''


@produkcja_bp.route('/skan/meldunek', methods=['POST'])
@login_required
@station_required
def report_submit():
    emp = current_dep_employee()
    if not emp:
        return jsonify({'error': 'Najpierw zeskanuj swoją plakietkę pracownika działu.',
                        'need_employee': True}), 401
    p = request.get_json(silent=True) or {}
    order = db.session.get(ControlOrder, _int(p.get('order_id')))
    stage = db.session.get(ProductionStage, _int(p.get('stage_id')))
    if not order or not stage or not stage.is_active:
        return jsonify({'error': 'Nieprawidłowe zlecenie lub etap.'}), 400
    qty_ok, qty_ng = _int(p.get('qty_ok')), _int(p.get('qty_ng'))
    if qty_ok < 0 or qty_ng < 0 or qty_ok > MAX_QTY or qty_ng > MAX_QTY:
        return jsonify({'error': 'Ilość musi być liczbą od 0 do 100000.'}), 400
    if qty_ok + qty_ng == 0:
        return jsonify({'error': 'Podaj ilość zgodną lub niezgodną.'}), 400

    cause = None
    if qty_ng:
        cause = db.session.get(DefectCause, _int(p.get('cause_id')))
        if not cause or not cause.is_active:
            return jsonify({'error': 'Przy ilości niezgodnej wybierz przyczynę.'}), 400
    machine = None
    if p.get('machine_id'):
        machine = db.session.get(Machine, _int(p.get('machine_id')))
        if not machine or machine.stage_id != stage.id:
            return jsonify({'error': 'Ta maszyna nie należy do wybranego etapu.'}), 400

    done_ok, done_ng = stage_totals(order).get(stage.id, (0, 0))
    if order.quantity and done_ok + done_ng + qty_ok + qty_ng > order.quantity and not p.get('confirm'):
        return jsonify({
            'ok': False, 'status': 'confirm',
            'message': (f'Razem zgłoszono by {done_ok + done_ng + qty_ok + qty_ng} szt. na etapie '
                        f'{stage.name}, a zlecenie ma {order.quantity} szt. Zgłosić mimo to?')})

    dept = emp.department
    report = StageReport(
        control_order_id=order.id, stage_id=stage.id, stage_name=stage.name,
        employee_id=emp.id, employee_name=emp.name, department_name=dept.name if dept else None,
        user_id=current_user.id, machine_id=machine.id if machine else None,
        qty_ok=qty_ok, qty_ng=qty_ng, kind='wykonanie')
    db.session.add(report)
    db.session.flush()
    if qty_ng:
        db.session.add(Nonconformity(
            control_order_id=order.id, report_id=report.id, qty=qty_ng, stage_id=stage.id,
            stage_name=stage.name, employee_id=emp.id, employee_name=emp.name,
            department_name=dept.name if dept else None, cause_id=cause.id, cause_name=cause.name,
            note=(str(p.get('note') or '').strip() or None)))
        _notify_nonconformity(order, stage, emp, report, qty_ng)
    db.session.commit()

    note = ''
    try:
        note = _sync_to_list(order, stage, emp)
    except Exception:   # wpis w Liście produkcyjnej jest dodatkiem, nie może blokować zgłoszenia
        db.session.rollback()
    msg = f'Zgłoszono: {qty_ok} zgodnych, {qty_ng} niezgodnych ({stage.name}).'
    if qty_ng:
        msg += ' Kontroler dostał alert "Niezgodność do weryfikacji".'
    return jsonify({'ok': True, 'status': 'ok', 'message': (msg + ' ' + note).strip(),
                    'order': _order_json(order, False)})
