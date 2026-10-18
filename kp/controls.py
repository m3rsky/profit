"""Kontrole etapów (etap 5a planu): plan kontroli, kontrola ilości wchodzącej na bufor etapu, ocena
punktów z podziałem na ilość zgodną i niezgodną oraz raporty QAR za punkty NG.

Kontrola następuje po wykonanym etapie: ilość odstawiona na bufor po etapie z kontrolą dostaje status
`do_kontroli` (nie można jej przejąć) i wraz z nią zapisany jest etap, na który czeka dalej. Oczekujące
partie tego samego elementu, wykonanego etapu i etapu oczekiwania na tym buforze są scalone w jedną kontrolę."""
from datetime import datetime, timezone

from models import (db, Alert, Movement, QARReport, StageControl, StageControlPoint,
                    StageControlResult, StockPosition, User, WorkOrderControl)

UTC = timezone.utc


def _now():
    return datetime.now(UTC)


# ── Plan kontroli i wejście ilości na bufor ───────────────────────────────────

def plan_stage_ids(order):
    """Etapy, na których zlecenie ma kontrolę (plan zlecenia)."""
    return {c.stage_id for c in order.control_plan}


def needs_control(order, stage):
    """Czy ilość wchodząca na bufor tego etapu ma czekać na kontrolę: etap z kontrolą obowiązkową
    albo etap wskazany w planie kontroli zlecenia."""
    return bool(stage is not None and (stage.control_required or stage.id in plan_stage_ids(order)))


def control_kind(order, stage):
    return 'obowiazkowa' if stage.control_required else 'plan'


def pending_for(component_id, buffer_id, stage_id, wait_stage_id):
    return (StageControl.query.filter_by(component_id=component_id, buffer_id=buffer_id, stage_id=stage_id,
                                         wait_stage_id=wait_stage_id, status='oczekuje')
            .populate_existing().first())


def register_arrival(comp, buffer, qty, performed_stage, wait_stage_id):
    """Ustala status ilości odstawianej na bufor po wykonanym etapie i zasila oczekującą kontrolę.
    Zwraca (status pozycji, kontrola albo None, czy kontrola została utworzona). Bez commitu."""
    order = comp.order
    stage = performed_stage
    if not needs_control(order, stage):
        return 'ok', None, False
    ctrl = pending_for(comp.id, buffer.id, stage.id, wait_stage_id)
    created = False
    if ctrl is not None:
        ctrl.quantity += qty
    else:
        ctrl = StageControl(order_id=order.id, component_id=comp.id, buffer_id=buffer.id,
                            stage_id=stage.id, wait_stage_id=wait_stage_id, kind=control_kind(order, stage), quantity=qty)
        db.session.add(ctrl)
        created = True
    db.session.flush()
    return 'do_kontroli', ctrl, created


def notify_new_control(ctrl):
    """Alert dla kontrolerów i adminów o nowej oczekującej kontroli."""
    comp, order, buf = ctrl.component, ctrl.order, ctrl.buffer
    msg = (f'Do kontroli: ZO {order.zo_number}, {comp.name} {ctrl.quantity} szt., '
           f'po etapie {ctrl.stage.name}, bufor {buf.code}.')
    for u in User.query.filter(User.role.in_(['admin', 'kontroler'])).all():
        db.session.add(Alert(recipient_id=u.id, message=msg[:512], alert_type='info'))


def pending_count():
    return StageControl.query.filter_by(status='oczekuje').count()


# ── Wycofanie wpisu z oczekującą kontrolą ─────────────────────────────────────

GONE = 'Nie można wycofać: ilość została już skontrolowana albo przeniesiona dalej.'


def _pending_position(comp_id, buffer_id, wait_stage_id):
    return (StockPosition.query.filter_by(component_id=comp_id, buffer_id=buffer_id, stage_id=wait_stage_id,
                                          status='do_kontroli').populate_existing().first())


def pending_revert_error(movement):
    """Wycofanie wpisu, którego ilość czeka na kontrolę: dozwolone, dopóki kontrola nie została oceniona."""
    ctrl = db.session.get(StageControl, movement.control_id)
    if ctrl is None or ctrl.status != 'oczekuje' or ctrl.quantity < movement.quantity:
        return GONE
    pos = _pending_position(movement.component_id, movement.to_buffer_id, movement.wait_stage_id)
    if pos is None or pos.quantity < movement.quantity:
        return GONE
    return None


def pending_revert(movement):
    """Zdejmuje ilość z oczekującej kontroli i z bufora. Kontrola bez ilości zostaje anulowana."""
    ctrl = db.session.get(StageControl, movement.control_id)
    pos = _pending_position(movement.component_id, movement.to_buffer_id, movement.wait_stage_id)
    pos.quantity -= movement.quantity
    pos.updated_at = _now()
    if pos.quantity <= 0:
        db.session.delete(pos)
    ctrl.quantity -= movement.quantity
    if ctrl.quantity <= 0:
        ctrl.status = 'anulowana'
        ctrl.completed_at = _now()


# ── Ocena kontroli ────────────────────────────────────────────────────────────

