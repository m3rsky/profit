"""Przepływ po wpisie na bufor (etap 4 planu): przejęcie etapu, odstawienie po etapie na dowolny bufor
(wózek), składanie elementów w produkt, pakowanie i wycofanie dowolnego ruchu.

Bufor jest tylko miejscem. Ilość elementu jest zawsze w jednym miejscu: na buforze jako pozycja czekająca
na etap (`StockPosition.stage_id`) albo w realizacji (`WorkInProgress`, po przejęciu etapu). Złożenie
zużywa elementy czekające na składanie na zeskanowanym buforze i tworzy sztuki gotowego produktu
(specjalny element zlecenia `is_product`), które idą dalej jak elementy."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func

from models import (db, Buffer, Machine, Movement, ProductionStage, StockPosition, WorkInProgress, WorkOrder,
                    WorkOrderComponent)
from . import controls, stock

UTC = timezone.utc


def _now():
    return datetime.now(UTC)


def _int(value):
    """Liczba całkowita (także z napisu) albo None. Odrzuca bool i ułamki."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ── Wspólne reguły ────────────────────────────────────────────────────────────

def identify_error(stage, employee, user, own_department=True):
    """Kto może wykonać krok na etapie: kontroler i admin zawsze, inni tylko zidentyfikowani plakietką
    pracownicy działu etapu (etap bez działu nie ogranicza pracownika)."""
    if user.is_kontroler:
        return None
    if employee is None:
        return 'Najpierw zeskanuj swoją plakietkę.'
    if own_department and stage.department_id and employee.department_id != stage.department_id:
        dept = stage.department.name if stage.department else ''
        return (f'Etap „{stage.name}” mogą wykonywać tylko pracownicy działu „{dept}”. '
                f'Twoja plakietka należy do działu „{employee.department.name}”.')
    return None


def machine_check(stage, machine_id):
    """(maszyna, błąd): etap z aktywnymi maszynami wymaga wyboru jednej z nich, etap bez maszyn ich nie ma."""
    active = [m for m in stage.machines if m.is_active]
    if not active:
        return None, None
    mid = _int(machine_id)
    machine = db.session.get(Machine, mid) if mid else None
    if machine is None:
        return None, f'Wybierz maszynę, na której wykonano etap „{stage.name}”.'
    if machine.stage_id != stage.id or not machine.is_active:
        return None, 'Wybrana maszyna nie należy do tego etapu albo jest nieaktywna.'
    return machine, None


def _take_from_position(pos, qty):
    pos.quantity -= qty
    pos.updated_at = _now()
    if pos.quantity <= 0:
        db.session.delete(pos)


def _wip_origin(wip_id):
    """Pierwszy ruch pozycji w realizacji (przejęcie albo złożenie): skąd się wzięła i kto ją wykonuje."""
    return (Movement.query.filter(Movement.wip_id == wip_id, Movement.kind.in_(('przejecie', 'zlozenie')))
            .order_by(Movement.id).first())


# ── Przejęcie etapu ───────────────────────────────────────────────────────────

BLOCKED = {'do_kontroli': 'czeka na kontrolę', 'wstrzymane': 'jest wstrzymane (niezgodne, czeka na decyzję kontrolera)',
           'do_poprawki': 'jest do poprawki'}


