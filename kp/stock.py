"""Stan buforów: wpis elementów na bufor, rozliczenie ilości i wycofanie wpisu (etap 3 planu).

Niezmiennik: suma niewycofanych wpisów dla elementu zlecenia nie przekracza wymaganej ilości."""
from datetime import datetime, timezone

from sqlalchemy import func

from models import (db, Buffer, Movement, StockPosition, WorkOrder, WorkOrderComponent)

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


# ── Wpis na bufor ─────────────────────────────────────────────────────────────

def entry_error(comp, buffer, quantity):
    """Komunikat błędu, gdy wpisu nie wolno wykonać, inaczej None."""
    order = comp.order
    if order.status != 'active':
        return f'Zlecenie ZO {order.zo_number} jest anulowane.'
    if buffer is None or not buffer.is_active:
        return 'Bufor jest nieaktywny albo nie istnieje.'
    stage = buffer.stage
    if stage is None:
        return f'Bufor {buffer.code} nie jest przypisany do żadnego etapu. Przypisz go w Ustawieniach produkcji.'
    if not stage.is_active:
        return f'Etap „{stage.name}” tego bufora jest nieaktywny.'
    if not comp.stage_ids:
        return f'Element „{comp.name}” nie ma etapów w trasie.'
    if stage.id not in comp.stage_ids:
        route = ', '.join(s.name for s in comp.stages)
        return (f'Bufor {buffer.code} należy do etapu „{stage.name}”, którego nie ma w trasie elementu '
                f'„{comp.name}” ({route}).')
    if not isinstance(quantity, int) or quantity < 1:
        return 'Ilość musi być liczbą całkowitą większą od zera.'
    left = remaining_qty(comp)
    if quantity > left:
        return (f'Za duża ilość. Element „{comp.name}” ma jeszcze {left} szt. do wpisania '
                f'(wymagane {comp.required_qty}).' if left else
                f'Element „{comp.name}” został już w całości wpisany (wymagane {comp.required_qty} szt.).')
    return None


def _position(comp, buffer, status='ok'):
    pos = (StockPosition.query.filter_by(component_id=comp.id, buffer_id=buffer.id, status=status)
           .populate_existing().first())
    if pos is None:
        pos = StockPosition(component_id=comp.id, buffer_id=buffer.id, status=status, quantity=0)
        db.session.add(pos)
    return pos


def enter(comp, buffer, quantity, employee, user):
    """Wpisuje ilość elementu na bufor. Zwraca (ruch, None) albo (None, komunikat błędu). Bez commitu."""
    err = entry_error(comp, buffer, quantity)
    if err:
        return None, err
    pos = _position(comp, buffer)
    pos.quantity += quantity
    pos.updated_at = datetime.now(UTC)
    mv = Movement(order_id=comp.order_id, component_id=comp.id, kind='wpis', to_buffer_id=buffer.id,
                  quantity=quantity, employee_id=employee.id if employee else None,
                  user_id=user.id if user else None)
    db.session.add(mv)
    db.session.flush()
    return mv, None


# ── Wycofanie wpisu ───────────────────────────────────────────────────────────

def revert_error(movement, user, employee):
    """Komunikat błędu, gdy użytkownik nie może wycofać wpisu, inaczej None.
    Admin i kontroler wycofują dowolny wpis, operator i stanowisko tylko własny (ten sam pracownik)."""
    if movement.kind != 'wpis':
        return 'Wycofać można tylko wpis na bufor.'
    if movement.reverted_at is not None:
        return 'Ten wpis został już wycofany.'
    if not (user.is_kontroler or (user.can_enter_stock and employee is not None
                                  and movement.employee_id == employee.id)):
        if user.can_enter_stock and employee is None:
            return 'Najpierw zeskanuj swoją plakietkę, aby wycofać własny wpis.'
        return 'Możesz wycofać tylko własny wpis.'
    # Kontrola spójności zawsze na aktualnym stanie z bazy (nie z pamięci sesji).
    pos = (StockPosition.query.filter_by(component_id=movement.component_id,
                                         buffer_id=movement.to_buffer_id, status='ok')
           .populate_existing().first())
    if pos is None or pos.quantity < movement.quantity:
        return ('Nie można wycofać wpisu: część tej ilości nie leży już na buforze '
                '(została przeniesiona albo wstrzymana).')
    return None


