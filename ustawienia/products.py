"""Szablony produktów: elementy produktu, ilość na sztukę i trasa przez etapy (Ustawienia produkcji)."""
from flask import render_template, redirect, url_for, request, flash, abort
from flask_login import login_required
from sqlalchemy import func

from models import (db, get_or_404, ProductionStage, ProductTemplate, ProductComponent,
                    ProductComponentStage, WorkOrder)
from . import ustawienia_bp
from .routes import settings_required, _audit


def _element_stages():
    return ProductionStage.query.filter_by(is_active=True, level='element').order_by(
        ProductionStage.order, ProductionStage.id).all()


def _name_taken(name, exclude_id=None):
    from kp.service import norm
    wanted = norm(name)
    for t in ProductTemplate.query.all():
        if t.id != exclude_id and norm(t.name) == wanted:
            return t
    return None


def _edit_page(tpl):
    return redirect(url_for('ustawienia.product_edit', tpl_id=tpl.id))


@ustawienia_bp.route('/produkty')
@login_required
@settings_required
def products():
    templates = ProductTemplate.query.order_by(ProductTemplate.name).all()
    return render_template('ustawienia/products.html', templates=templates)


@ustawienia_bp.route('/produkty/nowy', methods=['GET', 'POST'])
@login_required
@settings_required
def product_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Nazwa szablonu jest wymagana.', 'warning')
        elif _name_taken(name):
            flash(f'Szablon „{name}” już istnieje.', 'warning')
        else:
            tpl = ProductTemplate(name=name[:128])
            db.session.add(tpl)
            db.session.commit()
            _audit('ustawienia_product_add', 'ProductTemplate', tpl.id, name)
            flash(f'Utworzono szablon „{name}”. Dodaj elementy produktu.', 'success')
            return _edit_page(tpl)
        return render_template('ustawienia/product_new.html', name=name)
    return render_template('ustawienia/product_new.html', name=request.args.get('name', ''))


@ustawienia_bp.route('/produkty/<int:tpl_id>')
@login_required
@settings_required
def product_edit(tpl_id):
    tpl = get_or_404(ProductTemplate, tpl_id)
    return render_template('ustawienia/product_edit.html', tpl=tpl, stages=_element_stages())


@ustawienia_bp.route('/produkty/<int:tpl_id>/zapisz', methods=['POST'])
@login_required
@settings_required
def product_save(tpl_id):
    tpl = get_or_404(ProductTemplate, tpl_id)
    name = request.form.get('name', '').strip()
    if not name:
        flash('Nazwa szablonu jest wymagana.', 'warning')
    elif _name_taken(name, exclude_id=tpl.id):
        flash(f'Szablon „{name}” już istnieje.', 'warning')
    else:
        tpl.name = name[:128]
        tpl.is_active = request.form.get('is_active') == '1'
        db.session.commit()
        _audit('ustawienia_product_edit', 'ProductTemplate', tpl.id, tpl.name)
        flash('Zapisano szablon.', 'success')
    return _edit_page(tpl)


@ustawienia_bp.route('/produkty/<int:tpl_id>/kopiuj', methods=['POST'])
@login_required
@settings_required
def product_copy(tpl_id):
    src = get_or_404(ProductTemplate, tpl_id)
    base = f'{src.name} (kopia)'
    name, n = base, 2
    while _name_taken(name):
        name = f'{base} {n}'
        n += 1
    tpl = ProductTemplate(name=name[:128], is_active=False)
    db.session.add(tpl)
    db.session.flush()
    for i, comp in enumerate(src.components, 1):
        c = ProductComponent(template_id=tpl.id, name=comp.name, position=i)
        db.session.add(c)
        db.session.flush()
        for sid in comp.stage_ids:
            db.session.add(ProductComponentStage(component_id=c.id, stage_id=sid))
    db.session.commit()
    _audit('ustawienia_product_copy', 'ProductTemplate', tpl.id, f'{src.name} -> {name}')
    flash(f'Skopiowano szablon jako „{name}” (nieaktywny, zmień nazwę i aktywuj).', 'success')
    return _edit_page(tpl)


