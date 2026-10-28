"""Logika zleceń demo: tworzenie trasy, meldunek operacji ze wstrzymanym RW, statusy, oś czasu, usuwanie.

Zasada demo: każda operacja z flagą KJ (K1) dostaje wszystkie aktywne materiały z flagą KJ (K2). Po meldunku
takiej operacji RW na te materiały jest wstrzymane do zakończenia kontroli. Operacja bez KJ nie wstrzymuje RW."""
from datetime import datetime, timezone

from sqlalchemy import func

from models import db
from .models import (PkjOperation, PkjMaterial, PkjEmployee, PkjOrder, PkjOrderOperation, PkjOrderMaterial,
                     PkjInspection, PkjInspectionMaterial, PkjResult, PkjPhoto, PkjReturn, PkjReturnOperation,
                     PkjEvent)
from .service import remove_upload
from . import clock

UTC = timezone.utc

ORDER_STATUS = ('nowe', 'w_produkcji', 'do_kontroli', 'zakonczone', 'zamkniete')


def _now():
    return clock.now()


def make_qr_key(product, client, number):
    """Klucz kodu QR zlecenia w formacie używanym w profit: produkt|klient|ZO."""
    return f'{product or ""}|{client or ""}|{number}'


def add_event(order_id, type_, text, actor=None, when=None, ref_type=None, ref_id=None):
    db.session.add(PkjEvent(order_id=order_id, type=type_, text=text[:512], actor=actor,
                            ts=when or _now(), ref_type=ref_type, ref_id=ref_id))


def operations_of(order_id):
    return (PkjOrderOperation.query.filter_by(order_id=order_id)
            .order_by(PkjOrderOperation.position, PkjOrderOperation.id).all())


def materials_of(order_id):
    return (PkjOrderMaterial.query.filter_by(order_id=order_id)
            .order_by(PkjOrderMaterial.order_operation_id, PkjOrderMaterial.id).all())


def create_order(number, product, client, quantity, operation_ids=None, actor=None, scenario=False,
                 order_type='ZP', when=None):
    """Zakłada zlecenie z trasą (wybrane lub wszystkie aktywne operacje) i materiałami KJ. Rzuca ValueError."""
    number = (number or '').strip()[:64]
    product = (product or '').strip()[:256]
    if not number or not product:
        raise ValueError('Numer zlecenia i produkt są wymagane.')
    if PkjOrder.query.filter(func.lower(PkjOrder.number) == number.lower()).first():
        raise ValueError(f'Zlecenie {number} już istnieje.')
    q = PkjOperation.query.filter_by(is_active=True)
    if operation_ids:
        q = q.filter(PkjOperation.id.in_(operation_ids))
    ops = q.order_by(PkjOperation.order, PkjOperation.id).all()
    if not ops:
        raise ValueError('Trasa zlecenia musi mieć co najmniej jedną aktywną operację.')
    try:
        qty = max(1, min(int(quantity or 1), 9999))
    except (TypeError, ValueError):
        qty = 1
    client = (client or '').strip()[:256] or None
    order = PkjOrder(number=number, type=order_type, product=product, client=client, quantity=qty,
                     status='nowe', qr_key=make_qr_key(product, client, number), is_demo_scenario=scenario,
                     created_at=when or _now())
    db.session.add(order)
    db.session.flush()
    kj_materials = (PkjMaterial.query.filter_by(kj_flag=True, is_active=True)
                    .order_by(PkjMaterial.order, PkjMaterial.id).all())
    for pos, op in enumerate(ops, 1):
        oop = PkjOrderOperation(order_id=order.id, operation_id=op.id, operation_name=op.name, position=pos,
                                kj_required=op.kj_required)
        db.session.add(oop)
        db.session.flush()
        if op.kj_required:
            for m in kj_materials:
                db.session.add(PkjOrderMaterial(order_id=order.id, order_operation_id=oop.id, material_id=m.id,
                                                material_name=m.name, quantity=qty))
    add_event(order.id, 'utworzenie', f'Utworzono zlecenie {number}: {product}'
              + (f', klient {client}' if client else ''), actor=actor, when=when)
    return order


def blocking_reason(oop, ops=None):
    """Powód, dla którego operacji nie można teraz zameldować (None, gdy można)."""
    if oop.status != 'oczekuje':
        return 'Operacja jest już zameldowana.'
    ops = ops if ops is not None else operations_of(oop.order_id)
    for other in ops:
        if other.position < oop.position and other.status != 'zakonczona':
            if other.status == 'do_kontroli':
                return f'Operacja {other.operation_name} czeka na kontrolę KJ.'
            return f'Najpierw trzeba zameldować operację {other.operation_name}.'
    return None


