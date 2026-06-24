import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import (render_template, redirect, url_for, request,
                   flash, abort, current_app, make_response)
from flask_login import login_required, current_user

from models import db, AuditLog, SpawalniaOperator, GiecieOperator, CiecieOperator, SpawalniaRecord
from . import spawalnia_bp

UTC = timezone.utc


# ── Dekoratory dostępu ────────────────────────────────────────────────────────

def spawalnia_required(f):
    """admin + kontroler + spawacz"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_spawalnia_user:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def spawalnia_editor_required(f):
    """admin + kontroler (nie spawacz)"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_kontroler:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── Pomocnicze ────────────────────────────────────────────────────────────────

def _audit(action, target_type=None, target_id=None, detail=None):
    try:
        uid = current_user.id if current_user.is_authenticated else None
        db.session.add(AuditLog(
            user_id=uid, action=action, target_type=target_type,
            target_id=target_id, detail=detail, ip=request.remote_addr
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Audit error: %s', exc)


def _float(val):
    try:
        return float(val.replace(',', '.')) if val and val.strip() else None
    except (ValueError, AttributeError):
        return None


def _operators():
    return SpawalniaOperator.query.filter_by(is_active=True).order_by(SpawalniaOperator.initials).all()


def _giecie_operators():
    return GiecieOperator.query.filter_by(is_active=True).order_by(GiecieOperator.initials).all()


def _ciecie_operators():
    return CiecieOperator.query.filter_by(is_active=True).order_by(CiecieOperator.initials).all()


def _apply_fields(rec, form):
    op_id        = form.get('operator_id') or None
    giecie_op_id = form.get('giecie_operator_id') or None
    ciecie_op_id = form.get('ciecie_operator_id') or None
    rec.otworowanie        = form.get('otworowanie') or None
    rec.przekatna          = form.get('przekatna') or None
    rec.przekatna_odchylka = _float(form.get('przekatna_odchylka'))
    rec.pomiar1            = _float(form.get('pomiar1'))
    rec.pomiar2            = _float(form.get('pomiar2'))
    rec.pomiar3            = _float(form.get('pomiar3'))
    rec.jakosc_wyciecia    = form.get('jakosc_wyciecia') or None
    rec.operator_id        = int(op_id) if op_id else None
    rec.giecie_operator_id = int(giecie_op_id) if giecie_op_id else None
    rec.ciecie_operator_id = int(ciecie_op_id) if ciecie_op_id else None
    rec.updated_at         = datetime.now(UTC)


# ── Lista ─────────────────────────────────────────────────────────────────────

@spawalnia_bp.route('/')
@login_required
@spawalnia_required
def list_records():
    zo_filter = request.args.get('zo', '').strip()
    q = SpawalniaRecord.query.order_by(SpawalniaRecord.created_at.desc())
    if zo_filter:
        q = q.filter(SpawalniaRecord.zo_number.ilike(f'%{zo_filter}%'))
    records = q.all()

    # Group by ZO number, preserving newest-first order of groups.
    # Within each group sort by batch_index (batch records first, in sequence).
    zo_groups: dict = {}
    zo_order: list = []
    for rec in records:
        if rec.zo_number not in zo_groups:
            zo_groups[rec.zo_number] = []
            zo_order.append(rec.zo_number)
        zo_groups[rec.zo_number].append(rec)

    for zo in zo_order:
        zo_groups[zo].sort(
            key=lambda r: (r.batch_index if r.batch_index is not None else 9999,
                           r.created_at)
        )

    # For each group, find the first empty record (entry point for one-click fill)
    groups = []
    for zo in zo_order:
        recs = zo_groups[zo]
        first_empty = next((r for r in recs if r.is_empty), None)
        groups.append((zo, recs, first_empty))

    return render_template('spawalnia/list.html',
                           groups=groups, zo_filter=zo_filter,
                           total=len(records))


# ── Nowy wpis — tylko ZO + ilość ─────────────────────────────────────────────

@spawalnia_bp.route('/new', methods=['GET', 'POST'])
@login_required
@spawalnia_required
def new_record():
    if request.method == 'POST':
        zo = request.form.get('zo_number', '').strip()
        if not zo:
            flash('Numer ZO jest wymagany.', 'warning')
            return render_template('spawalnia/new.html',
                                   prefill_zo=request.form.get('zo_number', ''))

        quantity = max(1, min(99, int(request.form.get('quantity', 1) or 1)))
        bid = uuid.uuid4().hex if quantity > 1 else None
        first_id = None

        for i in range(1, quantity + 1):
            rec = SpawalniaRecord(
                zo_number   = zo,
                batch_id    = bid,
                batch_index = i if quantity > 1 else None,
                batch_total = quantity if quantity > 1 else None,
                created_by_id = current_user.id,
            )
            db.session.add(rec)
            db.session.flush()
            if i == 1:
                first_id = rec.id

        _audit('spawalnia_create', 'SpawalniaRecord', first_id,
               f'ZO={zo} qty={quantity}')
        db.session.commit()

        if quantity > 1:
            flash(
                f'Utworzono {quantity} listy kontrolne dla ZO {zo}. '
                f'Czekają na wypełnienie.',
                'success'
            )
        else:
            flash(f'Lista kontrolna dla ZO {zo} gotowa do wypełnienia.', 'success')
        return redirect(url_for('spawalnia.list_records', zo=zo))

    return render_template('spawalnia/new.html',
                           prefill_zo=request.args.get('zo', ''))



# ── Edycja / wypełnianie listy ────────────────────────────────────────────────

@spawalnia_bp.route('/<int:record_id>/edit', methods=['GET', 'POST'])
@login_required
@spawalnia_required
def edit_record(record_id):
    rec = SpawalniaRecord.query.get_or_404(record_id)
    ops        = _operators()
    giecie_ops = _giecie_operators()
    ciecie_ops = _ciecie_operators()

    if request.method == 'POST':
        zo = request.form.get('zo_number', '').strip()
        if not zo:
            flash('Numer ZO jest wymagany.', 'warning')
            return render_template('spawalnia/form.html', operators=ops,
                                   giecie_operators=giecie_ops, ciecie_operators=ciecie_ops,
                                   record=rec)

        rec.zo_number = zo
        _apply_fields(rec, request.form)
        _audit('spawalnia_edit', 'SpawalniaRecord', rec.id, f'ZO={zo}')
        db.session.commit()

        # Auto-przejście do następnego w serii
        if rec.batch_id and rec.batch_index and rec.batch_index < rec.batch_total:
            next_rec = SpawalniaRecord.query.filter_by(
                batch_id=rec.batch_id,
                batch_index=rec.batch_index + 1,
            ).first()
            if next_rec:
                flash(
                    f'Lista {rec.batch_index}/{rec.batch_total} zapisana. '
                    f'Wypełnij następną.', 'success'
                )
                return redirect(url_for('spawalnia.edit_record', record_id=next_rec.id))

        flash(f'Lista ZO {zo} zapisana.', 'success')
        return redirect(url_for('spawalnia.list_records'))

    return render_template('spawalnia/form.html', operators=ops,
                           giecie_operators=giecie_ops, ciecie_operators=ciecie_ops,
                           record=rec)


# ── Usuń pojedynczy wpis (admin + kontroler) ─────────────────────────────────

@spawalnia_bp.route('/<int:record_id>/delete', methods=['POST'])
@login_required
@spawalnia_editor_required
def delete_record(record_id):
    rec = SpawalniaRecord.query.get_or_404(record_id)
    zo = rec.zo_number
    _audit('spawalnia_delete', 'SpawalniaRecord', rec.id, f'ZO={zo}')
    db.session.delete(rec)
    db.session.commit()
    flash(f'Wpis ZO {zo} usunięty.', 'success')
    return redirect(url_for('spawalnia.list_records'))


# ── Usuń całą grupę ZO (admin + kontroler) ────────────────────────────────────

@spawalnia_bp.route('/group/delete', methods=['POST'])
@login_required
@spawalnia_editor_required
def delete_group():
    zo = request.form.get('zo_number', '').strip()
    if not zo:
        flash('Brak numeru ZO.', 'warning')
        return redirect(url_for('spawalnia.list_records'))

    recs = SpawalniaRecord.query.filter_by(zo_number=zo).all()
    count = len(recs)
    for rec in recs:
        db.session.delete(rec)
    _audit('spawalnia_delete_group', 'SpawalniaRecord',
           detail=f'ZO={zo} count={count}')
    db.session.commit()
    flash(f'Usunięto {count} list kontrolnych dla ZO {zo}.', 'success')
    return redirect(url_for('spawalnia.list_records'))


# ── Export PDF (admin + kontroler) ────────────────────────────────────────────

@spawalnia_bp.route('/export/pdf')
@login_required
@spawalnia_editor_required
def export_pdf():
    from .pdf_export import generate_pdf
    zo_filter = request.args.get('zo', '').strip()
    q = SpawalniaRecord.query.order_by(SpawalniaRecord.created_at.desc())
    if zo_filter:
        q = q.filter(SpawalniaRecord.zo_number.ilike(f'%{zo_filter}%'))
    records = q.all()
    pdf_bytes = generate_pdf(records, zo_filter)
    resp = make_response(pdf_bytes)
    resp.headers['Content-Type'] = 'application/pdf'
    fname = f'spawalnia_{zo_filter or "lista"}.pdf'
    resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp


# ── Export Excel (admin + kontroler) ─────────────────────────────────────────

@spawalnia_bp.route('/export/excel')
@login_required
@spawalnia_editor_required
def export_excel():
    from .excel_export import generate_excel
    zo_filter = request.args.get('zo', '').strip()
    q = SpawalniaRecord.query.order_by(SpawalniaRecord.created_at.desc())
    if zo_filter:
        q = q.filter(SpawalniaRecord.zo_number.ilike(f'%{zo_filter}%'))
    records = q.all()
    buf = generate_excel(records, zo_filter)
    resp = make_response(buf.getvalue())
    resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    fname = f'spawalnia_{zo_filter or "lista"}.xlsx'
    resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp


# ── Admin: wszyscy operatorzy (tylko admin) ───────────────────────────────────

_OP_MODELS = {
    'spawacz': (SpawalniaOperator, 'SpawalniaOperator', 'spawalnia_operator', 'Spawacz'),
    'giecie':  (GiecieOperator,    'GiecieOperator',    'giecie_operator',    'Gięcie'),
    'ciecie':  (CiecieOperator,    'CiecieOperator',    'ciecie_operator',    'Cięcie'),
}


@spawalnia_bp.route('/admin/operators', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_operators():
    if request.method == 'POST':
        action  = request.form.get('action')
        op_type = request.form.get('op_type', 'spawacz')
        Model, model_name, audit_prefix, label = _OP_MODELS.get(op_type, _OP_MODELS['spawacz'])

        if action == 'add':
            initials = request.form.get('initials', '').strip().upper()
            name     = request.form.get('name', '').strip()
            if not initials or not name:
                flash('Inicjały i imię są wymagane.', 'warning')
            elif Model.query.filter_by(initials=initials).first():
                flash(f'Inicjały {initials} już istnieją.', 'warning')
            else:
                op = Model(initials=initials, name=name)
                db.session.add(op)
                _audit(f'{audit_prefix}_add', model_name, detail=f'{initials} – {name}')
                db.session.commit()
                flash(f'Dodano operatora {label} {initials}.', 'success')

        elif action == 'toggle':
            op = Model.query.get_or_404(int(request.form.get('op_id', 0)))
            op.is_active = not op.is_active
            _audit(f'{audit_prefix}_toggle', model_name, op.id,
                   f'{op.initials} -> {"active" if op.is_active else "inactive"}')
            db.session.commit()
            flash(f'Operator {op.initials} {"aktywowany" if op.is_active else "dezaktywowany"}.', 'success')

        elif action == 'delete':
            op = Model.query.get_or_404(int(request.form.get('op_id', 0)))
            if op.records.count() > 0:
                flash(f'Nie można usunąć – operator {op.initials} ma przypisane wpisy.', 'warning')
            else:
                _audit(f'{audit_prefix}_delete', model_name, op.id, op.initials)
                db.session.delete(op)
                db.session.commit()
                flash(f'Operator {op.initials} usunięty.', 'success')

        return redirect(url_for('spawalnia.admin_operators'))

    spawacze = SpawalniaOperator.query.order_by(SpawalniaOperator.initials).all()
    giecie   = GiecieOperator.query.order_by(GiecieOperator.initials).all()
    ciecie   = CiecieOperator.query.order_by(CiecieOperator.initials).all()
    return render_template('spawalnia/admin_operators.html',
                           spawacze=spawacze, giecie=giecie, ciecie=ciecie)


# Przekierowania dla ewentualnych starych linków
@spawalnia_bp.route('/admin/giecie-operators')
@login_required
@admin_required
def admin_giecie_operators():
    return redirect(url_for('spawalnia.admin_operators'))


@spawalnia_bp.route('/admin/ciecie-operators')
@login_required
@admin_required
def admin_ciecie_operators():
    return redirect(url_for('spawalnia.admin_operators'))
