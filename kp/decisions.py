"""Decyzje o ilości wstrzymanej po kontroli (zwolnienie, zwrot do poprawki, złomowanie) oraz skierowanie
ilości na kontrolę doraźną (etap 5b planu).

Decyzje podejmuje kontroler albo admin. Każda zapisuje ruch w historii (kto, kiedy, uwaga). Zwolnienie i zwrot
nie są wycofywalne jak zwykłe ruchy, bo ilość mogła już pójść dalej."""
from datetime import datetime, timezone

from models import (db, Movement, ProductionStage, ScrapRecord, StageControl, StockPosition)
from . import controls, stock

UTC = timezone.utc


def _now():
    return datetime.now(UTC)


def held_positions():
    """Ilości wstrzymane (NG) czekające na decyzję, najstarsze pierwsze."""
    return (StockPosition.query.filter(StockPosition.status == 'wstrzymane', StockPosition.quantity > 0)
            .order_by(StockPosition.updated_at, StockPosition.id).all())


def held_count():
    return StockPosition.query.filter(StockPosition.status == 'wstrzymane', StockPosition.quantity > 0).count()


def _qty(value):
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _check(pos, qty, status):
    """Wspólne sprawdzenie pozycji i ilości. Zwraca (ilość, błąd)."""
    if pos is None or pos.status != status or pos.quantity < 1:
        return None, 'Ta ilość nie czeka już na tę operację. Odśwież stronę.'
    order = pos.component.order
    if order.status != 'active':
        return None, f'Zlecenie ZO {order.zo_number} nie jest w toku.'
    n = _qty(qty)
    if n is None or n < 1:
        return None, 'Ilość musi być liczbą całkowitą większą od zera.'
    if n > pos.quantity:
        return None, f'Dostępne jest tylko {pos.quantity} szt.'
    return n, None


def _take(pos, qty):
    pos.quantity -= qty
    pos.updated_at = _now()
    if pos.quantity <= 0:
        db.session.delete(pos)


def _clean_note(note):
    return (note or '').strip()[:512] or None


def return_stages(pos):
    """Etapy, do których można zwrócić ilość do poprawki: etapy, które ilość mogła przejąć, nie późniejsze
    niż etap wykrytej niezgodności."""
    comp = pos.component
    failing = pos.recheck_stage or pos.stage
    if failing is None:
        return []
    key = (failing.order or 0, failing.id)
    if comp.is_product:
        first = stock.first_product_stage()
        pool = [s for s in stock.product_stages() if first is None or s.id != first.id]
    else:
        pool = [s for s in comp.stages if s.is_active]
    return [s for s in sorted(pool, key=lambda s: (s.order or 0, s.id)) if (s.order or 0, s.id) <= key]


def release(pos, qty, user, note=None):
    """Zwolnienie: ilość wraca do obiegu jako zgodna (np. uwaga niekrytyczna). Zwraca (ruch, błąd). Bez commitu."""
    n, err = _check(pos, qty, 'wstrzymane')
    if err:
        return None, err
    comp, buffer, wait_id, fail_id = pos.component, pos.buffer, pos.stage_id, pos.recheck_stage_id
    _take(pos, n)
    ok = stock._position(comp, buffer, 'ok', wait_id)
    ok.quantity += n
    ok.updated_at = _now()
    mv = Movement(order_id=comp.order_id, component_id=comp.id, kind='zwolnienie', to_buffer_id=buffer.id,
                  wait_stage_id=wait_id, stage_id=fail_id, quantity=n, user_id=user.id, note=_clean_note(note))
    db.session.add(mv)
    db.session.flush()
    return mv, None