def active_points(stage_id):
    return (StageControlPoint.query.filter_by(stage_id=stage_id, is_active=True)
            .order_by(StageControlPoint.order, StageControlPoint.id).all())


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _create_qar(done, title, description, user):
    from qar.routes import _next_qar_number
    order, comp = done.order, done.component
    extra = ' | '.join(x for x in (order.product_name, order.client) if x)
    if extra:
        description += ' | Zlecenie: ' + extra
    qar = QARReport(number=_next_qar_number(), zo_number=order.zo_number,
                    title=title[:256], location=done.stage.name[:128], description=description,
                    user_id=user.id)
    db.session.add(qar)
    db.session.flush()
    return qar


def evaluate(control, expected, ok_qty, ng_qty, point_results, notes, user):
    """Ocenia kontrolę: `expected` to ilość, którą kontroler widział, `point_results` to słownik
    {id punktu: {'result': 'OK'|'NG', 'notes': ...}}. Ilość zgodna dostaje status `ok`, niezgodna
    `wstrzymane`, za każdy punkt NG powstaje raport QAR. Zwraca (wykonana kontrola, lista QAR, błąd).
    Bez commitu."""
    from . import stock
    if control is None or control.status != 'oczekuje':
        return None, [], 'Ta kontrola nie czeka już na ocenę.'
    expected, ok_qty, ng_qty = _int(expected), _int(ok_qty), _int(ng_qty)
    if expected is None or expected < 1:
        return None, [], 'Brak kontrolowanej ilości.'
    if control.quantity < expected:
        return None, [], ('Ilość do kontroli zmniejszyła się (wycofano wpis). Odśwież stronę i oceń ponownie.')
    if ok_qty is None or ng_qty is None or ok_qty < 0 or ng_qty < 0:
        return None, [], 'Ilości zgodna i niezgodna muszą być liczbami całkowitymi nieujemnymi.'
    if ok_qty + ng_qty != expected:
        return None, [], (f'Suma ilości zgodnej ({ok_qty}) i niezgodnej ({ng_qty}) musi być równa '
                          f'kontrolowanej ilości ({expected}).')

    points = active_points(control.stage_id)
    rated, ng_points = [], []
    for p in points:
        r = point_results.get(p.id) or point_results.get(str(p.id)) or {}
        res = (r.get('result') or '').upper()
        if res not in ('OK', 'NG'):
            return None, [], f'Oceń punkt kontrolny: {p.title}.'
        rated.append((p, res, (r.get('notes') or '').strip() or None))
        if res == 'NG':
            ng_points.append(p)
    if points:
        if ng_qty > 0 and not ng_points:
            return None, [], 'Wskaż, który punkt kontrolny jest niezgodny (NG).'
        if ng_qty == 0 and ng_points:
            return None, [], 'Punkt oceniony jako NG wymaga niezgodnej ilości większej od zera.'

    pending = _pending_position(control.component_id, control.buffer_id, control.wait_stage_id)
    if pending is None or pending.quantity < expected:
        return None, [], 'Niespójny stan bufora: brak ilości czekającej na kontrolę.'

    # Kontrola scalona: wykonana część wydziela się do osobnego rekordu, reszta (nowsze partie) czeka dalej.
    if control.quantity == expected:
        done = control
    else:
        done = StageControl(order_id=control.order_id, component_id=control.component_id,
                            buffer_id=control.buffer_id, stage_id=control.stage_id,
                            wait_stage_id=control.wait_stage_id, kind=control.kind,
                            quantity=expected)
        db.session.add(done)
        control.quantity -= expected
        db.session.flush()
    done.ok_qty, done.ng_qty = ok_qty, ng_qty
    done.status = 'zakonczona'
    done.controller_id = user.id
    done.completed_at = _now()
    done.notes = (notes or '').strip() or None

    comp, buffer = control.component, control.buffer
    pending.quantity -= expected
    pending.updated_at = _now()
    if pending.quantity <= 0:
        db.session.delete(pending)
    for status, qty in (('ok', ok_qty), ('wstrzymane', ng_qty)):
        if qty > 0:
            pos = stock._position(comp, buffer, status, control.wait_stage_id)
            pos.quantity += qty
            pos.updated_at = _now()

    rows = []
    for i, (p, res, pnotes) in enumerate(rated, 1):
        row = StageControlResult(control_id=done.id, point_id=p.id, position=i, title=p.title,
                                 description=p.description, result=res, notes=pnotes)
        db.session.add(row)
        rows.append(row)
    db.session.flush()

    qars = []
    where = f'Element: {comp.name}, niezgodnych {ng_qty} z {expected} szt., bufor {buffer.code}.'
    if ng_qty > 0:
        if rated:
            for row in (r for r in rows if r.result == 'NG'):
                desc = row.notes or f'Niezgodność (NG) stwierdzona w kontroli etapu {done.stage.name}, punkt: {row.title}.'
                qar = _create_qar(done, f'Kontrola etapu NG: {row.title}, ZO {done.order.zo_number}',
                                  f'{desc} {where}', user)
                row.qar_report_id = qar.id
                qars.append(qar)
        else:
            desc = done.notes or f'Niezgodność (NG) stwierdzona w kontroli etapu {done.stage.name}.'
            qars.append(_create_qar(done, f'Kontrola etapu NG: {done.stage.name}, ZO {done.order.zo_number}',
                                    f'{desc} {where}', user))

    db.session.add(Movement(order_id=done.order_id, component_id=done.component_id, kind='kontrola',
                            to_buffer_id=buffer.id, quantity=expected, stage_id=done.stage_id,
                            user_id=user.id, control_id=done.id))
    db.session.flush()
    return done, qars, None
