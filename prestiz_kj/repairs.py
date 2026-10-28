"""Zawroty i zlecenia naprawcze (ZN): zgłoszenie zawrotu dla materiału NOK, decyzja poprawka albo złom,
opcjonalne zatwierdzenie przez kierownika, tworzenie ZN z operacji naprawczych, zakończenie naprawy (element
do ponownej kontroli) i zamknięcie po kontroli sprawdzającej.

Operacje ZN to operacje naprawcze bez własnego KJ. Kontrola sprawdzająca dotyczy oryginalnego materiału na ZP."""
from sqlalchemy import func

from models import db
from .models import (PkjInspection, PkjInspectionMaterial, PkjOperation, PkjOrder, PkjOrderMaterial,
                     PkjOrderOperation, PkjReason, PkjResult, PkjReturn, PkjReturnOperation)
from .orders import add_event, make_qr_key
from .service import get_setting

OPEN_STATUSES = ('do_zatwierdzenia', 'w_naprawie', 'do_ponownej_kontroli')
DECISIONS = {'poprawka': 'Poprawka', 'zlom': 'Złom'}
RETURN_STATUS_LABELS = {'do_zatwierdzenia': 'Do zatwierdzenia', 'w_naprawie': 'W naprawie',
                        'do_ponownej_kontroli': 'Do ponownej kontroli', 'zamkniety': 'Zamknięty'}


def open_return_for(om_id):
    return (PkjReturn.query.filter(PkjReturn.order_material_id == om_id, PkjReturn.status.in_(OPEN_STATUSES))
            .order_by(PkjReturn.id.desc()).first())


def needs_return(order_id):
    """Materiały zawrócone przez kontrolę, dla których nie zgłoszono jeszcze zawrotu."""
    mats = (PkjOrderMaterial.query.filter_by(order_id=order_id, element_status='zawrocony')
            .order_by(PkjOrderMaterial.id).all())
    return [m for m in mats if open_return_for(m.id) is None]


def failed_points(om):
    """Punkty NOK ostatniej niezgodnej kontroli materiału (wspólne i własne), do podglądu przy zawrocie."""
    pim = (PkjInspectionMaterial.query.filter_by(order_material_id=om.id, verdict='NOK')
           .order_by(PkjInspectionMaterial.id.desc()).first())
    if pim is None:
        return None, []
    rows = (PkjResult.query.filter(PkjResult.inspection_id == pim.inspection_id, PkjResult.result == 'NOK')
            .filter((PkjResult.inspection_material_id == pim.id) | (PkjResult.inspection_material_id.is_(None)))
            .order_by(PkjResult.position).all())
    return pim, rows


def _operation_plan(om, decision, target_oop_id, extra_ids):
    """Operacje ZN jako lista (operation_id, nazwa, ręcznie). Poprawka: wskazana operacja plus dodatkowe.
    Złom: cała trasa do operacji kontrolowanej włącznie (nowy element od początku)."""
    route = (PkjOrderOperation.query.filter_by(order_id=om.order_id)
             .order_by(PkjOrderOperation.position).all())
    inspected = next((o for o in route if o.id == om.order_operation_id), None)
    if inspected is None:
        raise ValueError('Nie znaleziono operacji kontrolowanej tego materiału.')
    if decision == 'zlom':
        return [(o.operation_id, o.operation_name, False) for o in route if o.position <= inspected.position]
    target = next((o for o in route if o.id == target_oop_id), None)
    if target is None:
        raise ValueError('Wybierz operację, na którą element ma wrócić.')
    picked = {(target.operation_id, target.operation_name): False}
    ops = {o.id: o for o in PkjOperation.query.filter(PkjOperation.id.in_(extra_ids or [0])).all()}
    for oid in extra_ids or []:
        op = ops.get(oid)
        if op is None or not op.is_active:
            raise ValueError('Wybrana dodatkowa operacja nie istnieje lub jest nieaktywna.')
        if (op.id, op.name) not in picked:
            picked[(op.id, op.name)] = True
    order_of = {o.id: (o.order, o.id) for o in PkjOperation.query.all()}
    plan = [(oid, name, manual) for (oid, name), manual in picked.items()]
    plan.sort(key=lambda p: order_of.get(p[0], (10 ** 6, 0)))
    return plan


def create_return(om, reason_id, description, decision, target_oop_id=None, extra_ids=None, actor=None):
    """Zgłasza zawrót materiału NOK. Przy trybie "kierownik" czeka na zatwierdzenie, inaczej od razu powstaje ZN."""
    if om.element_status != 'zawrocony':
        raise ValueError('Ten materiał nie wymaga zawrotu.')
    if open_return_for(om.id) is not None:
        raise ValueError('Dla tego materiału zawrót został już zgłoszony.')
    if decision not in DECISIONS:
        raise ValueError('Wybierz decyzję: poprawka albo złom.')
    reason = db.session.get(PkjReason, reason_id) if reason_id else None
    if reason is None or not reason.is_active:
        raise ValueError('Wybierz przyczynę niezgodności.')
    plan = _operation_plan(om, decision, target_oop_id, extra_ids)
    pim, rows = failed_points(om)
    ret = PkjReturn(inspection_id=pim.inspection_id if pim else None, order_material_id=om.id,
                    result_id=rows[0].id if rows else None, reason_id=reason.id, reason_name=reason.name,
                    description=(description or '').strip()[:2000] or None, decision=decision,
                    status='do_zatwierdzenia', created_by=actor)
    db.session.add(ret)
    db.session.flush()
    for pos, (op_id, name, manual) in enumerate(plan, 1):
        db.session.add(PkjReturnOperation(return_id=ret.id, operation_id=op_id, operation_name=name,
                                          position=pos, added_manually=manual))
    add_event(om.order_id, 'zawrot',
              f'Zgłoszono zawrót: {om.material_name}, przyczyna: {reason.name}, decyzja: {DECISIONS[decision].lower()}'
              f'. Operacje: ' + ', '.join(p[1] for p in plan), actor=actor, ref_type='return', ref_id=ret.id)
    if get_setting('approval_mode') == 'manager':
        add_event(om.order_id, 'zawrot_czeka', 'Zawrót czeka na zatwierdzenie kierownika', actor=actor,
                  ref_type='return', ref_id=ret.id)
    else:
        issue_repair_order(ret, actor)
    return ret