def revert(movement, user, employee):
    """Wycofuje wpis: zdejmuje ilość z bufora i zapisuje ruch wycofania. Zwraca (ruch, błąd)."""
    err = revert_error(movement, user, employee)
    if err:
        return None, err
    pos = (StockPosition.query.filter_by(component_id=movement.component_id,
                                         buffer_id=movement.to_buffer_id, status='ok')
           .populate_existing().one())
    pos.quantity -= movement.quantity
    pos.updated_at = datetime.now(UTC)
    if pos.quantity == 0:
        db.session.delete(pos)
    movement.reverted_at = datetime.now(UTC)
    mv = Movement(order_id=movement.order_id, component_id=movement.component_id, kind='cofniecie',
                  from_buffer_id=movement.to_buffer_id, quantity=movement.quantity,
                  employee_id=employee.id if employee else None, user_id=user.id,
                  reverts_id=movement.id)
    db.session.add(mv)
    db.session.flush()
    return mv, None


# ── Widoki danych ─────────────────────────────────────────────────────────────

def component_summary(comp):
    """Dane elementu zlecenia do widoków: wymagane, wpisane, pozostałe, położenie."""
    entered = entered_qty(comp)
    positions = [{'buffer_id': p.buffer_id, 'code': p.buffer.code, 'name': p.buffer.display_name,
                  'stage': p.buffer.stage.name if p.buffer.stage else None,
                  'quantity': p.quantity, 'status': p.status}
                 for p in sorted(comp.positions, key=lambda p: p.buffer.code) if p.quantity > 0]
    return {'id': comp.id, 'name': comp.name, 'per_unit': comp.per_unit, 'required': comp.required_qty,
            'entered': entered, 'remaining': max(0, comp.required_qty - entered),
            'route': [{'id': s.id, 'name': s.name} for s in comp.stages],
            'route_ids': [s.id for s in comp.stages],
            'positions': positions}


def order_summary(order):
    return {'id': order.id, 'zo': order.zo_number, 'product': order.product_name,
            'client': order.client or '', 'quantity': order.quantity, 'status': order.status,
            'components': [component_summary(c) for c in order.components]}


def buffer_contents(buffer):
    """Co leży na buforze: pozycje z ilością większą od zera."""
    rows = []
    for p in (StockPosition.query.filter_by(buffer_id=buffer.id)
              .filter(StockPosition.quantity > 0).all()):
        o = p.component.order
        rows.append({'order_id': o.id, 'zo': o.zo_number, 'product': o.product_name,
                     'client': o.client or '', 'component_id': p.component_id,
                     'component': p.component.name, 'quantity': p.quantity, 'status': p.status})
    rows.sort(key=lambda r: (r['zo'], r['product'], r['component']))
    return rows


def buffer_summary(buffer):
    contents = buffer_contents(buffer)
    return {'id': buffer.id, 'code': buffer.code, 'name': buffer.display_name, 'number': buffer.number,
            'active': buffer.is_active,
            'stage': ({'id': buffer.stage.id, 'name': buffer.stage.name} if buffer.stage else None),
            'contents': contents, 'total': sum(r['quantity'] for r in contents)}


def movement_row(m):
    return {'id': m.id, 'kind': m.kind, 'kind_label': m.kind_label, 'zo': m.order.zo_number,
            'product': m.order.product_name, 'component': m.component.name, 'quantity': m.quantity,
            'buffer': (m.to_buffer.code if m.to_buffer else (m.from_buffer.code if m.from_buffer else '')),
            'employee': m.employee.name if m.employee else '', 'reverted': m.reverted_at is not None,
            'at': m.created_at}


def recent_entries(employee, limit=10):
    """Ostatnie niewycofane wpisy pracownika (do wycofania na stanowisku)."""
    if employee is None:
        return []
    return (Movement.query.filter_by(employee_id=employee.id, kind='wpis')
            .filter(Movement.reverted_at.is_(None))
            .order_by(Movement.created_at.desc(), Movement.id.desc()).limit(limit).all())
