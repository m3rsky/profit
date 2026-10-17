"""Pracownicy działów: plakietki QR, nowy kod i przenoszenie między działami."""
import re
import secrets

from flask import redirect, url_for, request, flash, abort, send_file
from flask_login import login_required

from models import db, get_or_404, ProductionDepartment, DepartmentEmployee
from . import ustawienia_bp
from .routes import settings_required, _audit
from .pdfs import badges_pdf


def _employees_page(emp):
    return redirect(url_for('ustawienia.admin_employees', dept_id=emp.department_id))


@ustawienia_bp.route('/admin/employees/<int:emp_id>/plakietka.pdf')
@login_required
@settings_required
def employee_badge(emp_id):
    emp = get_or_404(DepartmentEmployee, emp_id)
    safe = re.sub(r'[^A-Za-z0-9]', '', emp.name) or str(emp.id)
    return send_file(badges_pdf([emp]), mimetype='application/pdf',
                     download_name=f'plakietka_{safe}.pdf')


@ustawienia_bp.route('/admin/employees/plakietki.pdf')
@login_required
@settings_required
def employee_badges():
    """Plakietki aktywnych pracowników: `?dept_id=N` dla jednego działu, inaczej wszystkich."""
    q = DepartmentEmployee.query.filter_by(is_active=True)
    dept_id = request.args.get('dept_id', type=int)
    if dept_id:
        q = q.filter_by(department_id=dept_id)
    emps = q.join(ProductionDepartment).order_by(
        ProductionDepartment.order, DepartmentEmployee.name).all()
    if not emps:
        abort(404)
    return send_file(badges_pdf(emps), mimetype='application/pdf', download_name='plakietki_dzialy.pdf')


@ustawienia_bp.route('/admin/employees/<int:emp_id>/token', methods=['POST'])
@login_required
@settings_required
def employee_new_token(emp_id):
    emp = get_or_404(DepartmentEmployee, emp_id)
    emp.token = secrets.token_hex(16)
    db.session.commit()
    _audit('ustawienia_employee_token', 'DepartmentEmployee', emp.id, emp.name)
    flash(f'Wygenerowano nowy kod dla: {emp.name}. Stara plakietka przestała działać.', 'success')
    return _employees_page(emp)


@ustawienia_bp.route('/admin/employees/<int:emp_id>/przenies', methods=['POST'])
@login_required
@settings_required
def employee_move(emp_id):
    emp = get_or_404(DepartmentEmployee, emp_id)
    dept = db.session.get(ProductionDepartment, request.form.get('dept_id', type=int) or 0)
    if not dept:
        flash('Wybierz dział, do którego przenieść pracownika.', 'warning')
        return _employees_page(emp)
    if dept.id == emp.department_id:
        flash(f'{emp.name} jest już w dziale „{dept.name}”.', 'info')
        return _employees_page(emp)
    old_id = emp.department_id
    old = emp.department.name if emp.department else ''
    emp.department_id = dept.id
    db.session.commit()
    _audit('ustawienia_employee_move', 'DepartmentEmployee', emp.id, f'{emp.name}: {old} -> {dept.name}')
    flash(f'Przeniesiono {emp.name} z „{old}” do „{dept.name}”.', 'success')
    return redirect(url_for('ustawienia.admin_employees', dept_id=old_id))
