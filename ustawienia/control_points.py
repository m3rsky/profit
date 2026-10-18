"""Punkty kontrolne etapów i domyślny plan kontroli szablonu produktu (Ustawienia produkcji)."""
from flask import render_template, redirect, url_for, request, flash
from flask_login import login_required
from sqlalchemy import func

from models import (db, get_or_404, ProductionStage, ProductTemplate, ProductTemplateControl,
                    StageControlPoint)
from . import ustawienia_bp
from .routes import settings_required, _audit


def _points_page(stage_id):
    return redirect(url_for('ustawienia.stage_points', stage_id=stage_id))


@ustawienia_bp.route('/etapy/<int:stage_id>/punkty')
@login_required
@settings_required
def stage_points(stage_id):
    stage = get_or_404(ProductionStage, stage_id)
    return render_template('ustawienia/stage_points.html', stage=stage, points=stage.control_points.all())


@ustawienia_bp.route('/etapy/<int:stage_id>/punkty/nowy', methods=['POST'])
@login_required
@settings_required
def stage_point_add(stage_id):
    stage = get_or_404(ProductionStage, stage_id)
    title = request.form.get('title', '').strip()
    if not title:
        flash('Nazwa punktu jest wymagana.', 'warning')
        return _points_page(stage.id)
    order = (db.session.query(func.max(StageControlPoint.order)).filter_by(stage_id=stage.id).scalar() or 0) + 1
    db.session.add(StageControlPoint(stage_id=stage.id, title=title[:256], order=order,
                                     description=request.form.get('description', '').strip() or None))
    db.session.commit()
    _audit('ustawienia_stage_point_add', 'ProductionStage', stage.id, f'{stage.name}: {title}')
    flash('Dodano punkt kontrolny.', 'success')
    return _points_page(stage.id)


@ustawienia_bp.route('/punkty/<int:point_id>/zapisz', methods=['POST'])
@login_required
@settings_required
def stage_point_save(point_id):
    pt = get_or_404(StageControlPoint, point_id)
    title = request.form.get('title', '').strip()
    if not title:
        flash('Nazwa punktu jest wymagana.', 'warning')
        return _points_page(pt.stage_id)
    pt.title = title[:256]
    pt.description = request.form.get('description', '').strip() or None
    pt.is_active = request.form.get('is_active') == '1'
    db.session.commit()
    _audit('ustawienia_stage_point_edit', 'ProductionStage', pt.stage_id, pt.title)
    flash('Zapisano punkt kontrolny.', 'success')
    return _points_page(pt.stage_id)


@ustawienia_bp.route('/punkty/<int:point_id>/przesun/<direction>', methods=['POST'])
@login_required
@settings_required
def stage_point_move(point_id, direction):
    pt = get_or_404(StageControlPoint, point_id)
    items = StageControlPoint.query.filter_by(stage_id=pt.stage_id).order_by(
        StageControlPoint.order, StageControlPoint.id).all()
    idx = next(i for i, p in enumerate(items) if p.id == pt.id)
    swap = idx - 1 if direction == 'up' else idx + 1
    if 0 <= swap < len(items):
        items[idx], items[swap] = items[swap], items[idx]
    for i, p in enumerate(items, 1):
        p.order = i
    db.session.commit()
    return _points_page(pt.stage_id)


@ustawienia_bp.route('/punkty/<int:point_id>/usun', methods=['POST'])
@login_required
@settings_required
def stage_point_delete(point_id):
    pt = get_or_404(StageControlPoint, point_id)
    stage_id, title = pt.stage_id, pt.title
    db.session.delete(pt)             # wykonane kontrole mają własną kopię tytułu
    db.session.commit()
    _audit('ustawienia_stage_point_delete', 'ProductionStage', stage_id, title)
    flash(f'Usunięto punkt „{title}”.', 'success')
    return _points_page(stage_id)


# ── Domyślny plan kontroli szablonu produktu ──────────────────────────────────

@ustawienia_bp.route('/produkty/<int:tpl_id>/plan', methods=['POST'])
@login_required
@settings_required
def product_plan_save(tpl_id):
    tpl = get_or_404(ProductTemplate, tpl_id)
    valid = {s.id for s in ProductionStage.query.filter_by(is_active=True).all()}
    wanted = {int(x) for x in request.form.getlist('stage_ids') if x.isdigit()} & valid
    ProductTemplateControl.query.filter_by(template_id=tpl.id).delete()
    for sid in wanted:
        db.session.add(ProductTemplateControl(template_id=tpl.id, stage_id=sid))
    db.session.commit()
    _audit('ustawienia_product_plan', 'ProductTemplate', tpl.id, f'{tpl.name}: etapy={sorted(wanted)}')
    flash('Zapisano domyślny plan kontroli. Obowiązuje dla nowych zleceń z tego szablonu.', 'success')
    return redirect(url_for('ustawienia.product_edit', tpl_id=tpl.id))