def takeover_error(comp, buffer, stage_id, qty, employee, user, machine_id):
    """(komunikat błędu, maszyna). Przejmuje się ilość leżącą na buforze i czekającą na dany etap."""
    order = comp.order
    if order.status != 'active':
        return f'Zlecenie ZO {order.zo_number} nie jest w toku.', None
    if buffer is None or not buffer.is_active:
        return 'Bufor jest nieaktywny albo nie istnieje.', None
    stage = db.session.get(ProductionStage, _int(stage_id) or 0)
    if stage is None:
        return 'Wskaż etap, który przejmujesz.', None
    if not stage.is_active:
        return f'Etap „{stage.name}” jest nieaktywny.', None
    first = stock.first_product_stage()
    if comp.is_product:
        if first is None or stage.id == first.id or stage.id not in {s.id for s in stock.product_stages()}:
            return 'Gotowy produkt można przejąć na etapach po składaniu.', None
    else:
        if first is not None and stage.id == first.id:
            return (f'Na etapie „{stage.name}” elementy się składa. Użyj „Złóż produkt”, '
                    f'zamiast przejmować pojedyncze elementy.'), None
        if stage.id not in comp.stage_ids:
            return (f'Etap „{stage.name}” nie jest w trasie elementu „{comp.name}” '
                    f'({", ".join(s.name for s in comp.stages)}).'), None
    if not isinstance(qty, int) or qty < 1:
        return 'Ilość musi być liczbą całkowitą większą od zera.', None
    pos = stock.position_ref(comp.id, buffer.id, stage.id)
    avail = pos.quantity if pos else 0
    if qty > avail:
        notes = []
        for p in StockPosition.query.filter(StockPosition.component_id == comp.id,
                                            StockPosition.buffer_id == buffer.id,
                                            StockPosition.quantity > 0).all():
            if p.stage_id == stage.id and p.status != 'ok':
                notes.append(f'{p.quantity} szt. {BLOCKED.get(p.status, p.status)}')
            elif p.stage_id != stage.id:
                notes.append(f'{p.quantity} szt. czeka na etap „{p.stage.name if p.stage else "?"}”')
        extra = f' Ponadto na tym buforze: {"; ".join(notes)}.' if notes else ''
        return (f'Na buforze {buffer.code} jest tylko {avail} szt. „{comp.name}” czekających na etap '
                f'„{stage.name}” i gotowych do przejęcia.{extra}' if avail else
                f'Na buforze {buffer.code} nie ma „{comp.name}” gotowych do przejęcia na etap '
                f'„{stage.name}”.{extra}'), None
    err = identify_error(stage, employee, user)
    if err:
        return err, None
    machine, err = machine_check(stage, machine_id)
    if err:
        return err, None
    return None, machine


def takeover(comp, buffer, stage_id, qty, employee, user, machine_id):
    """Zdejmuje ilość czekającą na etap z bufora do stanu „w realizacji”. Zwraca (ruch, błąd). Bez commitu."""
    err, machine = takeover_error(comp, buffer, stage_id, qty, employee, user, machine_id)
    if err:
        return None, err
    stage = db.session.get(ProductionStage, _int(stage_id))
    _take_from_position(stock.position_ref(comp.id, buffer.id, stage.id), qty)
    wip = WorkInProgress(component_id=comp.id, stage_id=stage.id, quantity=qty,
                         employee_id=employee.id if employee else None, user_id=user.id,
                         machine_id=machine.id if machine else None, source_buffer_id=buffer.id)
    db.session.add(wip)
    db.session.flush()
    mv = Movement(order_id=comp.order_id, component_id=comp.id, kind='przejecie',
                  from_buffer_id=buffer.id, quantity=qty, stage_id=stage.id,
                  machine_id=machine.id if machine else None,
                  employee_id=employee.id if employee else None, user_id=user.id,
                  wip_id=wip.id, group_id=uuid.uuid4().hex)
    db.session.add(mv)
    db.session.flush()
    return mv, None


# ── Odstawienie po etapie na dowolny bufor ────────────────────────────────────

def transfer_error(wip, buffer, qty, employee, user, wait_stage_id=None):
    """(komunikat błędu, etap oczekiwania). Po etapie ilość trafia na dowolny aktywny bufor (wózek) i czeka
    na następny etap trasy albo na wskazany późniejszy."""
    comp = wip.component
    order = comp.order
    if order.status != 'active':
        return f'Zlecenie ZO {order.zo_number} nie jest w toku.', None
    if buffer is None or not buffer.is_active:
        return 'Bufor jest nieaktywny albo nie istnieje.', None
    targets = stock.target_stages(comp, wip.stage)
    if not targets:
        return (f'Po etapie „{wip.stage.name}” nie ma już kolejnego etapu. Ilość kończy się jako spakowana '
                f'(przycisk „Spakowane”).'), None
    if wait_stage_id in (None, '', 0):
        wait = targets[0]
    else:
        wait = next((s for s in targets if s.id == _int(wait_stage_id)), None)
        if wait is None:
            names = ', '.join(s.name for s in targets)
            return (f'Po etapie „{wip.stage.name}” element „{comp.name}” może czekać na etap: {names}.'), None
    if not isinstance(qty, int) or qty < 1:
        return 'Ilość musi być liczbą całkowitą większą od zera.', None
    if qty > wip.quantity:
        return f'W realizacji jest tylko {wip.quantity} szt. „{comp.name}”.', None
    if not user.is_kontroler and employee is None:
        return 'Najpierw zeskanuj swoją plakietkę.', None
    return None, wait


