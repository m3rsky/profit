import os
import re
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import (render_template, redirect, url_for, request, flash,
                   abort, jsonify, current_app, send_from_directory)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import func

from models import (db, get_or_404, AuditLog, ProductionDepartment, DepartmentEmployee,
                    RoutingTemplate, RoutingTemplateStage, RoutingCard,
                    RoutingCardStage, RoutingCardPhoto)
from . import marszruta_bp

UTC = timezone.utc


# ── Dostęp ────────────────────────────────────────────────────────────────────

def marszruta_required(f):
    """admin + kontroler — spójne z dostępem do szablonów checklisty."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_kontroler:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _audit(action, target_type=None, target_id=None, detail=None):
    try:
        db.session.add(AuditLog(
            user_id=current_user.id, action=action, target_type=target_type,
            target_id=target_id, detail=detail, ip=request.remote_addr,
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Marszruta audit error: %s', exc)


def _allowed_file(filename):
    return ('.' in filename and
            filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS'])


def _verify_image(file_stream):
    try:
        from PIL import Image as PILImage
        img = PILImage.open(file_stream)
        img.verify()
        file_stream.seek(0)
        return True
    except Exception:
        file_stream.seek(0)
        return False


def _departments(active_only=True):
    q = ProductionDepartment.query
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(ProductionDepartment.order).all()


def _find_matching_routing_template(product_name):
    """Dopasowuje ścieżkę produkcyjną po tokenach nazwy — ten sam algorytm co
    _find_matching_template w app.py dla ChecklistTemplate."""

    def _tokens(s):
        s = re.sub(r'\bv\d+\b', '', s, flags=re.IGNORECASE)
        parts = re.split(r'[^a-zA-Z0-9]+', s)
        return {p.lower() for p in parts if len(p) >= 1}

    product_tokens = _tokens(product_name)
    if not product_tokens:
        return None

    candidates = []
    for tpl in RoutingTemplate.query.filter_by(is_active=True).all():
        tpl_tokens = _tokens(tpl.name)
        common = tpl_tokens & product_tokens
        if common:
            unmatched = len(tpl_tokens) - len(common)
            score = len(common) * 100 + max(len(t) for t in common) - unmatched * 1000
            candidates.append((score, tpl))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# ── Lista kart ────────────────────────────────────────────────────────────────

@marszruta_bp.route('/')
@login_required
@marszruta_required
def list_cards():
    identifier_filter = request.args.get('q', '').strip()
    query = RoutingCard.query.order_by(RoutingCard.created_at.desc())
    if identifier_filter:
        query = query.filter(
            (RoutingCard.identifier.ilike(f'%{identifier_filter}%')) |
            (RoutingCard.product_name.ilike(f'%{identifier_filter}%'))
        )
    cards = query.all()
    return render_template('marszruta/list.html', cards=cards, identifier_filter=identifier_filter)


# ── Skanowanie QR ─────────────────────────────────────────────────────────────

@marszruta_bp.route('/scan')
@login_required
@marszruta_required
def scan_page():
    return render_template('marszruta/scan.html')


@marszruta_bp.route('/from-qr', methods=['POST'])
@login_required
@marszruta_required
def from_qr():
    data = request.get_json(silent=True) or {}
    product_name = (data.get('p') or '').strip()
    client       = (data.get('c') or '').strip()
    identifier   = (data.get('o') or '').strip()

    if not product_name:
        return jsonify({'error': 'Brak nazwy produktu w kodzie QR'}), 400

    try:
        quantity = max(1, min(99, int(data.get('q', 1))))
    except (TypeError, ValueError):
        quantity = 1

    if not identifier:
        identifier = f'{product_name}-{datetime.now().strftime("%Y%m%d%H%M%S")}'

    existing = RoutingCard.query.filter_by(identifier=identifier).first()
    if existing:
        _audit('marszruta_scan_existing', 'RoutingCard', existing.id, f'identifier={identifier}')
        return jsonify({
            'ok': True,
            'redirect': url_for('marszruta.card_detail', card_id=existing.id),
            'msg': f'Karta „{identifier}" już istnieje — otwieram.',
        })

    tmpl = _find_matching_routing_template(product_name)
    if not tmpl:
        return jsonify({'error': f'Nie znaleziono ścieżki produkcyjnej dla „{product_name}"'}), 404

    card = RoutingCard(
        identifier=identifier, product_name=product_name,
        client=client or None, quantity=quantity,
        template_id=tmpl.id, created_by_id=current_user.id,
    )
    db.session.add(card)
    db.session.flush()

    for stage_def in tmpl.stages.order_by(RoutingTemplateStage.order):
        db.session.add(RoutingCardStage(
            card_id=card.id, department_id=stage_def.department_id,
            order=stage_def.order,
        ))

    _audit('marszruta_card_create', 'RoutingCard', card.id,
           f'identifier={identifier} product={product_name} tpl={tmpl.name}')
    db.session.commit()

    return jsonify({
        'ok': True,
        'redirect': url_for('marszruta.card_detail', card_id=card.id),
        'msg': f'Utworzono kartę marszrutową „{identifier}".',
        'template': tmpl.name,
    })


# ── Karta marszrutowa ─────────────────────────────────────────────────────────

@marszruta_bp.route('/<int:card_id>')
@login_required
@marszruta_required
def card_detail(card_id):
    card = get_or_404(RoutingCard, card_id)
    stages = card.stages.order_by(RoutingCardStage.order).all()
    return render_template('marszruta/card_detail.html', card=card, stages=stages)


@marszruta_bp.route('/<int:card_id>/delete', methods=['POST'])
@login_required
def delete_card(card_id):
    if not current_user.is_admin:
        abort(403)
    card = get_or_404(RoutingCard, card_id)
    upload_dir = current_app.config['MARSZRUTA_UPLOAD_FOLDER']
    for stage in card.stages:
        for photo in stage.photos:
            path = os.path.join(upload_dir, photo.filename)
            if os.path.exists(path):
                os.remove(path)
    identifier = card.identifier
    db.session.delete(card)
    db.session.commit()
    _audit('marszruta_card_delete', 'RoutingCard', card_id, f'identifier={identifier}')
    flash(f'Karta marszrutowa „{identifier}" usunięta.', 'success')
    return redirect(url_for('marszruta.list_cards'))


@marszruta_bp.route('/stage/<int:stage_id>/edit', methods=['GET', 'POST'])
@login_required
@marszruta_required
def edit_stage(stage_id):
    stage = get_or_404(RoutingCardStage, stage_id)
    employees = (DepartmentEmployee.query
                .filter_by(department_id=stage.department_id, is_active=True)
                .order_by(DepartmentEmployee.name).all())

    if request.method == 'POST':
        result = request.form.get('result') or None
        if result not in (None, 'ok', 'ng'):
            abort(400)
        employee_id = request.form.get('employee_id') or None

        stage.result        = result
        stage.employee_id   = int(employee_id) if employee_id else None
        stage.notes         = request.form.get('notes', '').strip() or None
        stage.checked_by_id = current_user.id
        stage.checked_at    = datetime.now(UTC)

        files = request.files.getlist('photos')
        for file in files:
            if not file or not file.filename:
                continue
            if not _allowed_file(file.filename):
                flash(f'Pominięto plik „{file.filename}" — niedozwolone rozszerzenie.', 'warning')
                continue
            if not _verify_image(file):
                flash(f'Pominięto plik „{file.filename}" — plik nie jest prawidłowym obrazem.', 'warning')
                continue
            ext = file.filename.rsplit('.', 1)[1].lower()
            unique_name = f'{uuid.uuid4().hex}.{ext}'
            upload_dir = current_app.config['MARSZRUTA_UPLOAD_FOLDER']
            file.save(os.path.join(upload_dir, unique_name))
            db.session.add(RoutingCardPhoto(
                stage_id=stage.id, filename=unique_name,
                original_name=secure_filename(file.filename),
            ))

        _audit('marszruta_stage_edit', 'RoutingCardStage', stage.id,
               f'dept={stage.department.name} result={result}')
        db.session.commit()
        flash(f'Etap „{stage.department.name}" zapisany.', 'success')
        return redirect(url_for('marszruta.card_detail', card_id=stage.card_id))

    return render_template('marszruta/stage_edit.html', stage=stage, employees=employees)


# ── Zdjęcia (usuwanie) ────────────────────────────────────────────────────────

@marszruta_bp.route('/photo/<int:photo_id>/delete', methods=['POST'])
@login_required
@marszruta_required
def delete_photo(photo_id):
    photo = get_or_404(RoutingCardPhoto, photo_id)
    stage_id = photo.stage_id
    filepath = os.path.join(current_app.config['MARSZRUTA_UPLOAD_FOLDER'], photo.filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    db.session.delete(photo)
    db.session.commit()
    return redirect(url_for('marszruta.edit_stage', stage_id=stage_id))


@marszruta_bp.route('/uploads/<filename>')
@login_required
def serve_photo(filename):
    return send_from_directory(current_app.config['MARSZRUTA_UPLOAD_FOLDER'], filename)


# ── Admin: działy produkcyjne ─────────────────────────────────────────────────

@marszruta_bp.route('/admin/departments', methods=['GET', 'POST'])
@login_required
@marszruta_required
def admin_departments():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            name = request.form.get('name', '').strip()
            if not name:
                flash('Nazwa działu jest wymagana.', 'warning')
            elif ProductionDepartment.query.filter_by(name=name).first():
                flash(f'Dział „{name}" już istnieje.', 'warning')
            else:
                max_order = db.session.query(func.max(ProductionDepartment.order)).scalar() or 0
                dept = ProductionDepartment(name=name, order=max_order + 1)
                db.session.add(dept)
                db.session.commit()
                _audit('marszruta_dept_add', 'ProductionDepartment', dept.id, name)
                flash(f'Dodano dział „{name}".', 'success')

        elif action == 'rename':
            dept = get_or_404(ProductionDepartment, request.form.get('dept_id', type=int))
            name = request.form.get('name', '').strip()
            if not name:
                flash('Nazwa działu jest wymagana.', 'warning')
            elif ProductionDepartment.query.filter(ProductionDepartment.name == name,
                                                    ProductionDepartment.id != dept.id).first():
                flash(f'Dział „{name}" już istnieje.', 'warning')
            else:
                old_name = dept.name
                dept.name = name
                db.session.commit()
                _audit('marszruta_dept_rename', 'ProductionDepartment', dept.id,
                       f'{old_name} -> {name}')
                flash(f'Zmieniono nazwę działu na „{name}".', 'success')

        elif action == 'toggle':
            dept = get_or_404(ProductionDepartment, request.form.get('dept_id', type=int))
            dept.is_active = not dept.is_active
            db.session.commit()
            _audit('marszruta_dept_toggle', 'ProductionDepartment', dept.id,
                   f'{dept.name} -> {"active" if dept.is_active else "inactive"}')
            flash(f'Dział „{dept.name}" {"aktywowany" if dept.is_active else "dezaktywowany"}.', 'success')

        elif action == 'delete':
            dept = get_or_404(ProductionDepartment, request.form.get('dept_id', type=int))
            if RoutingTemplateStage.query.filter_by(department_id=dept.id).first():
                flash(f'Nie można usunąć „{dept.name}" — jest użyty w ścieżce produkcyjnej.', 'warning')
            else:
                name = dept.name
                db.session.delete(dept)
                db.session.commit()
                _audit('marszruta_dept_delete', 'ProductionDepartment', dept.id, name)
                flash(f'Dział „{name}" usunięty.', 'success')

        return redirect(url_for('marszruta.admin_departments'))

    departments = ProductionDepartment.query.order_by(ProductionDepartment.order).all()
    return render_template('marszruta/admin_departments.html', departments=departments)


@marszruta_bp.route('/admin/departments/reorder', methods=['POST'])
@login_required
@marszruta_required
def admin_departments_reorder():
    items = request.get_json(silent=True)
    if not isinstance(items, list):
        return jsonify({'error': 'bad request'}), 400
    for idx, entry in enumerate(items):
        dept = db.session.get(ProductionDepartment, entry.get('id'))
        if dept:
            dept.order = idx
    db.session.commit()
    _audit('marszruta_dept_reorder', 'ProductionDepartment', None,
           ','.join(str(e.get('id')) for e in items))
    return jsonify({'ok': True})


@marszruta_bp.route('/admin/departments/<int:dept_id>/employees', methods=['GET', 'POST'])
@login_required
@marszruta_required
def admin_employees(dept_id):
    dept = get_or_404(ProductionDepartment, dept_id)

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            name = request.form.get('name', '').strip()
            if not name:
                flash('Imię i nazwisko pracownika jest wymagane.', 'warning')
            else:
                emp = DepartmentEmployee(department_id=dept.id, name=name)
                db.session.add(emp)
                db.session.commit()
                _audit('marszruta_employee_add', 'DepartmentEmployee', emp.id,
                       f'{name} ({dept.name})')
                flash(f'Dodano pracownika „{name}".', 'success')

        elif action == 'toggle':
            emp = get_or_404(DepartmentEmployee, request.form.get('emp_id', type=int))
            emp.is_active = not emp.is_active
            db.session.commit()
            _audit('marszruta_employee_toggle', 'DepartmentEmployee', emp.id,
                   f'{emp.name} -> {"active" if emp.is_active else "inactive"}')
            flash(f'Pracownik „{emp.name}" {"aktywowany" if emp.is_active else "dezaktywowany"}.', 'success')

        elif action == 'delete':
            emp = get_or_404(DepartmentEmployee, request.form.get('emp_id', type=int))
            if emp.stages.count() > 0:
                flash(f'Nie można usunąć „{emp.name}" — ma przypisane oceny etapów.', 'warning')
            else:
                name = emp.name
                db.session.delete(emp)
                db.session.commit()
                _audit('marszruta_employee_delete', 'DepartmentEmployee', emp.id, name)
                flash(f'Pracownik „{name}" usunięty.', 'success')

        return redirect(url_for('marszruta.admin_employees', dept_id=dept.id))

    employees = DepartmentEmployee.query.filter_by(department_id=dept.id).order_by(DepartmentEmployee.name).all()
    return render_template('marszruta/admin_employees.html', department=dept, employees=employees)


# ── Admin: ścieżki produkcyjne ────────────────────────────────────────────────

@marszruta_bp.route('/admin/routing-templates')
@login_required
@marszruta_required
def admin_routing_templates():
    templates = RoutingTemplate.query.order_by(RoutingTemplate.name).all()
    return render_template('marszruta/admin_routing_templates.html', templates=templates)


@marszruta_bp.route('/admin/routing-templates/new', methods=['GET', 'POST'])
@login_required
@marszruta_required
def new_routing_template():
    departments = _departments()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        dept_ids = set(request.form.getlist('department_ids', type=int))
        if not name:
            flash('Nazwa ścieżki jest wymagana.', 'warning')
        elif not dept_ids:
            flash('Wybierz przynajmniej jeden dział.', 'warning')
        else:
            tmpl = RoutingTemplate(name=name)
            db.session.add(tmpl)
            db.session.flush()
            for dept in departments:
                if dept.id in dept_ids:
                    db.session.add(RoutingTemplateStage(
                        template_id=tmpl.id, department_id=dept.id, order=dept.order,
                    ))
            db.session.commit()
            _audit('marszruta_template_create', 'RoutingTemplate', tmpl.id, name)
            flash(f'Ścieżka produkcyjna „{name}" utworzona.', 'success')
            return redirect(url_for('marszruta.admin_routing_templates'))

    return render_template('marszruta/routing_template_form.html',
                           departments=departments, template=None, selected_ids=set())


@marszruta_bp.route('/admin/routing-templates/<int:tpl_id>/edit', methods=['GET', 'POST'])
@login_required
@marszruta_required
def edit_routing_template(tpl_id):
    tmpl = get_or_404(RoutingTemplate, tpl_id)
    departments = _departments()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        dept_ids = set(request.form.getlist('department_ids', type=int))
        is_active = request.form.get('is_active') == 'on'
        if not name:
            flash('Nazwa ścieżki jest wymagana.', 'warning')
        elif not dept_ids:
            flash('Wybierz przynajmniej jeden dział.', 'warning')
        else:
            tmpl.name = name
            tmpl.is_active = is_active
            RoutingTemplateStage.query.filter_by(template_id=tmpl.id).delete()
            for dept in departments:
                if dept.id in dept_ids:
                    db.session.add(RoutingTemplateStage(
                        template_id=tmpl.id, department_id=dept.id, order=dept.order,
                    ))
            db.session.commit()
            _audit('marszruta_template_edit', 'RoutingTemplate', tmpl.id, name)
            flash(f'Ścieżka produkcyjna „{name}" zapisana.', 'success')
            return redirect(url_for('marszruta.admin_routing_templates'))

    selected_ids = {s.department_id for s in tmpl.stages}
    return render_template('marszruta/routing_template_form.html',
                           departments=departments, template=tmpl, selected_ids=selected_ids)


@marszruta_bp.route('/admin/routing-templates/<int:tpl_id>/delete', methods=['POST'])
@login_required
@marszruta_required
def delete_routing_template(tpl_id):
    tmpl = get_or_404(RoutingTemplate, tpl_id)
    name = tmpl.name
    db.session.delete(tmpl)
    db.session.commit()
    _audit('marszruta_template_delete', 'RoutingTemplate', tpl_id, name)
    flash(f'Ścieżka produkcyjna „{name}" usunięta.', 'success')
    return redirect(url_for('marszruta.admin_routing_templates'))
