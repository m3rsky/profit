"""Stan buforów (wózków): wpis elementów, rozliczenie ilości, trasy etapów i widoki danych (etapy 3 i 4).

Bufor (wózek) jest tylko miejscem. Informacja o tym, na który etap ilość czeka, należy do pozycji
(`StockPosition.stage_id`): na jednym wózku mogą leżeć ilości różnych zleceń czekające na różne etapy.

Niezmiennik: suma niewycofanych wpisów dla elementu zlecenia nie przekracza wymaganej ilości."""
from datetime import datetime, timezone

from sqlalchemy import func

from models import (db, Buffer, Machine, Movement, ProductionStage, StockPosition, WorkInProgress, WorkOrder,
                    WorkOrderComponent)

UTC = timezone.utc


# ── Rozliczenie ilości ────────────────────────────────────────────────────────

def entered_qty(comp):
    """Ilość elementu wpisana na bufory (wpisy niewycofane)."""
    total = (db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
             .filter(Movement.component_id == comp.id, Movement.kind == 'wpis',
                     Movement.reverted_at.is_(None)).scalar())
    return int(total or 0)


def remaining_qty(comp):
    return max(0, comp.required_qty - entered_qty(comp))


def stock_qty(comp):
    """Ilość elementu aktualnie leżąca na buforach."""
    total = (db.session.query(func.coalesce(func.sum(StockPosition.quantity), 0))
             .filter(StockPosition.component_id == comp.id).scalar())
    return int(total or 0)


def has_history(comp=None, buffer=None):
    """Czy element lub bufor ma jakiekolwiek ruchy (wtedy nie wolno ich usuwać)."""
    q = Movement.query
    if comp is not None:
        q = q.filter(Movement.component_id == comp.id)
    if buffer is not None:
        q = q.filter((Movement.to_buffer_id == buffer.id) | (Movement.from_buffer_id == buffer.id))
    return q.first() is not None


# ── Etapy i trasy ─────────────────────────────────────────────────────────────

def product_stages():
    """Aktywne etapy poziomu produktu (składanie, kontrola końcowa, pakowanie) w kolejności."""
    return (ProductionStage.query.filter_by(level='produkt', is_active=True)
            .order_by(ProductionStage.order, ProductionStage.id).all())


def first_product_stage():
    """Etap składania: pierwszy etap poziomu produktu. Tu elementy łączą się w sztuki produktu."""
    stages = product_stages()
    return stages[0] if stages else None


def last_product_stage():
    """Ostatni etap poziomu produktu (pakowanie): tu ilość kończy się jako spakowana."""
    stages = product_stages()
    return stages[-1] if stages else None


def entry_stages(comp):
    """Etapy, na które element wpisany z zewnątrz może czekać: etapy trasy (w kolejności) oraz etap
    składania (element może być już gotowy do złożenia). Gotowy produkt powstaje tylko przez złożenie."""
    if comp.is_product:
        return []
    stages = [s for s in comp.stages if s.is_active]
    first = first_product_stage()
    if first and first.id not in {s.id for s in stages}:
        stages.append(first)
    return sorted(stages, key=lambda s: (s.order or 0, s.id))


def entry_stage_ids(comp):
    return {s.id for s in entry_stages(comp)}


def target_stages(comp, stage):
    """Etapy, na które ilość może czekać po wykonaniu `stage`: dowolny późniejszy etap trasy elementu,
    a po trasie etap składania. Dla gotowego produktu późniejsze etapy poziomu produktu."""
    key = (stage.order or 0, stage.id)
    if comp.is_product:
        pool = product_stages()
    else:
        pool = [s for s in comp.stages if s.is_active]
        first = first_product_stage()
        if first and first.id not in {s.id for s in pool}:
            pool.append(first)
    return [s for s in sorted(pool, key=lambda s: (s.order or 0, s.id)) if (s.order or 0, s.id) > key]


# ── Pozycje na buforach ───────────────────────────────────────────────────────

def _position(comp, buffer, status='ok', stage_id=None):
    """Pozycja elementu na buforze z danym statusem i etapem oczekiwania (tworzona, gdy nie istnieje)."""
    pos = (StockPosition.query.filter_by(component_id=comp.id, buffer_id=buffer.id, status=status,
                                         stage_id=stage_id)
           .populate_existing().first())
    if pos is None:
        pos = StockPosition(component_id=comp.id, buffer_id=buffer.id, status=status, stage_id=stage_id,
                            quantity=0)
        db.session.add(pos)
    return pos


