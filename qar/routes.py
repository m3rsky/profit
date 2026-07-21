import os
import uuid
from datetime import datetime, timezone

from flask import (render_template, redirect, url_for, request,
                   flash, abort, current_app, jsonify, send_from_directory)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import (db, get_or_404, AuditLog, QARReport, QARPhoto,
                    ProductionDepartment, DepartmentEmployee,
                    RoutingCard, RoutingCardStage)
from . import qar_bp

UTC = timezone.utc


# ── Dostęp ────────────────────────────────────────────────────────────────────

def _audit(action, target_id=None, detail=None):
    try:
        db.session.add(AuditLog(
            user_id=current_user.id, action=action,
            target_type='qar_report', target_id=target_id,
            detail=detail, ip=request.remote_addr,
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('QAR audit error: %s', exc)


def _qar_access(report):
    """Zwraca True jeśli użytkownik może zobaczyć raport."""
    return (current_user.is_admin or current_user.is_kontroler or current_user.is_konstruktor
            or report.user_id == current_user.id)


def _qar_edit_access(report):
    """Zwraca True jeśli użytkownik może edytować raport."""
    if current_user.is_admin:
        return True
    if report.status == 'closed':
        return False
    return current_user.is_kontroler or current_user.is_konstruktor or report.user_id == current_user.id


def _allowed_image(filename):
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


def _employee_departments():
    """Aktywne działy z aktywnymi pracownikami — do dropdownu pogrupowanego po działach."""
    return (ProductionDepartment.query
            .filter_by(is_active=True)
            .order_by(ProductionDepartment.order, ProductionDepartment.name)
            .all())


def _parse_employee_id(raw):
    """Zwraca id istniejącego pracownika albo None (puste/nieprawidłowe pole)."""
    try:
        emp_id = int(raw)
    except (TypeError, ValueError):
        return None
    return emp_id if db.session.get(DepartmentEmployee, emp_id) else None


def _next_qar_number():
    year = datetime.now().year
    prefix = f'QAR-{year}-'
    last = (QARReport.query
            .filter(QARReport.number.like(f'{prefix}%'))
            .order_by(QARReport.number.desc())
            .first())
    if last:
        try:
            seq = int(last.number.rsplit('-', 1)[-1]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
    return f'{prefix}{seq:04d}'


# ── Lista ─────────────────────────────────────────────────────────────────────

@qar_bp.route('/')
@login_required
def list_reports():
    page       = request.args.get('page', 1, type=int)
    per_page   = request.args.get('per_page', 20, type=int)
    if per_page not in (10, 20, 50):
        per_page = 20
    status_f   = request.args.get('status', '')
    category_f = request.args.get('category', '')
    employee_f = request.args.get('employee', type=int)
    search_q   = request.args.get('q', '').strip()
    date_from  = request.args.get('date_from', '')
    date_to    = request.args.get('date_to', '')

    if current_user.is_admin or current_user.is_kontroler or current_user.is_konstruktor:
        q = QARReport.query
    else:
        q = QARReport.query.filter_by(user_id=current_user.id)

    if status_f in ('open', 'in_progress', 'closed'):
        q = q.filter_by(status=status_f)
    if category_f:
        q = q.filter_by(category=category_f)
    if employee_f:
        q = q.filter_by(employee_id=employee_f)
    if search_q:
        q = q.filter(
            QARReport.title.ilike(f'%{search_q}%') |
            QARReport.number.ilike(f'%{search_q}%') |
            QARReport.location.ilike(f'%{search_q}%') |
            QARReport.zo_number.ilike(f'%{search_q}%') |
            QARReport.drawing_number.ilike(f'%{search_q}%')
        )
    if date_from:
        try:
            q = q.filter(QARReport.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
        except ValueError:
            pass
    if date_to:
        from datetime import timedelta
        try:
            q = q.filter(QARReport.created_at < datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass

    reports = q.order_by(QARReport.created_at.desc()).paginate(page=page, per_page=per_page)
    filters = dict(status=status_f, category=category_f, q=search_q,
                   employee=employee_f or '',
                   date_from=date_from, date_to=date_to, per_page=per_page)
    return render_template('qar/list.html', reports=reports,
                           categories=QARReport.CATEGORIES, filters=filters,
                           departments=_employee_departments())


# ── Nowy raport ───────────────────────────────────────────────────────────────

@qar_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_report():
    if request.method == 'POST':
        title          = request.form.get('title', '').strip()
        zo_number      = request.form.get('zo_number', '').strip()
        drawing_number = request.form.get('drawing_number', '').strip()
        category       = request.form.get('category', '').strip()
        location       = request.form.get('location', '').strip()
        description    = request.form.get('description', '').strip()
        employee_id    = _parse_employee_id(request.form.get('employee_id'))
        if not title or not description:
            flash('Tytuł i opis problemu są wymagane.', 'error')
            return render_template('qar/new.html', categories=QARReport.CATEGORIES,
                                   form=request.form, departments=_employee_departments())
        report = QARReport(
            number=_next_qar_number(),
            zo_number=zo_number or None,
            drawing_number=drawing_number or None,
            title=title,
            category=category or None,
            location=location or None,
            description=description,
            employee_id=employee_id,
            user_id=current_user.id,
        )
        db.session.add(report)
        db.session.commit()
        _audit('qar_create', report.id, f'number={report.number}')
        flash(f'Raport {report.number} został utworzony.', 'success')
        return redirect(url_for('qar.detail_report', report_id=report.id))
    return render_template('qar/new.html', categories=QARReport.CATEGORIES, form={},
                           departments=_employee_departments())


# ── Szczegóły ─────────────────────────────────────────────────────────────────

@qar_bp.route('/<int:report_id>')
@login_required
def detail_report(report_id):
    report = get_or_404(QARReport, report_id)
    if not _qar_access(report):
        abort(403)
    return render_template('qar/detail.html', report=report,
                           upload_folder=current_app.config.get('QAR_UPLOAD_FOLDER', ''))


# ── Edycja ────────────────────────────────────────────────────────────────────

@qar_bp.route('/<int:report_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_report(report_id):
    report = get_or_404(QARReport, report_id)
    if not _qar_access(report):
        abort(403)
    if not _qar_edit_access(report):
        flash('Zamknięty raport może edytować tylko administrator.', 'warning')
        return redirect(url_for('qar.detail_report', report_id=report_id))
    if request.method == 'POST':
        title          = request.form.get('title', '').strip()
        zo_number      = request.form.get('zo_number', '').strip()
        drawing_number = request.form.get('drawing_number', '').strip()
        category       = request.form.get('category', '').strip()
        location       = request.form.get('location', '').strip()
        description    = request.form.get('description', '').strip()
        findings       = request.form.get('findings', '').strip()
        resolution     = request.form.get('resolution', '').strip()
        status         = request.form.get('status', report.status)
        if not title or not description:
            flash('Tytuł i opis problemu są wymagane.', 'error')
            return render_template('qar/edit.html', report=report,
                                   categories=QARReport.CATEGORIES,
                                   departments=_employee_departments())
        if status not in ('open', 'in_progress', 'closed'):
            status = report.status
        report.title          = title
        report.zo_number      = zo_number or None
        report.drawing_number = drawing_number or None
        report.category       = category or None
        report.location       = location or None
        report.description    = description
        report.employee_id    = _parse_employee_id(request.form.get('employee_id'))
        report.findings       = findings or None
        report.resolution     = resolution or None
        report.updated_at     = datetime.now(UTC)
        if status == 'closed' and report.status != 'closed':
            report.status       = 'closed'
            report.verified_by_id = current_user.id
            report.verified_at  = datetime.now(UTC)
        elif status != 'closed':
            report.status = status
            if report.verified_by_id and status != 'closed':
                report.verified_by_id = None
                report.verified_at    = None
        db.session.commit()
        _audit('qar_edit', report.id, f'status={report.status}')
        flash('Raport został zaktualizowany.', 'success')
        return redirect(url_for('qar.detail_report', report_id=report.id))
    return render_template('qar/edit.html', report=report, categories=QARReport.CATEGORIES,
                           departments=_employee_departments())


# ── Zamknięcie raportu ────────────────────────────────────────────────────────

@qar_bp.route('/<int:report_id>/close', methods=['POST'])
@login_required
def close_report(report_id):
    report = get_or_404(QARReport, report_id)
    if not _qar_access(report):
        abort(403)
    if report.status == 'closed':
        flash('Raport jest już zamknięty.', 'info')
        return redirect(url_for('qar.detail_report', report_id=report_id))
    report.status         = 'closed'
    report.verified_by_id = current_user.id
    report.verified_at    = datetime.now(UTC)
    report.updated_at     = datetime.now(UTC)
    db.session.commit()
    _audit('qar_close', report.id)
    flash('Raport został zamknięty.', 'success')
    return redirect(url_for('qar.detail_report', report_id=report_id))


# ── Wznowienie raportu (admin, konstruktor) ───────────────────────────────────

@qar_bp.route('/<int:report_id>/reopen', methods=['POST'])
@login_required
def reopen_report(report_id):
    if not (current_user.is_admin or current_user.is_konstruktor):
        abort(403)
    report = get_or_404(QARReport, report_id)
    report.status         = 'in_progress'
    report.verified_by_id = None
    report.verified_at    = None
    report.updated_at     = datetime.now(UTC)
    db.session.commit()
    _audit('qar_reopen', report.id)
    flash('Raport wznowiony.', 'success')
    return redirect(url_for('qar.detail_report', report_id=report_id))


# ── Usunięcie (admin) ─────────────────────────────────────────────────────────

@qar_bp.route('/<int:report_id>/delete', methods=['POST'])
@login_required
def delete_report(report_id):
    if not current_user.is_admin:
        abort(403)
    report = get_or_404(QARReport, report_id)
    upload_dir = current_app.config.get('QAR_UPLOAD_FOLDER', '')
    for photo in report.photos.all():
        path = os.path.join(upload_dir, photo.filename)
        if os.path.exists(path):
            os.remove(path)
    number = report.number
    db.session.delete(report)
    db.session.commit()
    _audit('qar_delete', report_id, f'number={number}')
    flash(f'Raport {number} został usunięty.', 'success')
    return redirect(url_for('qar.list_reports'))


# ── Upload zdjęcia (AJAX) ─────────────────────────────────────────────────────

@qar_bp.route('/<int:report_id>/photo', methods=['POST'])
@login_required
def upload_photo(report_id):
    report = get_or_404(QARReport, report_id)
    if not _qar_access(report):
        return jsonify({'error': 'Forbidden'}), 403
    if report.status == 'closed' and not current_user.is_admin:
        return jsonify({'error': 'Raport jest zamknięty'}), 400
    file = request.files.get('photo')
    if not file or not _allowed_image(file.filename):
        return jsonify({'error': 'Nieprawidłowy plik'}), 400
    if not _verify_image(file):
        return jsonify({'error': 'Plik nie jest prawidłowym obrazem'}), 400
    ext = file.filename.rsplit('.', 1)[1].lower()
    unique_name = f'qar_{uuid.uuid4().hex}.{ext}'
    upload_dir = current_app.config.get('QAR_UPLOAD_FOLDER', '')
    os.makedirs(upload_dir, exist_ok=True)
    file.save(os.path.join(upload_dir, unique_name))
    caption = request.form.get('caption', '').strip() or None
    photo = QARPhoto(
        report_id=report_id,
        filename=unique_name,
        original_name=secure_filename(file.filename),
        caption=caption,
    )
    db.session.add(photo)
    report.updated_at = datetime.now(UTC)
    db.session.commit()
    return jsonify({
        'ok': True,
        'photo_id': photo.id,
        'url': url_for('qar.serve_photo', filename=unique_name),
    })


# ── Usunięcie zdjęcia (AJAX) ──────────────────────────────────────────────────

@qar_bp.route('/photo/<int:photo_id>', methods=['DELETE'])
@login_required
def delete_photo(photo_id):
    photo = get_or_404(QARPhoto, photo_id)
    report = photo.report
    if not _qar_access(report):
        return jsonify({'error': 'Forbidden'}), 403
    if report.status == 'closed' and not current_user.is_admin:
        return jsonify({'error': 'Raport jest zamknięty'}), 400
    upload_dir = current_app.config.get('QAR_UPLOAD_FOLDER', '')
    path = os.path.join(upload_dir, photo.filename)
    if os.path.exists(path):
        os.remove(path)
    db.session.delete(photo)
    report.updated_at = datetime.now(UTC)
    db.session.commit()
    return jsonify({'ok': True})


# ── Aktualizacja podpisu zdjęcia (AJAX) ──────────────────────────────────────

@qar_bp.route('/photo/<int:photo_id>/caption', methods=['POST'])
@login_required
def update_caption(photo_id):
    photo = get_or_404(QARPhoto, photo_id)
    report = photo.report
    if not _qar_access(report):
        return jsonify({'error': 'Forbidden'}), 403
    if report.status == 'closed' and not current_user.is_admin:
        return jsonify({'error': 'Raport jest zamknięty'}), 400
    data = request.get_json(silent=True) or {}
    photo.caption = data.get('caption', '').strip() or None
    db.session.commit()
    return jsonify({'ok': True})


# ── Serwowanie zdjęć ──────────────────────────────────────────────────────────

@qar_bp.route('/uploads/<filename>')
@login_required
def serve_photo(filename):
    upload_dir = current_app.config.get('QAR_UPLOAD_FOLDER', '')
    return send_from_directory(upload_dir, filename)


# ── Podpowiedź pracowników z marszruty (AJAX) ────────────────────────────────

@qar_bp.route('/suggest-employees')
@login_required
def suggest_employees():
    """Pracownicy przypisani do etapów karty marszrutowej o podanym numerze ZO."""
    zo = request.args.get('zo', '').strip()
    if not zo:
        return jsonify({'employees': []})
    card = (RoutingCard.query
            .filter(RoutingCard.identifier == zo)
            .order_by(RoutingCard.created_at.desc())
            .first())
    if not card:
        return jsonify({'employees': []})
    seen, employees = set(), []
    for stage in card.stages:
        emp = stage.employee
        if emp and emp.id not in seen:
            seen.add(emp.id)
            employees.append({
                'id': emp.id,
                'name': emp.name,
                'department': stage.department.name if stage.department else '',
            })
    return jsonify({'employees': employees})


# ── Statystyki błędów per pracownik ──────────────────────────────────────────

@qar_bp.route('/stats')
@login_required
def stats():
    if not (current_user.is_admin or current_user.is_kontroler or current_user.is_konstruktor):
        abort(403)
    from sqlalchemy import func, case
    date_from = request.args.get('date_from', '')
    date_to   = request.args.get('date_to', '')

    unassigned_q = QARReport.query.filter(QARReport.employee_id.is_(None))

    q = (db.session.query(
            DepartmentEmployee.id.label('emp_id'),
            DepartmentEmployee.name.label('emp_name'),
            ProductionDepartment.name.label('dept_name'),
            func.count(QARReport.id).label('total'),
            func.sum(case((QARReport.status == 'open', 1), else_=0)).label('open'),
            func.sum(case((QARReport.status == 'in_progress', 1), else_=0)).label('in_progress'),
            func.sum(case((QARReport.status == 'closed', 1), else_=0)).label('closed'),
        )
        .join(QARReport, QARReport.employee_id == DepartmentEmployee.id)
        .join(ProductionDepartment, DepartmentEmployee.department_id == ProductionDepartment.id))

    cat_q = (db.session.query(
                QARReport.employee_id,
                QARReport.category,
                func.count(QARReport.id).label('cnt'),
            )
            .filter(QARReport.employee_id.isnot(None)))

    if date_from:
        try:
            dt = datetime.strptime(date_from, '%Y-%m-%d')
            q     = q.filter(QARReport.created_at >= dt)
            cat_q = cat_q.filter(QARReport.created_at >= dt)
            unassigned_q = unassigned_q.filter(QARReport.created_at >= dt)
        except ValueError:
            date_from = ''
    if date_to:
        from datetime import timedelta
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            q     = q.filter(QARReport.created_at < dt)
            cat_q = cat_q.filter(QARReport.created_at < dt)
            unassigned_q = unassigned_q.filter(QARReport.created_at < dt)
        except ValueError:
            date_to = ''

    rows = (q.group_by(DepartmentEmployee.id)
             .order_by(func.count(QARReport.id).desc(), DepartmentEmployee.name)
             .all())

    categories = {}
    for emp_id, category, cnt in cat_q.group_by(QARReport.employee_id, QARReport.category).all():
        categories.setdefault(emp_id, []).append((category or 'Bez kategorii', cnt))
    for cats in categories.values():
        cats.sort(key=lambda c: -c[1])

    unassigned = unassigned_q.count()
    return render_template('qar/stats.html', rows=rows, categories=categories,
                           unassigned=unassigned,
                           date_from=date_from, date_to=date_to)


# ── PDF ───────────────────────────────────────────────────────────────────────

@qar_bp.route('/<int:report_id>/pdf')
@login_required
def export_pdf(report_id):
    from .pdf_export import generate_qar_pdf
    report = get_or_404(QARReport, report_id)
    if not _qar_access(report):
        abort(403)
    upload_dir = current_app.config.get('QAR_UPLOAD_FOLDER', '')
    return generate_qar_pdf(report, upload_dir)