def transfer(wip, buffer, qty, employee, user, wait_stage_id=None):
    """Odstawia ilość z „w realizacji” na bufor jako czekającą na kolejny etap. Gdy wykonany etap ma
    kontrolę (obowiązkową albo z planu zlecenia), ilość czeka na kontrolę. Zwraca (ruch, błąd). Bez commitu."""
    err, wait = transfer_error(wip, buffer, qty, employee, user, wait_stage_id)
    if err:
        return None, err
    comp = wip.component
    performed = wip.stage
    stage_id, machine_id, wip_id = wip.stage_id, wip.machine_id, wip.id
    wip.quantity -= qty
    if wip.quantity <= 0:
        db.session.delete(wip)
    status, ctrl, created = controls.register_arrival(comp, buffer, qty, performed, wait.id)
    pos = stock._position(comp, buffer, status, wait.id)
    pos.quantity += qty
    pos.updated_at = _now()
    mv = Movement(order_id=comp.order_id, component_id=comp.id, kind='przeniesienie',
                  to_buffer_id=buffer.id, wait_stage_id=wait.id, quantity=qty, stage_id=stage_id,
                  machine_id=machine_id, employee_id=employee.id if employee else None, user_id=user.id,
                  wip_id=wip_id, group_id=uuid.uuid4().hex, control_id=ctrl.id if ctrl else None)
    db.session.add(mv)
    db.session.flush()
    if created:
        controls.notify_new_control(ctrl)
    return mv, None


# ── Składanie ─────────────────────────────────────────────────────────────────

def product_produced(order):
    comp = order.product_component
    if comp is None:
        return 0
    total = (db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
             .filter(Movement.component_id == comp.id, Movement.kind == 'zlozenie',
                     Movement.reverted_at.is_(None)).scalar())
    return int(total or 0)


def product_packed(order):
    comp = order.product_component
    if comp is None:
        return 0
    total = (db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
             .filter(Movement.component_id == comp.id, Movement.kind == 'spakowanie',
                     Movement.reverted_at.is_(None)).scalar())
    return int(total or 0)