def position_ref(component_id, buffer_id, stage_id, status='ok'):
    """Pozycja odczytana na nowo z bazy (nie z pamięci sesji) albo None."""
    return (StockPosition.query.filter_by(component_id=component_id, buffer_id=buffer_id, status=status,
                                          stage_id=stage_id).populate_existing().first())


# ── Wpis na bufor ─────────────────────────────────────────────────────────────

def default_entry_stage(comp):
    stages = entry_stages(comp)
    return stages[0] if stages else None


def entry_error(comp, buffer, quantity, stage):
    """Komunikat błędu, gdy wpisu nie wolno wykonać, inaczej None. `stage` to etap, na który ilość czeka."""
    order = comp.order
    if order.status != 'active':
        return f'Zlecenie ZO {order.zo_number} jest anulowane.'
    if buffer is None or not buffer.is_active:
        return 'Bufor jest nieaktywny albo nie istnieje.'
    if comp.is_product:
        return 'Gotowy produkt powstaje przez złożenie elementów, nie przez wpis na bufor.'
    if not comp.stage_ids:
        return f'Element „{comp.name}” nie ma etapów w trasie.'
    if stage is None:
        return 'Wskaż etap, na który element czeka.'
    if stage.id not in entry_stage_ids(comp):
        route = ', '.join(s.name for s in entry_stages(comp))
        return (f'Etap „{stage.name}” nie jest w trasie elementu „{comp.name}”. Możliwe etapy: {route}.')
    if not isinstance(quantity, int) or quantity < 1:
        return 'Ilość musi być liczbą całkowitą większą od zera.'
    left = remaining_qty(comp)
    if quantity > left:
        return (f'Za duża ilość. Element „{comp.name}” ma jeszcze {left} szt. do wpisania '
                f'(wymagane {comp.required_qty}).' if left else
                f'Element „{comp.name}” został już w całości wpisany (wymagane {comp.required_qty} szt.).')
    return None


def enter(comp, buffer, quantity, employee, user, stage_id=None):
    """Wpisuje ilość elementu na bufor jako czekającą na etap `stage_id` (domyślnie pierwszy etap trasy).
    Wpis z zewnątrz nie ma wykonanego etapu, więc nie podlega kontroli. Zwraca (ruch, błąd). Bez commitu."""
    stage = db.session.get(ProductionStage, stage_id) if stage_id else default_entry_stage(comp)
    err = entry_error(comp, buffer, quantity, stage)
    if err:
        return None, err
    pos = _position(comp, buffer, 'ok', stage.id)
    pos.quantity += quantity
    pos.updated_at = datetime.now(UTC)
    mv = Movement(order_id=comp.order_id, component_id=comp.id, kind='wpis', to_buffer_id=buffer.id,
                  wait_stage_id=stage.id, quantity=quantity, employee_id=employee.id if employee else None,
                  user_id=user.id if user else None)
    db.session.add(mv)
    db.session.flush()
    return mv, None


# ── Widoki danych ─────────────────────────────────────────────────────────────

STATUS_LABELS = {'ok': 'gotowe', 'do_kontroli': 'czeka na kontrolę', 'wstrzymane': 'wstrzymane (NG)',
                 'do_poprawki': 'do poprawki'}


def machines_json(stage):
    return [{'id': m.id, 'name': m.name} for m in stage.machines if m.is_active] if stage else []


def _position_json(p):
    st = p.stage
    return {'buffer_id': p.buffer_id, 'code': p.buffer.code, 'name': p.buffer.display_name,
            'stage_id': p.stage_id, 'stage': st.name if st else None, 'machines': machines_json(st),
            'quantity': p.quantity, 'status': p.status, 'status_label': STATUS_LABELS.get(p.status, p.status)}


def _positions_json(comp):
    return [_position_json(p)
            for p in sorted(comp.positions, key=lambda p: (p.buffer.code, (p.stage.order if p.stage else 0), p.status))
            if p.quantity > 0]


def wip_json(w):
    """Pozycja „w realizacji” do widoków stanowiska: na jakie etapy może czekać dalej i czy można ją spakować."""
    comp = w.component
    last = last_product_stage()
    targets = target_stages(comp, w.stage)
    return {'id': w.id, 'component_id': comp.id, 'component': comp.name, 'is_product': comp.is_product,
            'order_id': comp.order_id, 'zo': comp.order.zo_number, 'product': comp.order.product_name,
            'stage_id': w.stage_id, 'stage': w.stage.name, 'quantity': w.quantity,
            'machine': w.machine.name if w.machine else '', 'employee': w.employee.name if w.employee else '',
            'employee_id': w.employee_id,
            'targets': [{'id': s.id, 'name': s.name} for s in targets],
            'default_target_id': targets[0].id if targets else None,
            'can_pack': bool(comp.is_product and last is not None and w.stage_id == last.id)}


