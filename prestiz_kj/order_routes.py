"""Zlecenia demo: lista (standard list), strona zlecenia z trasą, materiałami i osią czasu, meldunek operacji,
ręczne tworzenie i usuwanie (pojedyncze i zbiorcze)."""
from datetime import datetime

from flask import render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import db
from . import prestiz_kj_bp
from .access import view_required, controller_required, admin_required, audit
from .models import PkjOperation, PkjOrder, PkjEmployee, PkjEvent, PkjInspection, PkjInspectionMaterial
from . import orders as ord_
from . import repairs
from .models import PkjReturn

STATUS_LABELS = {'nowe': 'Nowe', 'w_produkcji': 'W produkcji', 'do_kontroli': 'Do kontroli',
                 'zakonczone': 'Zakończone', 'zamkniete': 'Zamknięte'}
PER_PAGE_CHOICES = (10, 20, 25, 50, 100)


def _day(raw):
    try:
        return datetime.strptime((raw or '').strip(), '%Y-%m-%d')
    except ValueError:
        return None


@prestiz_kj_bp.route('/zlecenia')
@login_required
@view_required
def orders_list():
    q = request.args.get('q', '').strip()
    status = request.args.get('s', '')
    date_from, date_to = request.args.get('od', ''), request.args.get('do', '')
    per_page = request.args.get('per_page', 20, type=int)
    if per_page not in PER_PAGE_CHOICES:
        per_page = 20
    typ = request.args.get('typ')
    if typ not in ('zp', 'zn', 'all'):
        typ = 'all' if q else 'zp'              # domyślnie tylko produkcyjne, wyszukiwanie sięga też po ZN
    query = PkjOrder.query
    if typ != 'all':
        query = query.filter(PkjOrder.type == typ.upper())
    if q:
        like = f'%{q}%'
        query = query.filter(or_(PkjOrder.number.ilike(like), PkjOrder.product.ilike(like),
                                 PkjOrder.client.ilike(like)))
    if status in STATUS_LABELS:
        query = query.filter(PkjOrder.status == status)
    else:
        status = ''
    d_from, d_to = _day(date_from), _day(date_to)
    if d_from:
        query = query.filter(PkjOrder.created_at >= d_from)
    if d_to:
        query = query.filter(PkjOrder.created_at <= d_to.replace(hour=23, minute=59, second=59))
    pager = query.order_by(PkjOrder.created_at.desc(), PkjOrder.id.desc()).paginate(
        page=request.args.get('page', 1, type=int), per_page=per_page, error_out=False)
    filters = {'q': q, 's': status, 'od': date_from, 'do': date_to, 'typ': typ}
    return render_template('prestiz_kj/zlecenia.html', pager=pager, filters=filters, per_page=per_page,
                           statuses=STATUS_LABELS, per_page_choices=PER_PAGE_CHOICES,
                           summaries=ord_.summaries(pager.items),
                           operations=PkjOperation.query.filter_by(is_active=True)
                           .order_by(PkjOperation.order, PkjOperation.id).all())


@prestiz_kj_bp.route('/zlecenia/<int:order_id>')
@login_required
@view_required
def order_page(order_id):
    order = db.session.get(PkjOrder, order_id) or abort(404)
    ops = ord_.operations_of(order.id)
    blocked = {o.id: ord_.blocking_reason(o, ops) for o in ops}
    events = PkjEvent.query.filter_by(order_id=order.id).order_by(PkjEvent.ts, PkjEvent.id).all()
    inspections = PkjInspection.query.filter_by(order_id=order.id).order_by(PkjInspection.id).all()
    imats = {}
    for m in PkjInspectionMaterial.query.filter(PkjInspectionMaterial.inspection_id.in_(
            [i.id for i in inspections])).all() if inspections else []:
        imats.setdefault(m.inspection_id, []).append(m.name)
    rets = repairs.returns_of_order(order.id)
    zns = {r.id: db.session.get(PkjOrder, r.repair_order_id) for r in rets if r.repair_order_id}
    source = db.session.get(PkjReturn, order.source_return_id) if order.source_return_id else None
    return render_template('prestiz_kj/zlecenie.html', order=order, ops=ops, blocked=blocked,
                           inspections=inspections, imats=imats, returns=rets, zns=zns, source=source,
                           ret_labels=repairs.RETURN_STATUS_LABELS, decisions=repairs.DECISIONS,
                           mat_names={m.id: m.material_name for m in ord_.materials_of(order.id)},
                           materials=ord_.materials_of(order.id), events=events,
                           employees=ord_.active_employees(),
                           parent=db.session.get(PkjOrder, order.parent_id) if order.parent_id else None)


@prestiz_kj_bp.route('/zlecenia/nowe', methods=['POST'])
@login_required
@admin_required
def order_create():
    ids = [int(i) for i in request.form.getlist('op') if i.isdigit()]
    try:
        order = ord_.create_order(request.form.get('number'), request.form.get('product'),
                                  request.form.get('client'), request.form.get('quantity'), ids or None,
                                  actor=current_user.username)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
        return redirect(url_for('prestiz_kj.orders_list'))
    db.session.commit()
    audit('pkj_order_create', order.id, order.number)
    flash(f'Dodano zlecenie {order.number}.', 'success')
    return redirect(url_for('prestiz_kj.order_page', order_id=order.id))


@prestiz_kj_bp.route('/zlecenia/<int:order_id>/melduj', methods=['POST'])
@login_required
@controller_required
def order_report(order_id):
    order = db.session.get(PkjOrder, order_id) or abort(404)
    oop_id = request.form.get('oop_id', type=int)
    oop = next((o for o in ord_.operations_of(order.id) if o.id == oop_id), None)
    if oop is None:
        abort(404)
    employee = db.session.get(PkjEmployee, request.form.get('employee_id', type=int) or 0)
    try:
        ord_.report_operation(oop, employee, actor=current_user.username)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    else:
        db.session.commit()
        audit('pkj_order_report', order.id, f'{order.number}: {oop.operation_name}')
        flash(f'Zameldowano operację {oop.operation_name}.'
              + (' RW na materiały wstrzymane do kontroli.' if oop.status == 'do_kontroli' else ''), 'success')
    return redirect(url_for('prestiz_kj.order_page', order_id=order.id))


@prestiz_kj_bp.route('/zlecenia/<int:order_id>/usun', methods=['POST'])
@login_required
@admin_required
def order_delete(order_id):
    order = db.session.get(PkjOrder, order_id) or abort(404)
    number = order.number
    ord_.delete_orders([order.id])
    audit('pkj_order_delete', order_id, number)
    flash(f'Usunięto zlecenie {number}.', 'success')
    return redirect(url_for('prestiz_kj.orders_list'))


@prestiz_kj_bp.route('/zlecenia/usun-zaznaczone', methods=['POST'])
@login_required
@admin_required
def orders_bulk_delete():
    ids = [int(i) for i in request.form.getlist('order_ids') if i.isdigit()]
    if not ids:
        flash('Nie zaznaczono żadnych zleceń.', 'info')
    else:
        count = ord_.delete_orders(ids)
        audit('pkj_orders_bulk_delete', None, f'{count} zleceń')
        flash(f'Usunięto zlecenia: {count}.', 'success')
    return redirect(url_for('prestiz_kj.orders_list'))
