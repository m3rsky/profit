from datetime import datetime, timezone

from flask import render_template, redirect, url_for, request, flash, abort, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func

from models import db, AuditLog, QATask
from . import zadania_qa_bp

UTC = timezone.utc


def _access():
    return current_user.is_kontroler


def _audit(action, target_id=None, detail=None):
    try:
        db.session.add(AuditLog(
            user_id=current_user.id, action=action,
            target_type='qa_task', target_id=target_id,
            detail=detail, ip=request.remote_addr,
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        from flask import current_app
        current_app.logger.error('Zadania QA audit error: %s', exc)


# ── Lista ─────────────────────────────────────────────────────────────────────

@zadania_qa_bp.route('/')
@login_required
def list_tasks():
    if not _access():
        abort(403)
    tasks = QATask.query.filter_by(is_active=True).order_by(QATask.order).all()
    today = datetime.now().strftime('%d.%m.%Y')
    return render_template('zadania_qa/list.html', tasks=tasks, today=today)


# ── Nowe zadanie ──────────────────────────────────────────────────────────────

@zadania_qa_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_task():
    if not _access():
        abort(403)
    if request.method == 'POST':
        title       = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        if not title:
            flash('Nazwa zadania jest wymagana.', 'error')
            return render_template('zadania_qa/new.html', form=request.form)
        max_order = db.session.query(func.max(QATask.order)).scalar() or 0
        task = QATask(
            title=title, description=description or None,
            order=max_order + 1, updated_by_id=current_user.id,
        )
        db.session.add(task)
        db.session.commit()
        _audit('qa_task_create', task.id, title)
        flash('Zadanie zostało dodane.', 'success')
        return redirect(url_for('zadania_qa.list_tasks'))
    return render_template('zadania_qa/new.html', form={})


# ── Edycja zadania ────────────────────────────────────────────────────────────

@zadania_qa_bp.route('/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_task(task_id):
    if not _access():
        abort(403)
    task = QATask.query.get_or_404(task_id)
    if request.method == 'POST':
        title       = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        if not title:
            flash('Nazwa zadania jest wymagana.', 'error')
            return render_template('zadania_qa/edit.html', task=task)
        task.title         = title
        task.description   = description or None
        task.updated_by_id = current_user.id
        task.updated_at    = datetime.now(UTC)
        db.session.commit()
        _audit('qa_task_edit', task.id, title)
        flash('Zadanie zostało zaktualizowane.', 'success')
        return redirect(url_for('zadania_qa.list_tasks'))
    return render_template('zadania_qa/edit.html', task=task)


# ── Usunięcie zadania ─────────────────────────────────────────────────────────

@zadania_qa_bp.route('/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    if not _access():
        abort(403)
    task = QATask.query.get_or_404(task_id)
    title = task.title
    db.session.delete(task)
    db.session.commit()
    _audit('qa_task_delete', task_id, title)
    flash(f'Zadanie „{title}” zostało usunięte.', 'success')
    return redirect(url_for('zadania_qa.list_tasks'))


# ── Zmiana kolejności ─────────────────────────────────────────────────────────

@zadania_qa_bp.route('/<int:task_id>/move', methods=['POST'])
@login_required
def move_task(task_id):
    if not _access():
        abort(403)
    direction = request.form.get('direction')
    tasks = QATask.query.filter_by(is_active=True).order_by(QATask.order).all()
    idx = next((i for i, t in enumerate(tasks) if t.id == task_id), None)
    if idx is not None:
        if direction == 'up' and idx > 0:
            other = tasks[idx - 1]
        elif direction == 'down' and idx < len(tasks) - 1:
            other = tasks[idx + 1]
        else:
            other = None
        if other is not None:
            tasks[idx].order, other.order = other.order, tasks[idx].order
            db.session.commit()
    return redirect(url_for('zadania_qa.list_tasks'))


# ── AJAX: przełącz wykonanie ──────────────────────────────────────────────────

@zadania_qa_bp.route('/<int:task_id>/toggle', methods=['POST'])
@login_required
def toggle_task(task_id):
    if not _access():
        return jsonify({'error': 'Forbidden'}), 403
    task = QATask.query.get_or_404(task_id)
    task.is_done       = not task.is_done
    task.updated_by_id = current_user.id
    task.updated_at    = datetime.now(UTC)
    db.session.commit()
    return jsonify({'ok': True, 'is_done': task.is_done})


# ── AJAX: zapisz uwagi ────────────────────────────────────────────────────────

@zadania_qa_bp.route('/<int:task_id>/notes', methods=['POST'])
@login_required
def update_notes(task_id):
    if not _access():
        return jsonify({'error': 'Forbidden'}), 403
    task = QATask.query.get_or_404(task_id)
    data = request.get_json(silent=True) or {}
    task.notes         = (data.get('notes') or '').strip() or None
    task.updated_by_id = current_user.id
    task.updated_at    = datetime.now(UTC)
    db.session.commit()
    return jsonify({'ok': True})


# ── PDF do wydruku ────────────────────────────────────────────────────────────

@zadania_qa_bp.route('/pdf')
@login_required
def export_pdf():
    if not _access():
        abort(403)
    from .pdf_export import generate_qa_tasks_pdf
    tasks = QATask.query.filter_by(is_active=True).order_by(QATask.order).all()
    header = {
        'date':       request.args.get('date', '').strip() or datetime.now().strftime('%d.%m.%Y'),
        'shift':      request.args.get('shift', '').strip(),
        'controller': request.args.get('controller', '').strip() or current_user.username,
        'area':       request.args.get('area', '').strip() or 'Produkcja rozdzielnic / obudowy',
    }
    return generate_qa_tasks_pdf(tasks, header)
