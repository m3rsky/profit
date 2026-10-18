"""Kolejka kontrolera, ocena kontroli etapu i plan kontroli zlecenia (etap 5a planu)."""
from flask import render_template, redirect, url_for, request, flash, abort, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import (db, get_or_404, AuditLog, ProductionStage, StageControl, WorkOrder, WorkOrderControl)
from . import kp_bp, controls
from .routes import controller_required


@kp_bp.app_context_processor
def _inject_controls():
    def pending():
        try:
            return controls.pending_count()
        except Exception:   # np. przed migracją bazy
            return 0
    return {'kp_pending_controls': pending}


def _audit(action, target_id=None, detail=None):
    try:
        db.session.add(AuditLog(user_id=current_user.id, action=action, target_type='stage_control',
                                target_id=target_id, detail=detail, ip=request.remote_addr))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Kontrole etapów audit error: %s', exc)


# ── Kolejka kontroli (odczyt dla wszystkich zalogowanych) ─────────────────────

@kp_bp.route('/kontrole')
@login_required
def controls_list():
    status = request.args.get('s', 'oczekuje')
    stage_id = request.args.get('stage', type=int)
    q_text = request.args.get('q', '').strip()
    query = StageControl.query
    if q_text:
        like = f'%{q_text}%'
        query = query.join(WorkOrder, WorkOrder.id == StageControl.order_id).filter(
            or_(WorkOrder.zo_number.ilike(like), WorkOrder.product_name.ilike(like),
                WorkOrder.client.ilike(like)))
    if stage_id:
        query = query.filter(StageControl.stage_id == stage_id)
    counts = {s: query.filter(StageControl.status == s).count() for s in ('oczekuje', 'zakonczona', 'anulowana')}
    if status in StageControl.STATUS_LABELS:
        query = query.filter(StageControl.status == status)
    else:
        status = 'all'
    order = StageControl.created_at.asc() if status == 'oczekuje' else StageControl.created_at.desc()
    items = query.order_by(order, StageControl.id).limit(300).all()
    stages = ProductionStage.query.order_by(ProductionStage.order, ProductionStage.id).all()
    return render_template('kp/controls.html', controls=items, status=status, counts=counts,
                           total=sum(counts.values()), stages=stages, stage_id=stage_id, q=q_text)


@kp_bp.route('/kontrole/<int:control_id>')
@login_required
def control_detail(control_id):
    ctrl = get_or_404(StageControl, control_id)
    points = controls.active_points(ctrl.stage_id) if ctrl.status == 'oczekuje' else []
    return render_template('kp/control_detail.html', ctrl=ctrl, points=points,
                           can_rate=current_user.is_kontroler and ctrl.status == 'oczekuje')


# ── Ocena kontroli ────────────────────────────────────────────────────────────

@kp_bp.route('/kontrole/<int:control_id>/ocen', methods=['POST'])
@login_required
@controller_required
def control_rate(control_id):
    ctrl = get_or_404(StageControl, control_id)
    form = request.form
    point_results = {}
    for p in controls.active_points(ctrl.stage_id):
        point_results[p.id] = {'result': form.get(f'result_{p.id}', ''), 'notes': form.get(f'pnotes_{p.id}', '')}
    done, qars, err = controls.evaluate(ctrl, form.get('expected'), form.get('ok_qty'), form.get('ng_qty'),
                                        point_results, form.get('notes'), current_user)
    if err:
        db.session.rollback()
        flash(err, 'warning')
        return redirect(url_for('kp.control_detail', control_id=ctrl.id))
    zo = done.order.zo_number
    db.session.commit()
    _audit('kp_control_done', done.id, f'ZO={zo} {done.component.name} {done.stage.name} '
                                       f'zgodne={done.ok_qty} niezgodne={done.ng_qty} QAR={len(qars)}')
    msg = f'Kontrola zakończona: {done.ok_qty} szt. zgodnych, {done.ng_qty} szt. niezgodnych.'
    if qars:
        msg += ' Utworzono raporty QAR: ' + ', '.join(q.number for q in qars) + '.'
    flash(msg, 'warning' if qars else 'success')
    nxt = StageControl.query.filter_by(status='oczekuje').order_by(StageControl.created_at, StageControl.id).first()
    if nxt:
        return redirect(url_for('kp.control_detail', control_id=nxt.id))
    return redirect(url_for('kp.controls_list'))


# ── Plan kontroli zlecenia ────────────────────────────────────────────────────

@kp_bp.route('/<int:order_id>/plan', methods=['POST'])
@login_required
@controller_required
def order_plan_save(order_id):
    """Plan kontroli zlecenia: etapy, na których ilości wchodzące na bufor czekają na kontrolę. Zmiana
    dotyczy kolejnych wejść na bufor, ilości już leżące na buforach zostają bez zmian."""
    order = get_or_404(WorkOrder, order_id)
    valid = {s.id for s in ProductionStage.query.filter_by(is_active=True).all()}
    wanted = {int(x) for x in request.form.getlist('stage_ids') if x.isdigit()} & valid
    WorkOrderControl.query.filter_by(order_id=order.id).delete()
    for sid in wanted:
        db.session.add(WorkOrderControl(order_id=order.id, stage_id=sid))
    db.session.commit()
    _audit('kp_plan', order.id, f'ZO={order.zo_number} etapy={sorted(wanted)}')
    flash('Zapisano plan kontroli zlecenia. Obowiązuje dla kolejnych wejść ilości na bufory.', 'success')
    return redirect(url_for('kp.order_detail', order_id=order.id))
