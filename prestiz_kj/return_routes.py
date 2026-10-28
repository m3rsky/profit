"""Zawroty: zgłoszenie w aplikacji KJ (operacja docelowa, dodatkowe operacje, przyczyna, opis, poprawka albo złom),
podsumowanie, a na komputerze rejestr zawrotów ze szczegółami i zatwierdzaniem przez kierownika (opcja)."""
from flask import render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import db
from . import prestiz_kj_bp
from .access import view_required, controller_required, admin_required, audit
from .models import (PkjInspection, PkjOperation, PkjOrder, PkjOrderMaterial, PkjOrderOperation, PkjPhoto,
                     PkjReason, PkjResult, PkjReturn, PkjReturnOperation)
from . import repairs
from .service import get_setting


def _get(model, ident):
    return db.session.get(model, ident) or abort(404)


def _ret_context(ret):
    om = db.session.get(PkjOrderMaterial, ret.order_material_id)
    return {'ret': ret, 'om': om, 'zp': db.session.get(PkjOrder, om.order_id),
            'zn': db.session.get(PkjOrder, ret.repair_order_id) if ret.repair_order_id else None,
            'ops': PkjReturnOperation.query.filter_by(return_id=ret.id).order_by(PkjReturnOperation.position).all()}


# ── Aplikacja KJ ──────────────────────────────────────────────────────────────

@prestiz_kj_bp.route('/m/zawrot/nowy/<int:material_id>', methods=['GET', 'POST'])
@login_required
@controller_required
def m_return_new(material_id):
    om = _get(PkjOrderMaterial, material_id)
    order = _get(PkjOrder, om.order_id)
    if om.element_status != 'zawrocony' or repairs.open_return_for(om.id):
        flash('Ten materiał nie wymaga zgłoszenia zawrotu.', 'info')
        return redirect(url_for('prestiz_kj.m_order', order_id=order.id))
    route = PkjOrderOperation.query.filter_by(order_id=order.id).order_by(PkjOrderOperation.position).all()
    if request.method == 'POST':
        extra = [int(i) for i in request.form.getlist('extra') if i.isdigit()]
        try:
            ret = repairs.create_return(om, request.form.get('reason_id', type=int), request.form.get('description'),
                                        request.form.get('decision'), request.form.get('target', type=int), extra,
                                        actor=current_user.username)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
        else:
            db.session.commit()
            audit('pkj_return_create', ret.id, f'{order.number}: {om.material_name}')
            return redirect(url_for('prestiz_kj.m_return', return_id=ret.id))
    _, failed = repairs.failed_points(om)
    return render_template('prestiz_kj/m/zawrot_form.html', om=om, order=order, route=route, failed=failed,
                           reasons=PkjReason.query.filter_by(is_active=True).order_by(PkjReason.order).all(),
                           operations=PkjOperation.query.filter_by(is_active=True).order_by(PkjOperation.order).all(),
                           sel_target=request.form.get('target', type=int) or om.order_operation_id,
                           sel_reason=request.form.get('reason_id', type=int),
                           manager=get_setting('approval_mode') == 'manager', form=request.form)


@prestiz_kj_bp.route('/m/zawrot/<int:return_id>')
@login_required
@controller_required
def m_return(return_id):
    return render_template('prestiz_kj/m/zawrot.html', **_ret_context(_get(PkjReturn, return_id)),
                           decisions=repairs.DECISIONS, labels=repairs.RETURN_STATUS_LABELS)


# ── Rejestr zawrotów (komputer) ───────────────────────────────────────────────

@prestiz_kj_bp.route('/zawroty')
@login_required
@view_required
def returns_list():
    q = request.args.get('q', '').strip()
    status = request.args.get('s', '')
    query = (PkjReturn.query.join(PkjOrderMaterial, PkjReturn.order_material_id == PkjOrderMaterial.id)
             .join(PkjOrder, PkjOrderMaterial.order_id == PkjOrder.id))
    if q:
        like = f'%{q}%'
        query = query.filter(or_(PkjOrder.number.ilike(like), PkjOrder.client.ilike(like),
                                 PkjOrderMaterial.material_name.ilike(like), PkjReturn.reason_name.ilike(like)))
    if status in repairs.RETURN_STATUS_LABELS:
        query = query.filter(PkjReturn.status == status)
    else:
        status = ''
    pager = query.order_by(PkjReturn.created_at.desc(), PkjReturn.id.desc()).paginate(
        page=request.args.get('page', 1, type=int), per_page=20, error_out=False)
    rows = [_ret_context(r) for r in pager.items]
    waiting = PkjReturn.query.filter_by(status='do_zatwierdzenia').count()
    return render_template('prestiz_kj/zawroty.html', rows=rows, pager=pager, q=q, status=status,
                           labels=repairs.RETURN_STATUS_LABELS, decisions=repairs.DECISIONS, waiting=waiting)


@prestiz_kj_bp.route('/zawroty/<int:return_id>')
@login_required
@view_required
def return_page(return_id):
    ctx = _ret_context(_get(PkjReturn, return_id))
    ret = ctx['ret']
    results, photos = [], {}
    if ret.inspection_id:
        results = (PkjResult.query.filter(PkjResult.inspection_id == ret.inspection_id, PkjResult.result == 'NOK')
                   .order_by(PkjResult.position).all())
        mat_section = ctx['om'].material_name
        results = [r for r in results if r.section in (mat_section, 'Wspólne')]
        for ph in (PkjPhoto.query.filter(PkjPhoto.result_id.in_([r.id for r in results])).all() if results else []):
            photos.setdefault(ph.result_id, []).append(ph)
    return render_template('prestiz_kj/zawrot.html', **ctx, results=results, photos=photos,
                           inspection=db.session.get(PkjInspection, ret.inspection_id) if ret.inspection_id else None,
                           decisions=repairs.DECISIONS, labels=repairs.RETURN_STATUS_LABELS)


@prestiz_kj_bp.route('/zawroty/<int:return_id>/zatwierdz', methods=['POST'])
@login_required
@admin_required
def return_approve(return_id):
    ret = _get(PkjReturn, return_id)
    try:
        repairs.approve_return(ret, actor=current_user.username)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    else:
        db.session.commit()
        audit('pkj_return_approve', ret.id)
        flash('Zawrót zatwierdzony, utworzono zlecenie naprawcze.', 'success')
    return redirect(url_for('prestiz_kj.return_page', return_id=ret.id))