def assembly_info(order, buffer):
    """Ile sztuk produktu da się złożyć z elementów leżących na zeskanowanym buforze i czekających na
    składanie: minimum po elementach z (ilość podzielona przez ilość na sztukę, w dół), nie więcej niż
    zostało do złożenia z `q`."""
    stage = stock.first_product_stage()
    elements = []
    for c in order.components:
        pos = stock.position_ref(c.id, buffer.id, stage.id) if stage else None
        avail = pos.quantity if pos else 0
        per = max(1, c.per_unit)
        elements.append({'id': c.id, 'name': c.name, 'per_unit': per, 'available': avail,
                         'units': avail // per})
    produced = product_produced(order)
    left = max(0, order.quantity - produced)
    limit = min([e['units'] for e in elements] + [left]) if (elements and stage) else 0
    missing = [e['name'] for e in elements if e['units'] == 0]
    return {'limit': limit, 'produced': produced, 'left': left, 'elements': elements, 'missing': missing,
            'stage': {'id': stage.id, 'name': stage.name} if stage else None,
            'machines': stock.machines_json(stage), 'any': any(e['available'] for e in elements)}


def _ensure_product_component(order):
    comp = order.product_component
    if comp is None:
        comp = WorkOrderComponent(order_id=order.id, name='Gotowy produkt', per_unit=1,
                                  required_qty=order.quantity, position=0, is_product=True)
        db.session.add(comp)
        db.session.flush()
        db.session.expire(order, ['all_components'])
    return comp


def assemble_error(order, buffer, qty, employee, user, machine_id):
    if order.status != 'active':
        return f'Zlecenie ZO {order.zo_number} nie jest w toku.', None, None
    if buffer is None or not buffer.is_active:
        return 'Bufor jest nieaktywny albo nie istnieje.', None, None
    stage = stock.first_product_stage()
    if stage is None:
        return 'Brak etapu składania w ustawieniach produkcji.', None, None
    info = assembly_info(order, buffer)
    if not isinstance(qty, int) or qty < 1:
        return 'Ilość musi być liczbą całkowitą większą od zera.', None, info
    if info['left'] == 0:
        return 'Wszystkie sztuki zlecenia zostały już złożone.', None, info
    if info['limit'] == 0:
        return ('Na tym buforze brakuje elementów czekających na składanie: ' + ', '.join(info['missing']) + '.',
                None, info)
    if qty > info['limit']:
        return f'Z elementów na tym buforze można złożyć najwyżej {info["limit"]} szt. produktu.', None, info
    err = identify_error(stage, employee, user)
    if err:
        return err, None, info
    machine, err = machine_check(stage, machine_id)
    if err:
        return err, None, info
    return None, machine, info


def assemble(order, buffer, qty, employee, user, machine_id):
    """Składa `qty` sztuk produktu: zużywa elementy czekające na składanie na buforze i tworzy produkt
    „w realizacji”. Zwraca (ruch złożenia, błąd). Bez commitu."""
    err, machine, info = assemble_error(order, buffer, qty, employee, user, machine_id)
    if err:
        return None, err
    stage = stock.first_product_stage()
    group = uuid.uuid4().hex
    emp_id = employee.id if employee else None
    for e in info['elements']:
        comp = db.session.get(WorkOrderComponent, e['id'])
        use = e['per_unit'] * qty
        _take_from_position(stock.position_ref(comp.id, buffer.id, stage.id), use)
        db.session.add(Movement(order_id=order.id, component_id=comp.id, kind='zuzycie',
                                from_buffer_id=buffer.id, quantity=use, stage_id=stage.id,
                                employee_id=emp_id, user_id=user.id, group_id=group))
    product = _ensure_product_component(order)
    wip = WorkInProgress(component_id=product.id, stage_id=stage.id, quantity=qty, employee_id=emp_id,
                         user_id=user.id, machine_id=machine.id if machine else None,
                         source_buffer_id=buffer.id)
    db.session.add(wip)
    db.session.flush()
    mv = Movement(order_id=order.id, component_id=product.id, kind='zlozenie', from_buffer_id=buffer.id,
                  quantity=qty, stage_id=stage.id, machine_id=machine.id if machine else None,
                  employee_id=emp_id, user_id=user.id, wip_id=wip.id, group_id=group)
    db.session.add(mv)
    db.session.flush()
    return mv, None


# ── Pakowanie ─────────────────────────────────────────────────────────────────

def pack_error(wip, qty, employee, user):
    comp = wip.component
    if not comp.is_product:
        return 'Spakować można tylko gotowy produkt.'
    last = stock.last_product_stage()
    if last is None or wip.stage_id != last.id:
        where = f' „{last.name}”' if last else ''
        return f'Spakować można dopiero po przejęciu etapu{where}.'
    order = comp.order
    if order.status != 'active':
        return f'Zlecenie ZO {order.zo_number} nie jest w toku.'
    if not isinstance(qty, int) or qty < 1:
        return 'Ilość musi być liczbą całkowitą większą od zera.'
    if qty > wip.quantity:
        return f'W realizacji jest tylko {wip.quantity} szt. produktu.'
    if not user.is_kontroler and employee is None:
        return 'Najpierw zeskanuj swoją plakietkę.'
    return None


def pack(wip, qty, employee, user):
    """Oznacza ilość produktu jako spakowaną. Spakowane całe `q` kończy zlecenie. Zwraca (ruch, błąd)."""
    err = pack_error(wip, qty, employee, user)
    if err:
        return None, err
    comp = wip.component
    order = comp.order
    stage_id, machine_id, wip_id = wip.stage_id, wip.machine_id, wip.id
    wip.quantity -= qty
    if wip.quantity <= 0:
        db.session.delete(wip)
    mv = Movement(order_id=order.id, component_id=comp.id, kind='spakowanie', quantity=qty,
                  stage_id=stage_id, machine_id=machine_id,
                  employee_id=employee.id if employee else None, user_id=user.id, wip_id=wip_id,
                  group_id=uuid.uuid4().hex)
    db.session.add(mv)
    db.session.flush()
    if product_packed(order) >= order.quantity:
        order.status = 'done'
        order.completed_at = _now()
    return mv, None


# ── Wycofanie ruchu ───────────────────────────────────────────────────────────

REVERTIBLE = ('wpis', 'przejecie', 'przeniesienie', 'zlozenie', 'spakowanie')


def revert_error(movement, user, employee):
    """Komunikat błędu, gdy ruchu nie wolno wycofać, inaczej None. Admin i kontroler wycofują dowolny
    ruch, operator i stanowisko tylko własny (ten sam pracownik). Ruch, od którego odeszły dalsze
    (ilość nie leży już tam, gdzie trafiła), wycofać można dopiero po wycofaniu późniejszych."""
    if movement.kind == 'zuzycie':
        return 'Zużycie elementów wycofuje się razem ze złożeniem produktu.'
    if movement.kind not in REVERTIBLE:
        return 'Tego ruchu nie można wycofać.'
    if movement.reverted_at is not None:
        return 'Ten ruch został już wycofany.'
    if not (user.is_kontroler or (user.can_enter_stock and employee is not None
                                  and movement.employee_id == employee.id)):
        if user.can_enter_stock and employee is None:
            return 'Najpierw zeskanuj swoją plakietkę, aby wycofać własny ruch.'
        return 'Możesz wycofać tylko własny ruch.'
    gone = 'Nie można wycofać: ilość nie leży już tam, gdzie trafiła (została przeniesiona dalej).'
    if movement.kind in ('wpis', 'przeniesienie') and movement.control_id:
        return controls.pending_revert_error(movement)     # ilość czeka na kontrolę
    if movement.kind in ('wpis', 'przeniesienie'):
        pos = stock.position_ref(movement.component_id, movement.to_buffer_id, movement.wait_stage_id)
        return None if pos is not None and pos.quantity >= movement.quantity else gone
    if movement.kind in ('przejecie', 'zlozenie'):
        wip = db.session.get(WorkInProgress, movement.wip_id) if movement.wip_id else None
        if wip is None or wip.quantity != movement.quantity:
            return 'Nie można wycofać: część ilości została już przeniesiona na bufor lub spakowana.'
        return None
    if movement.kind == 'spakowanie':
        return None
    return 'Tego ruchu nie można wycofać.'


def _restore_wip(movement, qty):
    """Zwraca ilość do pozycji „w realizacji” (odtwarza ją, jeśli została całkowicie zużyta)."""
    wip = db.session.get(WorkInProgress, movement.wip_id) if movement.wip_id else None
    if wip is not None:
        wip.quantity += qty
        return wip
    origin = _wip_origin(movement.wip_id) if movement.wip_id else None
    wip = WorkInProgress(component_id=movement.component_id, stage_id=movement.stage_id,
                         quantity=qty, machine_id=movement.machine_id,
                         employee_id=(origin.employee_id if origin else movement.employee_id),
                         user_id=(origin.user_id if origin else movement.user_id),
                         source_buffer_id=(origin.from_buffer_id if origin else None))
    db.session.add(wip)
    db.session.flush()
    return wip


def revert(movement, user, employee):
    """Wycofuje ruch i zapisuje ruch wycofania. Zwraca (ruch wycofania, błąd). Bez commitu."""
    err = revert_error(movement, user, employee)
    if err:
        return None, err
    now = _now()
    qty = movement.quantity
    kind = movement.kind
    back_to = None
    if kind in ('wpis', 'przeniesienie'):
        if movement.control_id:
            controls.pending_revert(movement)
        else:
            _take_from_position(stock.position_ref(movement.component_id, movement.to_buffer_id,
                                                   movement.wait_stage_id), qty)
        if kind == 'przeniesienie':
            _restore_wip(movement, qty)
    elif kind == 'przejecie':
        wip = db.session.get(WorkInProgress, movement.wip_id)
        back_to = db.session.get(Buffer, movement.from_buffer_id)
        pos = stock._position(wip.component, back_to, 'ok', movement.stage_id)
        pos.quantity += qty
        pos.updated_at = now
        db.session.delete(wip)
    elif kind == 'zlozenie':
        wip = db.session.get(WorkInProgress, movement.wip_id)
        db.session.delete(wip)
        for used in Movement.query.filter_by(group_id=movement.group_id, kind='zuzycie').all():
            if used.reverted_at is None:
                pos = stock._position(used.component, used.from_buffer, 'ok', used.stage_id)
                pos.quantity += used.quantity
                pos.updated_at = now
                used.reverted_at = now
        back_to = db.session.get(Buffer, movement.from_buffer_id)
    elif kind == 'spakowanie':
        _restore_wip(movement, qty)
        order = movement.order
        if order.status == 'done':
            order.status = 'active'
            order.completed_at = None
    movement.reverted_at = now
    mv = Movement(order_id=movement.order_id, component_id=movement.component_id, kind='cofniecie',
                  from_buffer_id=movement.to_buffer_id,
                  to_buffer_id=back_to.id if back_to else None, quantity=qty,
                  stage_id=movement.stage_id, employee_id=employee.id if employee else None,
                  user_id=user.id, reverts_id=movement.id, wip_id=movement.wip_id)
    db.session.add(mv)
    db.session.flush()
    return mv, None
