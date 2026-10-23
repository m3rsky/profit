from functools import wraps

from flask import (render_template, redirect, url_for, request, flash,
                   abort, jsonify, current_app)
from flask_login import login_required, current_user
from sqlalchemy import func

from models import db, get_or_404, AuditLog, ProductionDepartment, DepartmentEmployee
from . import ustawienia_bp


# ── Dostęp ────────────────────────────────────────────────────────────────────

def settings_required(f):
    """admin + kontroler, tak jak dotychczas w Marszrucie."""
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
        current_app.logger.error('Ustawienia produkcji audit error: %s', exc)


def _departments(active_only=True):
    q = ProductionDepartment.query
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(ProductionDepartment.order).all()


@ustawienia_bp.route('/')
@login_required
@settings_required
def index():
    return redirect(url_for('ustawienia.admin_departments'))


# ── Admin: działy produkcyjne ─────────────────────────────────────────────────

@ustawienia_bp.route('/admin/departments', methods=['GET', 'POST'])
@login_required
@settings_required
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
                _audit('ustawienia_dept_add', 'ProductionDepartment', dept.id, name)
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
                _audit('ustawienia_dept_rename', 'ProductionDepartment', dept.id,
                       f'{old_name} -> {name}')
                flash(f'Zmieniono nazwę działu na „{name}".', 'success')

        elif action == 'toggle':
            dept = get_or_404(ProductionDepartment, request.form.get('dept_id', type=int))
            dept.is_active = not dept.is_active
            db.session.commit()
            _audit('ustawienia_dept_toggle', 'ProductionDepartment', dept.id,
                   f'{dept.name} -> {"active" if dept.is_active else "inactive"}')
            flash(f'Dział „{dept.name}" {"aktywowany" if dept.is_active else "dezaktywowany"}.', 'success')

        elif action == 'delete':
            dept = get_or_404(ProductionDepartment, request.form.get('dept_id', type=int))
            name = dept.name
            db.session.delete(dept)
            db.session.commit()
            _audit('ustawienia_dept_delete', 'ProductionDepartment', dept.id, name)
            flash(f'Dział „{name}" usunięty.', 'success')

        return redirect(url_for('ustawienia.admin_departments'))

    departments = ProductionDepartment.query.order_by(ProductionDepartment.order).all()
    return render_template('ustawienia/admin_departments.html', departments=departments)


@ustawienia_bp.route('/admin/departments/reorder', methods=['POST'])
@login_required
@settings_required
def admin_departments_reorder():
    items = request.get_json(silent=True)
    if not isinstance(items, list):
        return jsonify({'error': 'bad request'}), 400
    for idx, entry in enumerate(items):
        dept = db.session.get(ProductionDepartment, entry.get('id'))
        if dept:
            dept.order = idx
    db.session.commit()
    _audit('ustawienia_dept_reorder', 'ProductionDepartment', None,
           ','.join(str(e.get('id')) for e in items))
    return jsonify({'ok': True})


@ustawienia_bp.route('/admin/departments/<int:dept_id>/employees', methods=['GET', 'POST'])
@login_required
@settings_required
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
                _audit('ustawienia_employee_add', 'DepartmentEmployee', emp.id,
                       f'{name} ({dept.name})')
                flash(f'Dodano pracownika „{name}".', 'success')

        elif action == 'toggle':
            emp = get_or_404(DepartmentEmployee, request.form.get('emp_id', type=int))
            emp.is_active = not emp.is_active
            db.session.commit()
            _audit('ustawienia_employee_toggle', 'DepartmentEmployee', emp.id,
                   f'{emp.name} -> {"active" if emp.is_active else "inactive"}')
            flash(f'Pracownik „{emp.name}" {"aktywowany" if emp.is_active else "dezaktywowany"}.', 'success')

        elif action == 'delete':
            emp = get_or_404(DepartmentEmployee, request.form.get('emp_id', type=int))
            if emp.qar_reports.count() > 0:
                flash(f'Nie można usunąć „{emp.name}”: ma przypisane raporty QAR. '
                      f'Dezaktywuj pracownika.', 'warning')
            else:
                name = emp.name
                db.session.delete(emp)
                db.session.commit()
                _audit('ustawienia_employee_delete', 'DepartmentEmployee', emp.id, name)
                flash(f'Pracownik „{name}" usunięty.', 'success')

        return redirect(url_for('ustawienia.admin_employees', dept_id=dept.id))

    employees = DepartmentEmployee.query.filter_by(department_id=dept.id).order_by(DepartmentEmployee.name).all()
    departments = ProductionDepartment.query.order_by(ProductionDepartment.order,
                                                      ProductionDepartment.name).all()
    return render_template('ustawienia/admin_employees.html', department=dept, employees=employees,
                           departments=departments)