def send_back(pos, qty, stage_id, buffer, user, note):
    """Zwrot do poprawki: ilość trafia na wskazany wózek ze statusem „do poprawki” i czeka na wskazany etap.
    Po poprawce czeka na ponowną kontrolę. Wymaga uwagi (co poprawić). Zwraca (ruch, błąd). Bez commitu."""
    n, err = _check(pos, qty, 'wstrzymane')
    if err:
        return None, err
    stages = return_stages(pos)
    target = next((s for s in stages if s.id == _qty(stage_id)), None)
    if target is None:
        names = ', '.join(s.name for s in stages)
        return None, ('Ilość można zwrócić do poprawki na etap: ' + names + '.') if names else (
            'Tej ilości nie da się zwrócić do poprawki (brak etapu do poprawy). Zwolnij ją albo zezłomuj.')
    if buffer is None or not buffer.is_active:
        return None, 'Wskaż aktywny bufor (wózek), na którym ilość będzie czekać na poprawkę.'
    note = _clean_note(note)
    if not note:
        return None, 'Opisz, co ma zostać poprawione.'
    comp, fail_id = pos.component, pos.recheck_stage_id or pos.stage_id
    from_buffer_id = pos.buffer_id
    _take(pos, n)
    fix = stock._position(comp, buffer, 'do_poprawki', target.id, fail_id)
    fix.quantity += n
    fix.updated_at = _now()
    mv = Movement(order_id=comp.order_id, component_id=comp.id, kind='zwrot', from_buffer_id=from_buffer_id,
                  to_buffer_id=buffer.id, wait_stage_id=target.id, stage_id=fail_id, quantity=n,
                  user_id=user.id, note=note)
    db.session.add(mv)
    db.session.flush()
    return mv, None


def scrap(pos, qty, user, note):
    """Złomowanie: wpis do archiwum, wymagana ilość elementu wraca do uzupełnienia. Gotowego produktu nie
    złomuje się (jego elementy zostały zużyte), tylko zwraca do poprawki albo zwalnia. Zwraca (ruch, błąd)."""
    n, err = _check(pos, qty, 'wstrzymane')
    if err:
        return None, err
    comp = pos.component
    if comp.is_product:
        return None, 'Gotowego produktu nie można zezłomować. Zwróć go do poprawki albo zwolnij.'
    note = _clean_note(note)
    if not note:
        return None, 'Podaj powód złomowania.'
    fail_id, buffer_id, wait_id = pos.recheck_stage_id or pos.stage_id, pos.buffer_id, pos.stage_id
    _take(pos, n)
    db.session.add(ScrapRecord(order_id=comp.order_id, component_id=comp.id, stage_id=fail_id,
                               buffer_id=buffer_id, quantity=n, reason=note, user_id=user.id))
    mv = Movement(order_id=comp.order_id, component_id=comp.id, kind='zlomowanie', from_buffer_id=buffer_id,
                  wait_stage_id=wait_id, stage_id=fail_id, quantity=n, user_id=user.id, note=note)
    db.session.add(mv)
    db.session.flush()
    return mv, None


def ad_hoc(pos, qty, stage_id, user):
    """Kontrola doraźna: zgodna ilość z wózka trafia do kolejki kontroli z punktami wskazanego etapu.
    Zwraca (kontrola, błąd). Bez commitu."""
    n, err = _check(pos, qty, 'ok')
    if err:
        return None, err
    stage = db.session.get(ProductionStage, _qty(stage_id) or 0)
    if stage is None or not stage.is_active:
        return None, 'Wskaż aktywny etap, którego punkty kontrolne obowiązują.'
    comp, buffer, wait_id = pos.component, pos.buffer, pos.stage_id
    ctrl = controls.pending_for(comp.id, buffer.id, stage.id, wait_id, 'dorazna')
    if ctrl is not None:
        ctrl.quantity += n
    else:
        ctrl = StageControl(order_id=comp.order_id, component_id=comp.id, buffer_id=buffer.id, stage_id=stage.id,
                            wait_stage_id=wait_id, kind='dorazna', quantity=n)
        db.session.add(ctrl)
    db.session.flush()
    _take(pos, n)
    held = stock._position(comp, buffer, 'do_kontroli', wait_id)
    held.quantity += n
    held.updated_at = _now()
    db.session.add(Movement(order_id=comp.order_id, component_id=comp.id, kind='dorazna', from_buffer_id=buffer.id,
                            to_buffer_id=buffer.id, wait_stage_id=wait_id, stage_id=stage.id, quantity=n,
                            user_id=user.id, control_id=ctrl.id))
    db.session.flush()
    return ctrl, None