def approve_return(ret, actor=None):
    if ret.status != 'do_zatwierdzenia':
        raise ValueError('Ten zawrót nie czeka na zatwierdzenie.')
    add_event(db.session.get(PkjOrderMaterial, ret.order_material_id).order_id, 'zawrot_zatwierdzony',
              'Zawrót zatwierdzony przez kierownika', actor=actor, ref_type='return', ref_id=ret.id)
    issue_repair_order(ret, actor)


def issue_repair_order(ret, actor=None):
    """Tworzy ZN z operacji zawrotu i ustawia status materiału (poprawka: w naprawie, złom: rozchód materiału)."""
    om = db.session.get(PkjOrderMaterial, ret.order_material_id)
    zp = db.session.get(PkjOrder, om.order_id)
    n = PkjOrder.query.filter_by(parent_id=zp.id, type='ZN').count() + 1
    number = f'ZN-{zp.number}/{n}'
    while PkjOrder.query.filter(func.lower(PkjOrder.number) == number.lower()).first():
        n += 1
        number = f'ZN-{zp.number}/{n}'
    zn = PkjOrder(number=number, type='ZN', parent_id=zp.id, source_return_id=ret.id, product=zp.product,
                  client=zp.client, quantity=1, status='nowe', qr_key=make_qr_key(zp.product, zp.client, number),
                  is_demo=zp.is_demo)
    db.session.add(zn)
    db.session.flush()
    plan = PkjReturnOperation.query.filter_by(return_id=ret.id).order_by(PkjReturnOperation.position).all()
    for pos, ro in enumerate(plan, 1):
        db.session.add(PkjOrderOperation(order_id=zn.id, operation_id=ro.operation_id,
                                         operation_name=ro.operation_name, position=pos, kj_required=False,
                                         added_manually=ro.added_manually, repair_for_return_id=ret.id))
    ret.repair_order_id, ret.status = zn.id, 'w_naprawie'
    kind = 'zlecenie na nowy element' if ret.decision == 'zlom' else 'zlecenie naprawcze'
    add_event(zn.id, 'utworzenie', f'Utworzono {kind} {number} do {zp.number}: {om.material_name}, '
              f'przyczyna: {ret.reason_name}', actor=actor, ref_type='return', ref_id=ret.id)
    add_event(zp.id, 'zn_utworzone', f'Utworzono {kind} {number} dla materiału {om.material_name}', actor=actor,
              ref_type='order', ref_id=zn.id)
    if ret.decision == 'zlom':
        om.element_status, om.rw_status = 'zlom', 'anulowane'
        text = f'Rozchód materiału (symulacja): {om.material_name} zezłomowany'
        if get_setting('scrap_mode') == 'with_cost':
            text += f', koszt materiału {get_setting("scrap_material_cost")} PLN'
        add_event(zp.id, 'rozchod', text, actor=actor, ref_type='return', ref_id=ret.id)
    else:
        om.element_status = 'w_naprawie'
    return zn


def complete_repair(zn, actor=None):
    """Wywoływane po zameldowaniu ostatniej operacji ZN: element wraca do kontroli sprawdzającej."""
    ret = db.session.get(PkjReturn, zn.source_return_id) if zn.source_return_id else None
    if ret is None or ret.status != 'w_naprawie':
        return None
    om = db.session.get(PkjOrderMaterial, ret.order_material_id)
    om.element_status = 'do_ponownej_kontroli'
    if ret.decision == 'zlom':
        om.rw_status = 'wstrzymane'          # nowy element czeka na RW do wyniku kontroli
    ret.status = 'do_ponownej_kontroli'
    add_event(om.order_id, 'do_ponownej_kontroli',
              f'{zn.number} zakończone: {om.material_name} czeka na kontrolę sprawdzającą', actor=actor,
              ref_type='order', ref_id=zn.id)
    return ret


def close_after_recheck(om, ok, actor=None):
    """Po zakończeniu kontroli sprawdzającej zamyka zawrót materiału. Zgodny wynik zamyka też ZN."""
    ret = PkjReturn.query.filter_by(order_material_id=om.id, status='do_ponownej_kontroli').first()
    if ret is None:
        return None
    ret.status = 'zamkniety'
    zn = db.session.get(PkjOrder, ret.repair_order_id) if ret.repair_order_id else None
    if zn is not None:
        if ok:
            zn.status = 'zamkniete'
        add_event(zn.id, 'zn_zamkniete' if ok else 'kontrola',
                  'Kontrola sprawdzająca zgodna, zlecenie zamknięte' if ok
                  else 'Kontrola sprawdzająca niezgodna, wymagany nowy zawrót', actor=actor)
    return ret


def returns_of_order(order_id):
    """Zawroty materiałów zlecenia (ZP) z numerem ZN."""
    mat_ids = [m.id for m in PkjOrderMaterial.query.filter_by(order_id=order_id).all()]
    if not mat_ids:
        return []
    return (PkjReturn.query.filter(PkjReturn.order_material_id.in_(mat_ids)).order_by(PkjReturn.id).all())
