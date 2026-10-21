from datetime import datetime, timezone
from functools import wraps

from flask import (render_template, redirect, url_for, request,
                   flash, abort, current_app, jsonify)
from flask_login import login_required, current_user
from sqlalchemy import func, or_

from models import (db, get_or_404, AuditLog, Alert, User, QARReport,
                    ControlElement, ControlOrder, ControlPoint, ControlRecord, ControlRecordElement,
                    ControlResult, DepartmentEmployee,
                    ProductionDepartment, CONTROL_STATE_LABELS)
from . import kontrola_bp

UTC = timezone.utc


# ── Dekoratory dostępu ────────────────────────────────────────────────────────

def view_required(f):
    """admin + kontroler + operator"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_view_control:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def controller_required(f):
    """admin + kontroler"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_kontroler:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def operator_required(f):
    """admin + operator"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not (current_user.is_admin or current_user.is_operator):
            abort(403)
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── Pomocnicze ────────────────────────────────────────────────────────────────

def _audit(action, target_id=None, detail=None):
    try:
        uid = current_user.id if current_user.is_authenticated else None
        db.session.add(AuditLog(
            user_id=uid, action=action, target_type='control_record',
            target_id=target_id, detail=detail, ip=request.remote_addr))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Kontrola audit error: %s', exc)


def _clean_zo(raw):
    """Usuwa query string, który mógł zostać doklejony przez serwer proxy."""
    return str(raw or '').split('?')[0].strip()


def _active_points(department_id=None):
    """Punkty kontroli ogólnej (bez działu) albo kontroli działu (punkty działu i ogólne)."""
    q = ControlPoint.query.filter_by(is_active=True).filter(ControlPoint.element_id.is_(None))
    if department_id is None:
        q = q.filter(ControlPoint.department_id.is_(None))
    else:
        q = q.filter(or_(ControlPoint.department_id.is_(None), ControlPoint.department_id == department_id))
    return q.order_by(ControlPoint.order, ControlPoint.id).all()


def _control_departments():
    """Aktywne działy mające co najmniej jeden aktywny punkt przypisany do siebie."""
    return (ProductionDepartment.query.filter_by(is_active=True)
            .filter(ProductionDepartment.id.in_(
                db.session.query(ControlPoint.department_id).filter(
                    ControlPoint.is_active.is_(True), ControlPoint.department_id.isnot(None))))
            .order_by(ProductionDepartment.order, ProductionDepartment.name).all())


def _active_elements():
    return ControlElement.query.filter_by(is_active=True).order_by(ControlElement.order, ControlElement.id).all()


def _element_points(element_id, department_id):
    """Punkty elementu w danym dziale: punkty elementu bez działu oraz punkty elementu tego działu."""
    return (ControlPoint.query.filter_by(is_active=True, element_id=element_id)
            .filter(or_(ControlPoint.department_id.is_(None), ControlPoint.department_id == department_id))
            .order_by(ControlPoint.order, ControlPoint.id).all())


def _add_department_results(rec, department_id, elements):
    """Wyniki kontroli działu: sekcja wspólna (punkty ogólne i działu), potem punkty każdego wybranego
    elementu osobno. Zwraca liczbę punktów. Bez commitu."""
    position = 0
    for p in _active_points(department_id):
        position += 1
        db.session.add(ControlResult(record=rec, point_id=p.id, position=position,
                                     title=p.title, description=p.description))
    for i, el in enumerate(elements, 1):
        db.session.add(ControlRecordElement(record_id=rec.id, element_id=el.id, name=el.name, position=i))
        for p in _element_points(el.id, department_id):
            position += 1
            db.session.add(ControlResult(record=rec, point_id=p.id, position=position, title=p.title,
                                         description=p.description, element_id=el.id, element_name=el.name))
    return position


def _parse_elements(raw_ids):
    """Wybrane elementy (kolejność wyboru, bez duplikatów). Zwraca (lista elementów, błąd)."""
    elements, seen = [], set()
    for raw in raw_ids:
        try:
            el = db.session.get(ControlElement, int(raw))
        except (TypeError, ValueError):
            el = None
        if el is None or not el.is_active:
            return [], 'Wybrany element nie istnieje albo jest nieaktywny.'
        if el.id not in seen:
            seen.add(el.id)
            elements.append(el)
    return elements, None


def _add_results(rec, rows):
    """Dodaje do kontroli wiersze punktów (kopia tytułu i opisu). `rows` to
    ControlPoint albo ControlResult z poprzedniej kontroli. Bez commitu."""
    for i, p in enumerate(rows, 1):
        point_id = p.id if isinstance(p, ControlPoint) else p.point_id
        db.session.add(ControlResult(record=rec, point_id=point_id, position=i,
                                     title=p.title, description=p.description))


def _notify(roles, message, alert_type='info'):
    for u in User.query.filter(User.role.in_(roles)).all():
        db.session.add(Alert(recipient_id=u.id, message=message[:512], alert_type=alert_type))


def _norm(text):
    return ' '.join((text or '').upper().split())


def _label(rec):
    """Opis zamówienia do komunikatów: pod jednym ZO może być kilka produktów."""
    return f'ZO {rec.zo_number}' + (f' ({rec.product_name})' if rec.product_name else '')


def _latest_for_order(zo, product_name):
    """Najnowszy etap cyklu dla pary ZO + produkt: kontrola sprawdzająca, jeśli istnieje,
    inaczej wstępna. Ten sam ZO z innym produktem to osobna kontrola."""
    wanted = _norm(product_name)
    candidates = (ControlRecord.query.filter_by(zo_number=zo, round=1, department_id=None)
                  .order_by(ControlRecord.id.desc()).all())
    first = next((r for r in candidates if _norm(r.product_name) == wanted), None)
    if not first:
        return None
    return first.followup or first


def _create_qar_for_result(rec, res):
    """Tworzy raport QAR dla punktu ocenionego jako NG i wiąże go z wynikiem."""
    from qar.routes import _next_qar_number
    extra = ' | '.join(x for x in (rec.product_name, rec.client) if x)
    kind = 'kontroli sprawdzającej' if rec.round == 2 else 'kontroli zamówienia'
    description = res.notes or f'Niezgodność (NG) stwierdzona podczas {kind}, punkt: {res.title}.'
    if extra:
        description += ' | Zlecenie: ' + extra
    if rec.department is not None:
        description += f' | Dział kontroli: {rec.department.name}'
    if res.element_name:
        description += f' | Element: {res.element_name}'
    emp = rec.employee or (rec.previous.employee if rec.previous is not None else None)
    if emp is not None:
        description += f' | Wykonawca: {emp.name} ({emp.department.name})'
    qar = QARReport(
        number=_next_qar_number(),
        zo_number=rec.zo_number,
        title=(f'Kontrola zamówienia NG: {res.title}'
               + (f' ({res.element_name})' if res.element_name else '')
               + f', ZO {rec.zo_number}')[:256],
        location=res.title[:128],
        description=description,
        user_id=current_user.id,
        employee_id=emp.id if emp is not None else None,
    )
    db.session.add(qar)
    db.session.flush()
    res.qar_report_id = qar.id
    return qar


def _employee_departments():
    """Aktywne działy do listy wykonawcy (z pracownikami grupowanymi w szablonie)."""
    return (ProductionDepartment.query.filter_by(is_active=True)
            .order_by(ProductionDepartment.order, ProductionDepartment.name).all())


def _parse_performer(rec, raw):
    """Wykonawca z formularza: puste pole zdejmuje wskazanie. Nowo można wskazać tylko aktywnego
    pracownika aktywnego działu, zapisany wcześniej pozostaje dozwolony. Zwraca (id albo None, błąd)."""
    raw = (raw or '').strip()
    if not raw:
        return None, None
    try:
        emp = db.session.get(DepartmentEmployee, int(raw))
    except (TypeError, ValueError):
        emp = None
    if emp is None:
        return rec.employee_id, 'Nie znaleziono wskazanego pracownika.'
    if rec.department_id is not None and emp.department_id != rec.department_id:
        return rec.employee_id, f'Wskaż pracownika z działu „{rec.department.name}”.'
    if emp.id != rec.employee_id and not (emp.is_active and emp.department.is_active):
        return rec.employee_id, 'Nie można wskazać nieaktywnego pracownika lub działu.'
    return emp.id, None


def _parse_department(raw):
    """Id istniejącego działu albo None (puste pole: punkt ogólny)."""
    try:
        dept = db.session.get(ProductionDepartment, int(raw))
    except (TypeError, ValueError):
        return None
    return dept.id if dept else None


def _parse_element(raw):
    """Id istniejącego elementu albo None (puste pole: punkt nie zależy od elementu)."""
    try:
        el = db.session.get(ControlElement, int(raw))
    except (TypeError, ValueError):
        return None
    return el.id if el else None


def _all_elements():
    return ControlElement.query.order_by(ControlElement.order, ControlElement.id).all()


def _sync_qar_employee(rec):
    """Uzupełnia pracownika w raportach QAR tej kontroli, które go jeszcze nie mają."""
    if rec.employee_id is None:
        return
    for res in rec.results:
        if res.qar_report is not None and res.qar_report.employee_id is None:
            res.qar_report.employee_id = rec.employee_id


def _apply_form(rec, form):
    """Zapisuje oceny punktów z formularza. Zwraca listę numerów utworzonych QAR."""
    created = []
    now = datetime.now(UTC)
    if 'employee_id' in form:
        emp_id, err = _parse_performer(rec, form.get('employee_id'))
        if err:
            flash(err, 'warning')
        else:
            rec.employee_id = emp_id
            db.session.flush()
            db.session.refresh(rec, ['employee'])
            _sync_qar_employee(rec)
    for res in rec.results:
        result = form.get(f'result_{res.id}') or None
        if result not in (None, 'OK', 'NG'):
            result = None
        notes = (form.get(f'notes_{res.id}') or '').strip() or None
        if (res.result, res.notes) != (result, notes):
            res.checked_at = now if result else None
        res.result, res.notes = result, notes
        if result == 'NG' and not res.qar_report_id:
            created.append(_create_qar_for_result(rec, res).number)
    return created


# ── Lista ─────────────────────────────────────────────────────────────────────

LIST_STATUSES = (
    ('in_progress', 'W trakcie'), ('completed', 'Zakończone'), ('new', 'Do skontrolowania'),
    ('to_do', 'Do wykonania'), ('recheck', 'Do kontroli sprawdzającej'))


class _Page:
    """Strona wyników z ręcznej paginacji (zlecenia i stare kontrole w jednej tabeli)."""

    def __init__(self, items, page, per_page, total):
        self.items, self.page, self.per_page, self.total = items, page, per_page, total
        self.pages = max(1, -(-total // per_page))
        self.has_prev, self.has_next = page > 1, page < self.pages
        self.prev_num, self.next_num = page - 1, page + 1


def _order_status(order, only_completed):
    ctrls = [c for c in order.controls if (c.status == 'completed' or not only_completed)]
    if any(c.status != 'completed' for c in ctrls):
        return 'in_progress'
    return 'completed' if ctrls else 'new'


def _legacy_status(rec):
    return {'in_control': 'in_progress', 'closed': 'completed'}.get(rec.state, rec.state)


def _parse_day(raw):
    try:
        return datetime.strptime((raw or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


@kontrola_bp.route('/')
@login_required
@view_required
def list_records():
    """Lista zleceń z kontrolami po działach oraz starych kontroli ogólnych, w jednej tabeli
    (układ jak lista Kontroli Końcowej: filtry, zaznaczanie, paginacja)."""
    is_op = current_user.is_operator
    zo_filter = request.args.get('zo', '').strip()
    dept_filter = request.args.get('dzial', type=int)
    element_filter = request.args.get('element', type=int)
    status_f = request.args.get('s')
    if status_f is None:
        status_f = 'to_do' if is_op else ''
    if status_f not in dict(LIST_STATUSES):
        status_f = ''
    date_from, date_to = request.args.get('date_from', ''), request.args.get('date_to', '')
    d_from, d_to = _parse_day(date_from), _parse_day(date_to)
    page = max(1, request.args.get('page', 1, type=int))
    per_page = request.args.get('per_page', 20, type=int)
    if per_page not in (10, 20, 25, 50, 100):
        per_page = 20

    def _by_employees(emp_ids):
        """Kontrole wstępne, w których wykonawcą jest jeden z pracowników (we wstępnej lub sprawdzającej)."""
        followups = db.session.query(ControlRecord.previous_id).filter(
            ControlRecord.employee_id.in_(emp_ids), ControlRecord.previous_id.isnot(None))
        return or_(ControlRecord.employee_id.in_(emp_ids), ControlRecord.id.in_(followups))

    # Stare kontrole ogólne (z cyklem) bez zlecenia oraz zlecenia z listy zleceń.
    q = ControlRecord.query.filter_by(round=1).filter(ControlRecord.control_order_id.is_(None))
    oq = ControlOrder.query
    if zo_filter:
        like = f'%{zo_filter}%'
        by_name = db.session.query(DepartmentEmployee.id).filter(DepartmentEmployee.name.ilike(like))
        oq = oq.filter(or_(ControlOrder.zo_number.ilike(like), ControlOrder.product_name.ilike(like),
                           ControlOrder.client.ilike(like),
                           ControlOrder.id.in_(db.session.query(ControlRecord.control_order_id).filter(
                               ControlRecord.control_order_id.isnot(None),
                               ControlRecord.employee_id.in_(by_name))),
                           ControlOrder.id.in_(db.session.query(ControlRecord.control_order_id).filter(
                               ControlRecord.control_order_id.isnot(None),
                               ControlRecord.id.in_(db.session.query(ControlRecordElement.record_id).filter(
                                   ControlRecordElement.name.ilike(like)))))))
        q = q.filter(or_(ControlRecord.zo_number.ilike(like),
                         ControlRecord.product_name.ilike(like),
                         ControlRecord.client.ilike(like),
                         _by_employees(by_name)))
    if dept_filter:
        in_dept = db.session.query(DepartmentEmployee.id).filter(
            DepartmentEmployee.department_id == dept_filter)
        q = q.filter(_by_employees(in_dept))
        oq = oq.filter(ControlOrder.id.in_(db.session.query(ControlRecord.control_order_id).filter(
            ControlRecord.control_order_id.isnot(None),
            or_(ControlRecord.department_id == dept_filter, ControlRecord.employee_id.in_(in_dept)))))

    if element_filter:
        oq = oq.filter(ControlOrder.id.in_(db.session.query(ControlRecord.control_order_id).filter(
            ControlRecord.control_order_id.isnot(None),
            ControlRecord.id.in_(db.session.query(ControlRecordElement.record_id).filter(
                ControlRecordElement.element_id == element_filter)))))
        q = q.filter(db.false())                       # stare kontrole ogólne nie mają elementów
    rows = []
    for o in oq.all():
        rows.append({'kind': 'order', 'obj': o, 'date': o.created_at, 'status': _order_status(o, is_op)})
    for r in q.all():
        if is_op and r.state == 'in_control':
            continue                                  # operator widzi dopiero zakończone kontrole
        rows.append({'kind': 'legacy', 'obj': r, 'date': r.created_at, 'status': _legacy_status(r)})
    if is_op:
        # Operator widzi zlecenia tylko z zakończonymi kontrolami (odczyt).
        rows = [x for x in rows if x['kind'] == 'legacy' or any(c.status == 'completed' for c in x['obj'].controls)]
    if status_f:
        rows = [x for x in rows if x['status'] == status_f]
    if d_from:
        rows = [x for x in rows if x['date'] and x['date'].date() >= d_from]
    if d_to:
        rows = [x for x in rows if x['date'] and x['date'].date() <= d_to]
    rows.sort(key=lambda x: (x['date'] or datetime.min, x['obj'].id), reverse=True)
    total = len(rows)
    page = min(page, max(1, -(-total // per_page)))
    pager = _Page(rows[(page - 1) * per_page: page * per_page], page, per_page, total)

    filters = {'zo': zo_filter, 's': status_f, 'dzial': dept_filter or '', 'element': element_filter or '', 'date_from': date_from,
               'date_to': date_to, 'per_page': per_page}
    return render_template('kontrola/list.html', pager=pager, filters=filters, per_page=per_page,
                           statuses=LIST_STATUSES, departments=_employee_departments(), elements=_all_elements(),
                           labels=CONTROL_STATE_LABELS, is_op=is_op)


# ── Skan QR (ten sam kod co kontrola końcowa: p, c, o, q) ────────────────────

@kontrola_bp.route('/scan')
@login_required
@controller_required
def scan_page():
    return render_template('kontrola/scan.html')


@kontrola_bp.route('/qr-lookup', methods=['POST'])
@login_required
@controller_required
def qr_lookup():
    """Po zeskanowaniu kodu: czy zlecenie jest już na liście (od tego zależy napis przycisku na ekranie skanu).
    `mode`: `orders` (są działy z punktami, skan dodaje zlecenie do listy) albo `legacy` (skan tworzy kontrolę ogólną)."""
    data = request.get_json(silent=True) or {}
    zo = _clean_zo(data.get('o'))
    product = (data.get('p') or '').strip()
    if _control_departments():
        exists = bool(zo) and _find_order(zo, product) is not None
        return jsonify({'ok': True, 'mode': 'orders', 'exists': exists})
    exists = bool(zo) and _latest_for_order(zo, product) is not None
    return jsonify({'ok': True, 'mode': 'legacy', 'exists': exists})


@kontrola_bp.route('/from-qr', methods=['POST'])
@login_required
@controller_required
def from_qr():
    data         = request.get_json(silent=True) or {}
    product_name = (data.get('p') or '').strip()
    client       = (data.get('c') or '').strip()
    zo           = _clean_zo(data.get('o'))
    try:
        quantity = max(1, min(99, int(data.get('q', 1))))
    except (TypeError, ValueError):
        quantity = 1

    if not zo:
        return jsonify({'error': 'Brak numeru zlecenia („o") w kodzie QR'}), 400

    if _control_departments():
        # Kontrole po działach: skan dodaje zlecenie do listy (ponowny skan otwiera istniejące).
        order, created = _get_or_create_order(zo, product_name, client, quantity)
        db.session.commit()
        _audit('kontrola_order_scan', order.id, f'ZO={zo} product={product_name} nowe={created}')
        if created:
            # Pierwszy skan: wpis ze statusem "Do skontrolowania" i powrót do listy.
            msg = f'Dodano do listy do skontrolowania: {_label(order)}.'
            flash(msg, 'success')
            return jsonify({'ok': True, 'redirect': url_for('kontrola.list_records', s=''), 'msg': msg})
        return jsonify({
            'ok': True,
            'redirect': url_for('kontrola.order_page', order_id=order.id),
            'msg': f'Zlecenie {_label(order)} jest już na liście, otwieram.',
        })

    existing = _latest_for_order(zo, product_name)
    if existing:
        _audit('kontrola_scan_existing', existing.id, f'ZO={zo} product={product_name}')
        return jsonify({
            'ok': True,
            'redirect': url_for('kontrola.view_record', record_id=existing.id),
            'msg': f'Kontrola dla {_label(existing)} już istnieje, otwieram.',
        })

    points = _active_points()
    if not points:
        return jsonify({'error': 'Brak punktów kontrolnych. Administrator musi je dodać w '
                                 'ustawieniach kontroli zamówień.'}), 400

    rec = ControlRecord(zo_number=zo, product_name=product_name or None,
                        client=client or None, quantity=quantity,
                        round=1, user_id=current_user.id)
    db.session.add(rec)
    db.session.flush()
    _add_results(rec, points)
    db.session.commit()
    _audit('kontrola_create', rec.id, f'ZO={zo} product={product_name} (QR)')
    return jsonify({
        'ok': True,
        'redirect': url_for('kontrola.view_record', record_id=rec.id),
        'msg': 'Utworzono kontrolę zamówienia.',
    })


# ── Zlecenia na liście i kontrole po działach ────────────────────────────────

def _order_args(source):
    """Dane zlecenia z kodu QR (zo, produkt, klient, sztuki)."""
    zo = _clean_zo(source.get('zo') or source.get('o'))
    product = (source.get('p') or '').strip()
    client = (source.get('c') or '').strip()
    try:
        qty = max(1, min(99, int(source.get('q', 1))))
    except (TypeError, ValueError):
        qty = 1
    return zo, product, client, qty


def _find_order(zo, product_name):
    """Zlecenie z listy dla pary ZO + produkt (bez wielkości liter i nadmiarowych spacji) albo None."""
    wanted = _norm(product_name)
    for o in ControlOrder.query.filter_by(zo_number=zo).order_by(ControlOrder.id.desc()).all():
        if _norm(o.product_name) == wanted:
            return o
    return None


def _get_or_create_order(zo, product, client, qty):
    """Zlecenie z listy (tworzone przy pierwszym skanie). Zwraca (zlecenie, czy nowe). Bez commitu."""
    order = _find_order(zo, product)
    if order is not None:
        return order, False
    order = ControlOrder(zo_number=zo, product_name=product or None, client=client or None,
                         quantity=qty, created_by_id=current_user.id)
    db.session.add(order)
    db.session.flush()
    return order, True


@kontrola_bp.route('/zlecenie/<int:order_id>')
@login_required
@controller_required
def order_page(order_id):
    order = get_or_404(ControlOrder, order_id)
    latest = {}
    for c in order.controls:                 # od najnowszej: pierwsza zakończona dla działu jest aktualna
        if c.status == 'completed' and c.department_id not in latest:
            latest[c.department_id] = c.id
    return render_template('kontrola/order_page.html', order=order, departments=_control_departments(),
                           latest=latest, elements=_active_elements())


@kontrola_bp.route('/zlecenie/nowe', methods=['POST'])
@login_required
@controller_required
def order_create():
    """Ręczne dodanie zlecenia do listy (bez skanu QR). Istniejące zlecenie (ZO + produkt) jest otwierane."""
    zo, product, client, qty = _order_args(request.form)
    if not zo:
        flash('Podaj numer zlecenia (ZO).', 'warning')
        return redirect(url_for('kontrola.list_records'))
    if not product:
        flash('Podaj nazwę produktu.', 'warning')
        return redirect(url_for('kontrola.list_records'))
    order, created = _get_or_create_order(zo, product, client, qty)
    db.session.commit()
    _audit('kontrola_order_create', order.id, f'ZO={zo} product={product} nowe={created}')
    flash('Dodano zlecenie do listy.' if created else 'To zlecenie jest już na liście, otwieram.', 'success')
    return redirect(url_for('kontrola.order_page', order_id=order.id))


def _delete_orders(orders):
    """Usuwa zlecenia z listy razem z ich kontrolami (raporty QAR zostają). Zwraca liczbę zleceń."""
    for order in orders:
        for rec in list(order.controls):
            db.session.delete(rec)
        db.session.flush()
        db.session.delete(order)
    db.session.commit()
    return len(orders)


@kontrola_bp.route('/zlecenie/usun-zaznaczone', methods=['POST'])
@login_required
@admin_required
def orders_bulk_delete():
    ids = request.form.getlist('order_ids', type=int)
    rec_ids = request.form.getlist('record_ids', type=int)
    orders = ControlOrder.query.filter(ControlOrder.id.in_(ids)).all() if ids else []
    records = ControlRecord.query.filter(ControlRecord.id.in_(rec_ids)).all() if rec_ids else []
    if not orders and not records:
        flash('Nie zaznaczono żadnych zleceń.', 'warning')
        return redirect(url_for('kontrola.list_records'))
    count = _delete_orders(orders) + (_delete_cycles(records) if records else 0)
    _audit('kontrola_order_bulk_delete', None, f'count={count}')
    flash(f'Usunięto zlecenia z listy: {count}.', 'success')
    return redirect(url_for('kontrola.list_records'))


@kontrola_bp.route('/zlecenie/<int:order_id>/usun', methods=['POST'])
@login_required
@admin_required
def order_delete(order_id):
    """Admin usuwa zlecenie z listy razem z jego kontrolami (raporty QAR zostają)."""
    order = get_or_404(ControlOrder, order_id)
    zo = order.zo_number
    _delete_orders([order])
    _audit('kontrola_order_delete', None, f'ZO={zo}')
    flash(f'Usunięto zlecenie ZO {zo} z listy.', 'success')
    return redirect(url_for('kontrola.list_records'))


@kontrola_bp.route('/zlecenie/<int:order_id>/start', methods=['POST'])
@login_required
@controller_required
def order_start(order_id):
    """Nowa albo ponowna kontrola działu dla zlecenia. Rozpoczęta, niezakończona kontrola tego działu
    zostaje otwarta zamiast tworzenia drugiej."""
    order = get_or_404(ControlOrder, order_id)
    back = url_for('kontrola.order_page', order_id=order.id)
    raw = request.form.get('department_id', '').strip()
    dept = next((d for d in _control_departments() if str(d.id) == raw), None)
    if dept is None:
        flash('Wybierz dział kontroli (dział z przypisanymi punktami).', 'warning')
        return redirect(back)
    elements, err = _parse_elements(request.form.getlist('element_ids'))
    if err:
        flash(err, 'warning')
        return redirect(back)
    wanted = {e.id for e in elements}
    for cand in ControlRecord.query.filter_by(control_order_id=order.id, department_id=dept.id,
                                              status='in_progress').all():
        if cand.element_ids == wanted:             # ta sama kontrola w toku: otwieramy zamiast tworzyć drugą
            return redirect(url_for('kontrola.view_record', record_id=cand.id))
    if not _active_points(dept.id) and not any(_element_points(e.id, dept.id) for e in elements):
        flash('Ten dział nie ma punktów kontrolnych.', 'warning')
        return redirect(back)
    rec = ControlRecord(zo_number=order.zo_number, product_name=order.product_name, client=order.client,
                        quantity=order.quantity, round=1, user_id=current_user.id, department_id=dept.id,
                        control_order_id=order.id)
    db.session.add(rec)
    db.session.flush()
    _add_department_results(rec, dept.id, elements)
    db.session.commit()
    _audit('kontrola_create', rec.id, f'ZO={order.zo_number} product={order.product_name} dzial={dept.id} '
                                      f'elementy={[e.name for e in elements]}')
    return redirect(url_for('kontrola.view_record', record_id=rec.id))


# ── Podgląd i wypełnianie ─────────────────────────────────────────────────────

@kontrola_bp.route('/<int:record_id>', methods=['GET', 'POST'])
@login_required
@view_required
def view_record(record_id):
    rec = get_or_404(ControlRecord, record_id)
    if current_user.is_operator and rec.round == 1 and rec.state == 'in_control':
        abort(403)

    can_edit = current_user.is_kontroler and rec.status == 'in_progress'

    if request.method == 'POST':
        if not can_edit:
            abort(403)
        created = _apply_form(rec, request.form)
        complete = request.form.get('action') == 'complete'
        if complete and rec.rated_count < len(rec.results):
            complete = False
            flash('Oceń wszystkie punkty (OK lub NG), aby zakończyć kontrolę.', 'warning')
        elif not complete:
            flash('Zapisano.', 'success')
        if complete:
            rec.status = 'completed'
            rec.completed_at = datetime.now(UTC)
            if rec.round == 1 and rec.department_id is None:
                _notify(['operator'],
                        f'{_label(rec)}: kontrola zakończona ({rec.result}), '
                        f'zamówienie czeka na wykonanie.',
                        'urgent' if rec.result == 'NG' else 'info')
        db.session.commit()
        _audit('kontrola_complete' if complete else 'kontrola_save', rec.id,
               f'ZO={rec.zo_number} runda={rec.round}')
        if created:
            flash('Utworzono raporty QAR: ' + ', '.join(created) + '.', 'warning')
        if complete:
            flash('Kontrola zakończona. Wynik: ' + rec.result + '.', 'success')
            if rec.control_order_id is not None:
                return redirect(url_for('kontrola.order_page', order_id=rec.control_order_id))
            return redirect(url_for('kontrola.list_records'))
        return redirect(url_for('kontrola.view_record', record_id=rec.id))

    return render_template('kontrola/detail.html', rec=rec, can_edit=can_edit,
                           labels=CONTROL_STATE_LABELS, departments=_employee_departments())


# ── Operator: Wykonane -> kontrola sprawdzająca ───────────────────────────────

@kontrola_bp.route('/<int:record_id>/done', methods=['POST'])
@login_required
@operator_required
def mark_done(record_id):
    rec = get_or_404(ControlRecord, record_id)
    if rec.round != 1 or rec.status != 'completed' or rec.done_at or rec.department_id is not None:
        flash('To zamówienie nie czeka na wykonanie.', 'warning')
        return redirect(url_for('kontrola.list_records'))

    rec.done_at = datetime.now(UTC)
    rec.done_by_id = current_user.id
    follow = ControlRecord(zo_number=rec.zo_number, product_name=rec.product_name,
                           client=rec.client, quantity=rec.quantity, round=2,
                           previous_id=rec.id, user_id=rec.user_id, employee_id=rec.employee_id)
    db.session.add(follow)
    db.session.flush()
    # Kontrola sprawdzająca używa aktualnej listy punktów; gdyby była pusta,
    # kopiujemy punkty z kontroli wstępnej.
    _add_results(follow, _active_points() or rec.results)
    _notify(['admin', 'kontroler'],
            f'{_label(rec)}: oznaczone jako wykonane przez {current_user.username}, '
            f'czeka na kontrolę sprawdzającą.')
    db.session.commit()
    _audit('kontrola_done', rec.id, f'ZO={rec.zo_number}')
    flash(f'{_label(rec)} oznaczone jako wykonane. Utworzono kontrolę sprawdzającą.', 'success')
    return redirect(url_for('kontrola.list_records'))


@kontrola_bp.route('/<int:record_id>/reopen', methods=['POST'])
@login_required
@admin_required
def reopen(record_id):
    """Admin cofa zakończenie kontroli (pomyłka kontrolera). Nie dotyczy kontroli
    wstępnej, po której operator już oznaczył wykonanie."""
    rec = get_or_404(ControlRecord, record_id)
    if rec.status != 'completed' or (rec.round == 1 and rec.done_at):
        flash('Tej kontroli nie można wznowić.', 'warning')
    else:
        rec.status = 'in_progress'
        rec.completed_at = None
        db.session.commit()
        _audit('kontrola_reopen', rec.id, f'ZO={rec.zo_number} runda={rec.round}')
        flash('Kontrola wznowiona.', 'success')
    return redirect(url_for('kontrola.view_record', record_id=rec.id))


def _delete_cycles(records):
    """Usuwa całe cykle kontroli (wstępną razem ze sprawdzającą). Raporty QAR
    utworzone z NG zostają, bo to osobne dokumenty niezgodności. Zwraca liczbę cykli."""
    roots = {r.previous or r for r in records}
    for root in roots:
        if root.followup is not None:
            db.session.delete(root.followup)
            db.session.flush()
        db.session.delete(root)
    db.session.commit()
    return len(roots)


@kontrola_bp.route('/<int:record_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_record(record_id):
    rec = get_or_404(ControlRecord, record_id)
    zo = rec.zo_number
    order_id = rec.control_order_id
    _delete_cycles([rec])
    _audit('kontrola_delete', None, f'ZO={zo}')
    flash(f'Usunięto kontrolę zamówienia ZO {zo}.', 'success')
    if order_id is not None:
        return redirect(url_for('kontrola.order_page', order_id=order_id))
    return redirect(url_for('kontrola.list_records'))


@kontrola_bp.route('/bulk-delete', methods=['POST'])
@login_required
@admin_required
def bulk_delete():
    ids = request.form.getlist('record_ids', type=int)
    records = ControlRecord.query.filter(ControlRecord.id.in_(ids)).all() if ids else []
    if not records:
        flash('Nie zaznaczono żadnych kontroli.', 'warning')
        return redirect(url_for('kontrola.list_records'))
    count = _delete_cycles(records)
    _audit('kontrola_bulk_delete', None, f'count={count}')
    flash(f'Usunięto kontrole zamówień: {count}.', 'success')
    return redirect(url_for('kontrola.list_records'))


# ── Admin: elementy kontroli ──────────────────────────────────────────────────

def _element_usage(el):
    """Ile punktów i kontroli używa elementu."""
    return {'points': ControlPoint.query.filter_by(element_id=el.id).count(),
            'records': ControlRecordElement.query.filter_by(element_id=el.id).count()}


@kontrola_bp.route('/elementy')
@login_required
@admin_required
def elements():
    items = _all_elements()
    return render_template('kontrola/elements.html', elements=items,
                           usage={e.id: _element_usage(e) for e in items})


@kontrola_bp.route('/elementy/dodaj', methods=['POST'])
@login_required
@admin_required
def element_add():
    name = ' '.join(request.form.get('name', '').split())
    if not name:
        flash('Nazwa elementu jest wymagana.', 'warning')
    elif ControlElement.query.filter(func.lower(ControlElement.name) == name.lower()).first():
        flash(f'Element „{name}” już istnieje.', 'warning')
    else:
        order = (db.session.query(func.max(ControlElement.order)).scalar() or 0) + 1
        db.session.add(ControlElement(name=name[:128], order=order))
        db.session.commit()
        _audit('kontrola_element_add', None, name)
        flash(f'Dodano element „{name}”.', 'success')
    return redirect(url_for('kontrola.elements'))


@kontrola_bp.route('/elementy/<int:element_id>/zapisz', methods=['POST'])
@login_required
@admin_required
def element_save(element_id):
    el = get_or_404(ControlElement, element_id)
    name = ' '.join(request.form.get('name', '').split())
    other = (ControlElement.query.filter(func.lower(ControlElement.name) == name.lower(),
                                         ControlElement.id != el.id).first() if name else None)
    if not name:
        flash('Nazwa elementu jest wymagana.', 'warning')
    elif other:
        flash(f'Element „{name}” już istnieje.', 'warning')
    else:
        el.name = name[:128]
        el.is_active = request.form.get('is_active') == '1'
        db.session.commit()
        _audit('kontrola_element_edit', None, f'{el.id}: {el.name}')
        flash('Zapisano element.', 'success')
    return redirect(url_for('kontrola.elements'))


def _apply_elements_form(form):
    """Zapisuje wszystkie elementy z jednego formularza (`name_<id>`, `active_<id>`) i nowy z `new_name`.
    Zwraca (liczba zmienionych, czy dodano, błędy). Bez commitu."""
    ids = _ids_with_prefix(form, 'name_')
    els = {e.id: e for e in ControlElement.query.filter(ControlElement.id.in_(ids)).all()} if ids else {}
    taken = {e.name.lower() for e in ControlElement.query.all()}
    changed, errors = 0, []
    renamed = {}
    for eid in ids:
        el = els.get(eid)
        if el is None:
            continue
        name = ' '.join(form.get(f'name_{eid}', '').split())
        if not name:
            errors.append(f'Element nr {eid}: nazwa jest wymagana, zmiany pominięto.')
            continue
        others = {e.name.lower() for e in els.values() if e.id != eid} | {
            n.lower() for i, n in renamed.items() if i != eid}
        outside = {e.name.lower() for e in ControlElement.query.filter(~ControlElement.id.in_(ids)).all()}
        if name.lower() in others | outside:
            errors.append(f'Element „{name}” już istnieje, zmiany pominięto.')
            continue
        active = form.get(f'active_{eid}') == '1'
        if el.name != name[:128] or bool(el.is_active) != active:
            el.name, el.is_active = name[:128], active
            changed += 1
        renamed[eid] = name
    added = False
    new_name = ' '.join(form.get('new_name', '').split())
    if new_name:
        if new_name.lower() in taken | {n.lower() for n in renamed.values()}:
            errors.append(f'Element „{new_name}” już istnieje, nowego nie dodano.')
        else:
            order = (db.session.query(func.max(ControlElement.order)).scalar() or 0) + 1
            db.session.add(ControlElement(name=new_name[:128], order=order))
            added = True
    return changed, added, errors


@kontrola_bp.route('/elementy/zapisz-wszystko', methods=['POST'])
@login_required
@admin_required
def elements_save_all():
    """Jeden zapis wszystkich zmian na stronie elementów: edycje istniejących i nowy element."""
    changed, added, errors = _apply_elements_form(request.form)
    db.session.commit()
    for e in errors:
        flash(e, 'warning')
    if changed or added:
        _audit('kontrola_elements_save_all', None, f'zmienione={changed} nowy={added}')
        parts = []
        if changed:
            parts.append(f'zapisano zmiany w elementach: {changed}')
        if added:
            parts.append('dodano nowy element')
        flash((', '.join(parts)).capitalize() + '.', 'success')
    elif not errors:
        flash('Brak zmian do zapisania.', 'info')
    return redirect(url_for('kontrola.elements'))


@kontrola_bp.route('/elementy/<int:element_id>/przesun/<direction>', methods=['POST'])
@login_required
@admin_required
def element_move(element_id, direction):
    if request.form.get('save_first'):
        _apply_elements_form(request.form)        # niezapisane zmiany nie giną przy przesuwaniu
        db.session.flush()
    items = _all_elements()
    idx = next((i for i, e in enumerate(items) if e.id == element_id), None)
    if idx is None:
        abort(404)
    swap = idx - 1 if direction == 'up' else idx + 1
    if 0 <= swap < len(items):
        items[idx], items[swap] = items[swap], items[idx]
    for i, e in enumerate(items, 1):
        e.order = i
    db.session.commit()
    return redirect(url_for('kontrola.elements'))


@kontrola_bp.route('/elementy/<int:element_id>/usun', methods=['POST'])
@login_required
@admin_required
def element_delete(element_id):
    """Usunięcie elementu. Element z przypisanymi punktami nie może zniknąć (punkty zmieniłyby zasięg),
    w zakończonych i rozpoczętych kontrolach zostaje kopia nazwy."""
    el = get_or_404(ControlElement, element_id)
    if request.form.get('save_first'):
        _apply_elements_form({k: v for k, v in request.form.items() if k != f'name_{element_id}'})
        db.session.flush()
    if ControlPoint.query.filter_by(element_id=el.id).first():
        db.session.commit()                      # zachowaj niezapisane zmiany z formularza
        flash(f'Nie można usunąć elementu „{el.name}”: ma przypisane punkty kontrolne. '
              f'Zmień punkty albo dezaktywuj element.', 'warning')
        return redirect(url_for('kontrola.elements'))
    name = el.name
    ControlRecordElement.query.filter_by(element_id=el.id).update({'element_id': None})
    ControlResult.query.filter_by(element_id=el.id).update({'element_id': None})
    db.session.delete(el)
    db.session.commit()
    _audit('kontrola_element_delete', None, name)
    flash(f'Usunięto element „{name}”.', 'success')
    return redirect(url_for('kontrola.elements'))


# ── Admin: punkty kontrolne ───────────────────────────────────────────────────

@kontrola_bp.route('/points')
@login_required
@admin_required
def points():
    dept_filter = request.args.get('dzial', type=int)
    element_filter = request.args.get('element', type=int)
    q = ControlPoint.query
    if dept_filter == -1:
        q = q.filter(ControlPoint.department_id.is_(None))
    elif dept_filter:
        q = q.filter(ControlPoint.department_id == dept_filter)
    if element_filter == -1:
        q = q.filter(ControlPoint.element_id.is_(None))
    elif element_filter:
        q = q.filter(ControlPoint.element_id == element_filter)
    pts = q.order_by(ControlPoint.order, ControlPoint.id).all()
    return render_template('kontrola/points.html', points=pts, departments=_employee_departments(),
                           dept_filter=dept_filter, elements=_all_elements(), element_filter=element_filter)


@kontrola_bp.route('/points/add', methods=['POST'])
@login_required
@admin_required
def point_add():
    title = request.form.get('title', '').strip()
    if not title:
        flash('Nazwa punktu jest wymagana.', 'error')
        return redirect(url_for('kontrola.points'))
    max_order = db.session.query(func.max(ControlPoint.order)).scalar() or 0
    db.session.add(ControlPoint(title=title[:256], order=max_order + 1,
                                description=request.form.get('description', '').strip() or None,
                                department_id=_parse_department(request.form.get('department_id')),
                                element_id=_parse_element(request.form.get('element_id'))))
    db.session.commit()
    _audit('kontrola_point_add', None, title)
    flash('Dodano punkt kontrolny.', 'success')
    return redirect(url_for('kontrola.points'))


@kontrola_bp.route('/points/<int:point_id>/save', methods=['POST'])
@login_required
@admin_required
def point_save(point_id):
    pt = get_or_404(ControlPoint, point_id)
    title = request.form.get('title', '').strip()
    if not title:
        flash('Nazwa punktu jest wymagana.', 'error')
        return redirect(url_for('kontrola.points'))
    pt.title = title[:256]
    pt.description = request.form.get('description', '').strip() or None
    pt.is_active = request.form.get('is_active') == '1'
    if 'department_id' in request.form:
        pt.department_id = _parse_department(request.form.get('department_id'))
    if 'element_id' in request.form:
        pt.element_id = _parse_element(request.form.get('element_id'))
    db.session.commit()
    _audit('kontrola_point_edit', None, f'{pt.id}: {pt.title}')
    flash('Zapisano punkt kontrolny.', 'success')
    return redirect(url_for('kontrola.points'))


def _ids_with_prefix(form, prefix):
    return sorted({int(k[len(prefix):]) for k in form if k.startswith(prefix) and k[len(prefix):].isdigit()})


def _apply_points_form(form):
    """Zapisuje wszystkie punkty z jednego formularza (pola `title_<id>`, `description_<id>`, `department_<id>`,
    `element_<id>`, `active_<id>`) oraz nowy punkt z pól `new_*`, jeśli ma nazwę. Zwraca
    (liczba zmienionych, czy dodano nowy, lista błędów). Bez commitu."""
    ids = _ids_with_prefix(form, 'title_')
    pts = {p.id: p for p in ControlPoint.query.filter(ControlPoint.id.in_(ids)).all()} if ids else {}
    changed, errors = 0, []
    for pid in ids:
        pt = pts.get(pid)
        if pt is None:
            continue
        title = form.get(f'title_{pid}', '').strip()
        if not title:
            errors.append(f'Punkt nr {pid}: nazwa jest wymagana, zmiany tego punktu pominięto.')
            continue
        new = {'title': title[:256],
               'description': form.get(f'description_{pid}', '').strip() or None,
               'is_active': form.get(f'active_{pid}') == '1',
               'department_id': _parse_department(form.get(f'department_{pid}')),
               'element_id': _parse_element(form.get(f'element_{pid}'))}
        if any(getattr(pt, k) != v for k, v in new.items()):
            for k, v in new.items():
                setattr(pt, k, v)
            changed += 1
    added = False
    new_title = form.get('new_title', '').strip()
    if new_title:
        max_order = db.session.query(func.max(ControlPoint.order)).scalar() or 0
        db.session.add(ControlPoint(title=new_title[:256], order=max_order + 1,
                                    description=form.get('new_description', '').strip() or None,
                                    department_id=_parse_department(form.get('new_department')),
                                    element_id=_parse_element(form.get('new_element'))))
        added = True
    return changed, added, errors


def _back_to_points():
    return redirect(url_for('kontrola.points', dzial=request.form.get('back_dzial') or None,
                            element=request.form.get('back_element') or None))


@kontrola_bp.route('/points/zapisz-wszystko', methods=['POST'])
@login_required
@admin_required
def points_save_all():
    """Jeden zapis wszystkich zmian na stronie punktów: edycje istniejących i nowy punkt."""
    changed, added, errors = _apply_points_form(request.form)
    db.session.commit()
    for e in errors:
        flash(e, 'warning')
    if changed or added:
        _audit('kontrola_points_save_all', None, f'zmienione={changed} nowy={added}')
        parts = []
        if changed:
            parts.append(f'zapisano zmiany w punktach: {changed}')
        if added:
            parts.append('dodano nowy punkt')
        flash((', '.join(parts)).capitalize() + '.', 'success')
    elif not errors:
        flash('Brak zmian do zapisania.', 'info')
    return _back_to_points()


@kontrola_bp.route('/points/<int:point_id>/move/<direction>', methods=['POST'])
@login_required
@admin_required
def point_move(point_id, direction):
    if request.form.get('save_first'):
        _apply_points_form(request.form)          # niezapisane zmiany nie giną przy przesuwaniu
        db.session.flush()
    pts = ControlPoint.query.order_by(ControlPoint.order, ControlPoint.id).all()
    idx = next((i for i, p in enumerate(pts) if p.id == point_id), None)
    if idx is None:
        abort(404)
    swap = idx - 1 if direction == 'up' else idx + 1
    if 0 <= swap < len(pts):
        pts[idx], pts[swap] = pts[swap], pts[idx]
    for i, p in enumerate(pts, 1):
        p.order = i
    db.session.commit()
    return _back_to_points() if request.form.get('save_first') else redirect(url_for('kontrola.points'))


@kontrola_bp.route('/points/<int:point_id>/delete', methods=['POST'])
@login_required
@admin_required
def point_delete(point_id):
    pt = get_or_404(ControlPoint, point_id)
    if request.form.get('save_first'):
        _apply_points_form({k: v for k, v in request.form.items() if k != f'title_{point_id}'})
        db.session.flush()
    # Historia zostaje: wyniki mają własną kopię tytułu.
    ControlResult.query.filter_by(point_id=pt.id).update({'point_id': None})
    db.session.delete(pt)
    db.session.commit()
    _audit('kontrola_point_delete', None, f'{point_id}: {pt.title}')
    flash('Usunięto punkt kontrolny.', 'success')
    return _back_to_points() if request.form.get('save_first') else redirect(url_for('kontrola.points'))