def report_operation(oop, employee, actor=None, when=None):
    """Meldunek operacji. Z flagą KJ: status "do kontroli" i RW wstrzymane na materiały KJ. Rzuca ValueError."""
    if employee is None or not employee.is_active:
        raise ValueError('Wybierz aktywnego pracownika.')
    reason = blocking_reason(oop)
    if reason:
        raise ValueError(reason)
    order = db.session.get(PkjOrder, oop.order_id)
    repair = order.type == 'ZN'
    when = when or _now()
    oop.reported_at = when
    oop.employee_id, oop.employee_name = employee.id, employee.name
    mats = PkjOrderMaterial.query.filter_by(order_operation_id=oop.id).all()
    add_event(order.id, 'meldunek', f'Zameldowano operację {"naprawczą " if repair else ""}{oop.operation_name}'
              f' (wykonawca: {employee.name})',
              actor=actor, when=when, ref_type='operation', ref_id=oop.id)
    if oop.kj_required and mats:
        oop.status = 'do_kontroli'
        for m in mats:
            m.rw_status, m.element_status = 'wstrzymane', 'oczekuje'
        add_event(order.id, 'rw_wstrzymane',
                  'RW wstrzymane do zakończenia kontroli KJ: ' + ', '.join(m.material_name for m in mats),
                  actor=actor, when=when, ref_type='operation', ref_id=oop.id)
    else:
        oop.status = 'zakonczona'
        if not repair:
            add_event(order.id, 'rw_wydane', 'Dokument RW wydany (symulacja), operacja bez kontroli KJ',
                      actor=actor, when=when, ref_type='operation', ref_id=oop.id)
    refresh_status(order)
    if repair and order.status == 'zakonczone':
        from .repairs import complete_repair
        complete_repair(order, actor)
    return oop


def refresh_status(order):
    if order.status == 'zamkniete':
        return order.status
    ops = operations_of(order.id)
    if ops and all(o.status == 'zakonczona' for o in ops):
        order.status = 'zakonczone'
    elif any(o.status == 'do_kontroli' for o in ops):
        order.status = 'do_kontroli'
    elif any(o.status != 'oczekuje' for o in ops):
        order.status = 'w_produkcji'
    else:
        order.status = 'nowe'
    return order.status


def summaries(orders):
    """Podsumowania do listy: {order_id: {done, total, next, held}} jednym zestawem zapytań."""
    ids = [o.id for o in orders]
    out = {i: {'done': 0, 'total': 0, 'next': None, 'held': 0} for i in ids}
    if not ids:
        return out
    ops = (PkjOrderOperation.query.filter(PkjOrderOperation.order_id.in_(ids))
           .order_by(PkjOrderOperation.order_id, PkjOrderOperation.position).all())
    for op in ops:
        s = out[op.order_id]
        s['total'] += 1
        if op.status == 'zakonczona':
            s['done'] += 1
        elif s['next'] is None:
            s['next'] = op.operation_name
    for oid, cnt in (db.session.query(PkjOrderMaterial.order_id, func.count(PkjOrderMaterial.id))
                     .filter(PkjOrderMaterial.order_id.in_(ids), PkjOrderMaterial.rw_status == 'wstrzymane')
                     .group_by(PkjOrderMaterial.order_id).all()):
        out[oid]['held'] = cnt
    return out


def delete_orders(order_ids):
    """Usuwa zlecenia razem z dziećmi (ZN), kontrolami, zawrotami, zdjęciami i osią czasu. Zwraca liczbę usuniętych."""
    ids = set(order_ids)
    while True:
        children = {o.id for o in PkjOrder.query.filter(PkjOrder.parent_id.in_(ids)).all()} - ids
        if not children:
            break
        ids |= children
    if not ids:
        return 0
    insp_ids = [i.id for i in PkjInspection.query.filter(PkjInspection.order_id.in_(ids)).all()]
    mat_ids = [m.id for m in PkjOrderMaterial.query.filter(PkjOrderMaterial.order_id.in_(ids)).all()]
    res_ids = [r.id for r in PkjResult.query.filter(PkjResult.inspection_id.in_(insp_ids)).all()] if insp_ids else []
    if res_ids:
        for photo in PkjPhoto.query.filter(PkjPhoto.result_id.in_(res_ids)).all():
            remove_upload(photo.filename)
        PkjPhoto.query.filter(PkjPhoto.result_id.in_(res_ids)).delete(synchronize_session=False)
    ret_ids = [r.id for r in PkjReturn.query.filter(
        (PkjReturn.inspection_id.in_(insp_ids)) | (PkjReturn.order_material_id.in_(mat_ids))).all()] \
        if (insp_ids or mat_ids) else []
    if ret_ids:
        PkjReturnOperation.query.filter(PkjReturnOperation.return_id.in_(ret_ids)).delete(synchronize_session=False)
        PkjReturn.query.filter(PkjReturn.id.in_(ret_ids)).delete(synchronize_session=False)
    if insp_ids:
        PkjResult.query.filter(PkjResult.inspection_id.in_(insp_ids)).delete(synchronize_session=False)
        PkjInspectionMaterial.query.filter(PkjInspectionMaterial.inspection_id.in_(insp_ids)).delete(
            synchronize_session=False)
        PkjInspection.query.filter(PkjInspection.id.in_(insp_ids)).delete(synchronize_session=False)
    for model in (PkjEvent, PkjOrderMaterial, PkjOrderOperation):
        model.query.filter(model.order_id.in_(ids)).delete(synchronize_session=False)
    PkjOrder.query.filter(PkjOrder.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return len(ids)


def active_employees():
    return PkjEmployee.query.filter_by(is_active=True).order_by(PkjEmployee.department, PkjEmployee.name).all()