@ustawienia_bp.route('/produkty/<int:tpl_id>/usun', methods=['POST'])
@login_required
@settings_required
def product_delete(tpl_id):
    tpl = get_or_404(ProductTemplate, tpl_id)
    name = tpl.name
    # Zlecenia mają własną kopię elementów, więc zostają.
    WorkOrder.query.filter_by(template_id=tpl.id).update({'template_id': None})
    db.session.delete(tpl)
    db.session.commit()
    _audit('ustawienia_product_delete', 'ProductTemplate', tpl_id, name)
    flash(f'Usunięto szablon „{name}”.', 'success')
    return redirect(url_for('ustawienia.products'))


# ── Elementy szablonu ─────────────────────────────────────────────────────────

def _apply_component(comp, form):
    name = form.get('name', '').strip()
    if not name:
        return 'Nazwa elementu jest wymagana.'
    valid = {s.id for s in _element_stages()}
    wanted = {int(x) for x in form.getlist('stage_ids') if x.isdigit()} & valid
    comp.name = name[:128]
    ProductComponentStage.query.filter_by(component_id=comp.id).delete()
    for sid in wanted:
        db.session.add(ProductComponentStage(component_id=comp.id, stage_id=sid))
    return None


@ustawienia_bp.route('/produkty/<int:tpl_id>/element/nowy', methods=['POST'])
@login_required
@settings_required
def product_component_add(tpl_id):
    tpl = get_or_404(ProductTemplate, tpl_id)
    position = (db.session.query(func.max(ProductComponent.position))
                .filter_by(template_id=tpl.id).scalar() or 0) + 1
    comp = ProductComponent(template_id=tpl.id, name='x', position=position)
    db.session.add(comp)
    db.session.flush()
    err = _apply_component(comp, request.form)
    if err:
        db.session.rollback()
        flash(err, 'warning')
    else:
        db.session.commit()
        _audit('ustawienia_product_component_add', 'ProductTemplate', tpl.id, comp.name)
        flash(f'Dodano element „{comp.name}”.', 'success')
    return _edit_page(tpl)


@ustawienia_bp.route('/produkty/element/<int:comp_id>/zapisz', methods=['POST'])
@login_required
@settings_required
def product_component_save(comp_id):
    comp = get_or_404(ProductComponent, comp_id)
    err = _apply_component(comp, request.form)
    if err:
        db.session.rollback()
        flash(err, 'warning')
    else:
        db.session.commit()
        _audit('ustawienia_product_component_edit', 'ProductTemplate', comp.template_id, comp.name)
        flash(f'Zapisano element „{comp.name}”.', 'success')
    return redirect(url_for('ustawienia.product_edit', tpl_id=comp.template_id))


@ustawienia_bp.route('/produkty/element/<int:comp_id>/usun', methods=['POST'])
@login_required
@settings_required
def product_component_delete(comp_id):
    comp = get_or_404(ProductComponent, comp_id)
    tpl_id, name = comp.template_id, comp.name
    db.session.delete(comp)
    db.session.commit()
    _audit('ustawienia_product_component_delete', 'ProductTemplate', tpl_id, name)
    flash(f'Usunięto element „{name}”.', 'success')
    return redirect(url_for('ustawienia.product_edit', tpl_id=tpl_id))


@ustawienia_bp.route('/produkty/element/<int:comp_id>/przesun/<direction>', methods=['POST'])
@login_required
@settings_required
def product_component_move(comp_id, direction):
    comp = get_or_404(ProductComponent, comp_id)
    items = ProductComponent.query.filter_by(template_id=comp.template_id).order_by(
        ProductComponent.position, ProductComponent.id).all()
    idx = next(i for i, c in enumerate(items) if c.id == comp.id)
    swap = idx - 1 if direction == 'up' else idx + 1
    if 0 <= swap < len(items):
        items[idx], items[swap] = items[swap], items[idx]
    for i, c in enumerate(items, 1):
        c.position = i
    db.session.commit()
    return redirect(url_for('ustawienia.product_edit', tpl_id=comp.template_id))