def order_wip(order):
    rows = []
    for c in order.all_components:
        rows.extend(c.wip)
    return sorted(rows, key=lambda w: w.id)


def employee_wip(employee):
    if employee is None:
        return []
    return WorkInProgress.query.filter_by(employee_id=employee.id).order_by(WorkInProgress.id).all()


def component_summary(comp):
    """Dane elementu zlecenia do widoków: wymagane, wpisane, pozostałe, położenie, w realizacji."""
    entered = entered_qty(comp)
    entries = entry_stages(comp)
    default = default_entry_stage(comp)
    return {'id': comp.id, 'name': comp.name, 'per_unit': comp.per_unit, 'required': comp.required_qty,
            'entered': entered, 'remaining': max(0, comp.required_qty - entered),
            'in_progress': sum(w.quantity for w in comp.wip),
            'route': [{'id': s.id, 'name': s.name} for s in comp.stages],
            'route_ids': [s.id for s in comp.stages],
            'entry_stages': [{'id': s.id, 'name': s.name} for s in entries],
            'default_entry_stage_id': default.id if default else None,
            'positions': _positions_json(comp)}


def product_summary(order):
    """Gotowy produkt zlecenia: ile złożono, ile jest w realizacji, ile spakowano."""
    from . import flow
    comp = order.product_component
    return {'required': order.quantity, 'produced': flow.product_produced(order),
            'packed': flow.product_packed(order),
            'in_progress': sum(w.quantity for w in comp.wip) if comp else 0,
            'positions': _positions_json(comp) if comp else [], 'component_id': comp.id if comp else None}


def order_summary(order, buffer=None):
    """Zlecenie z elementami. Przy podanym buforze dodaje dane składania (elementy czekające na składanie
    na tym buforze)."""
    from . import flow
    data = {'id': order.id, 'zo': order.zo_number, 'product': order.product_name,
            'client': order.client or '', 'quantity': order.quantity, 'status': order.status,
            'status_label': order.status_label,
            'components': [component_summary(c) for c in order.components],
            'product_unit': product_summary(order),
            'wip': [wip_json(w) for w in order_wip(order)], 'assembly': None}
    if buffer is not None and first_product_stage() is not None:
        data['assembly'] = flow.assembly_info(order, buffer)
    return data


def buffer_contents(buffer):
    """Co leży na buforze: pozycje z ilością większą od zera, z etapem, na który czekają."""
    rows = []
    for p in (StockPosition.query.filter_by(buffer_id=buffer.id)
              .filter(StockPosition.quantity > 0).all()):
        o = p.component.order
        rows.append({'order_id': o.id, 'zo': o.zo_number, 'product': o.product_name,
                     'client': o.client or '', 'component_id': p.component_id,
                     'component': p.component.name, 'is_product': p.component.is_product,
                     'stage_id': p.stage_id, 'stage': p.stage.name if p.stage else '',
                     'quantity': p.quantity, 'status': p.status,
                     'status_label': STATUS_LABELS.get(p.status, p.status)})
    rows.sort(key=lambda r: (r['zo'], r['product'], r['component'], r['stage']))
    return rows


def buffer_summary(buffer):
    contents = buffer_contents(buffer)
    return {'id': buffer.id, 'code': buffer.code, 'name': buffer.display_name, 'number': buffer.number,
            'active': buffer.is_active, 'contents': contents, 'total': sum(r['quantity'] for r in contents)}


def movement_row(m):
    return {'id': m.id, 'kind': m.kind, 'kind_label': m.kind_label, 'zo': m.order.zo_number,
            'product': m.order.product_name, 'component': m.component.name, 'quantity': m.quantity,
            'buffer': (m.to_buffer.code if m.to_buffer else (m.from_buffer.code if m.from_buffer else '')),
            'employee': m.employee.name if m.employee else '', 'reverted': m.reverted_at is not None,
            'at': m.created_at}


def recent_entries(employee, limit=10):
    """Ostatnie niewycofane ruchy pracownika, które da się wycofać (do przycisku „Cofnij”)."""
    if employee is None:
        return []
    return (Movement.query.filter_by(employee_id=employee.id)
            .filter(Movement.kind.in_(('wpis', 'przejecie', 'przeniesienie', 'zlozenie', 'spakowanie')))
            .filter(Movement.reverted_at.is_(None))
            .order_by(Movement.created_at.desc(), Movement.id.desc()).limit(limit).all())
