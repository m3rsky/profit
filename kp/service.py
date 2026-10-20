"""Logika zleceń produkcyjnych: dopasowanie szablonu produktu, tworzenie zlecenia, przeliczanie ilości."""
import re

from models import (db, Movement, ProductTemplate, ScrapRecord, StageControl, StageControlResult, StockPosition,
                    WorkInProgress, WorkOrder, WorkOrderComponent, WorkOrderComponentStage,
                    WorkOrderControl)

MAX_QTY = 9999


def norm(text):
    """Porównywanie bez wielkości liter i nadmiarowych spacji."""
    return ' '.join((text or '').upper().split())


def tokens(text):
    """Tokeny nazwy (bez numerów wersji v1, v2): ten sam algorytm co szablony kontroli."""
    text = re.sub(r'\bv\d+\b', '', text or '', flags=re.IGNORECASE)
    return {p.lower() for p in re.split(r'[^a-zA-Z0-9]+', text) if p}


def find_template(product_name):
    """Aktywny szablon produktu z elementami najlepiej pasujący do nazwy produktu.
    Wygrywa szablon z największą liczbą pasujących tokenów, nieużyte tokeny szablonu mocno
    obniżają wynik (PSH INOX wygrywa z PSH dla produktu INOX, ale PSH wygrywa dla zwykłego PSH)."""
    product_tokens = tokens(product_name)
    if not product_tokens:
        return None
    best, best_score = None, None
    for tpl in ProductTemplate.query.filter_by(is_active=True).all():
        if not tpl.components:
            continue
        tpl_tokens = tokens(tpl.name)
        common = tpl_tokens & product_tokens
        if not common:
            continue
        unmatched = len(tpl_tokens) - len(common)
        score = len(common) * 100 + max(len(t) for t in common) - unmatched * 1000
        if best_score is None or score > best_score:
            best, best_score = tpl, score
    return best


def find_order(zo, product_name):
    """Zlecenie dla pary ZO + produkt (bez wielkości liter i nadmiarowych spacji) albo None."""
    wanted_zo, wanted_product = norm(zo), norm(product_name)
    for o in WorkOrder.query.filter_by(zo_number=zo).order_by(WorkOrder.id.desc()).all():
        if norm(o.product_name) == wanted_product:
            return o
    # ZO zapisane inną wielkością liter
    for o in WorkOrder.query.order_by(WorkOrder.id.desc()).all():
        if norm(o.zo_number) == wanted_zo and norm(o.product_name) == wanted_product:
            return o
    return None


def parse_per_unit(template, raw):
    """Ilości elementów na sztukę produktu podane przy tworzeniu zlecenia: {id elementu szablonu: n}.
    Ilość jest zawsze podawana (0 oznacza, że element nie dotyczy tego zlecenia). Zwraca
    (słownik, komunikat błędu)."""
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for comp in template.components:
        value = raw.get(str(comp.id), raw.get(comp.id))
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None, f'Podaj ilość na sztukę dla elementu „{comp.name}” (0, jeśli nie dotyczy).'
        if n < 0 or n > MAX_QTY:
            return None, f'Ilość dla elementu „{comp.name}” musi być liczbą od 0 do {MAX_QTY}.'
        result[comp.id] = n
    if not any(result.values()):
        return None, 'Zlecenie musi mieć co najmniej jeden element z ilością większą od zera.'
    return result, None


def create_order(zo, product_name, client, quantity, user_id, template, per_unit):
    """Tworzy zlecenie z elementami i trasami skopiowanymi z szablonu. `per_unit` to ilości
    elementów na sztukę produktu {id elementu szablonu: n}, elementy z 0 są pomijane. Bez commitu."""
    order = WorkOrder(zo_number=zo, product_name=product_name, client=client or None,
                      quantity=quantity, template_id=template.id, created_by_id=user_id)
    db.session.add(order)
    db.session.flush()
    position = 0
    for comp in template.components:
        n = per_unit.get(comp.id, 0)
        if n <= 0:
            continue
        position += 1
        oc = WorkOrderComponent(order_id=order.id, name=comp.name, per_unit=n,
                                required_qty=n * quantity, position=position)
        db.session.add(oc)
        db.session.flush()
        for stage_id in comp.stage_ids:
            db.session.add(WorkOrderComponentStage(component_id=oc.id, stage_id=stage_id))
    for tc in template.control_plan:
        db.session.add(WorkOrderControl(order_id=order.id, stage_id=tc.stage_id))
    return order


def set_quantity(order, quantity):
    """Zmiana ilości zlecenia: wymagane ilości elementów przeliczane na q razy ilość na sztukę."""
    order.quantity = quantity
    for comp in order.components:
        comp.required_qty = comp.per_unit * quantity
    product = order.product_component
    if product is not None:
        product.required_qty = quantity


def delete_impact(order):
    """Co zniknie wraz ze zleceniem: liczba ruchów oraz sztuk leżących na buforach i w realizacji."""
    comp_ids = [c.id for c in order.all_components]
    stock_qty = wip_qty = 0
    if comp_ids:
        stock_qty = int(db.session.query(db.func.coalesce(db.func.sum(StockPosition.quantity), 0))
                        .filter(StockPosition.component_id.in_(comp_ids)).scalar() or 0)
        wip_qty = int(db.session.query(db.func.coalesce(db.func.sum(WorkInProgress.quantity), 0))
                      .filter(WorkInProgress.component_id.in_(comp_ids)).scalar() or 0)
    return {'movements': Movement.query.filter_by(order_id=order.id).count(),
            'stock': stock_qty, 'wip': wip_qty,
            'controls': StageControl.query.filter_by(order_id=order.id).count()}


def delete_order(order):
    """Usuwa zlecenie razem z elementami, trasami, ilościami na buforach, pozycjami w realizacji i
    historią ruchów. Bufory, pracownicy i szablony zostają. Bez commitu."""
    comp_ids = [c.id for c in order.all_components]
    control_ids = [c.id for c in StageControl.query.filter_by(order_id=order.id).all()]
    if control_ids:
        StageControlResult.query.filter(StageControlResult.control_id.in_(control_ids)).delete(
            synchronize_session=False)
        StageControl.query.filter(StageControl.id.in_(control_ids)).delete(synchronize_session=False)
    ScrapRecord.query.filter_by(order_id=order.id).delete(synchronize_session=False)
    Movement.query.filter_by(order_id=order.id).delete(synchronize_session=False)
    if comp_ids:
        StockPosition.query.filter(StockPosition.component_id.in_(comp_ids)).delete(synchronize_session=False)
        WorkInProgress.query.filter(WorkInProgress.component_id.in_(comp_ids)).delete(synchronize_session=False)
    db.session.delete(order)   # elementy i ich trasy usuwa kaskada
