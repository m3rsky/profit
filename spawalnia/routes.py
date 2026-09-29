import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import (render_template, redirect, url_for, request,
                   flash, abort, current_app, make_response, jsonify)
from flask_login import login_required, current_user

from models import (db, get_or_404, AuditLog, SpawalniaRecord, InterstageCheck,
                    DepartmentEmployee, ProductionDepartment, QARReport,
                    INTERSTAGE_STAGES, ensure_interstage_checks)
from . import spawalnia_bp

UTC = timezone.utc


def _clean_zo(raw: str) -> str:
    """Usuwa query string który mógł zostać doklejony przez serwer proxy."""
    return raw.split('?')[0].strip()


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


def _stage_employees():
    """stage_key -> aktywni pracownicy działów Marszruty przypisanych do etapu.
    Gdy żaden dział etapu nie ma pracowników (albo nazwy działów się nie zgadzają),
    etap dostaje wszystkich aktywnych pracowników, żeby zawsze dało się kogoś wskazać."""
    emps = (DepartmentEmployee.query.filter_by(is_active=True)
            .join(ProductionDepartment)
            .filter(ProductionDepartment.is_active.is_(True))
            .order_by(DepartmentEmployee.name).all())
    result = {}
    for st in INTERSTAGE_STAGES:
        names = {n.lower() for n in st['departments']}
        matched = [e for e in emps if e.department.name.lower() in names]
        result[st['key']] = matched or emps
    return result


def _create_records(zo, quantity, user_id, product_name=None, client=None):
    """Tworzy `quantity` wpisów kontroli (seria, gdy > 1) wraz z wierszami etapów.
    Zwraca listę rekordów (bez commitu)."""
    bid = uuid.uuid4().hex if quantity > 1 else None
    recs = []
    for i in range(1, quantity + 1):
        rec = SpawalniaRecord(
            zo_number=zo, batch_id=bid,
            batch_index=i if quantity > 1 else None,
            batch_total=quantity if quantity > 1 else None,
            product_name=product_name or None, client=client or None,
            created_by_id=user_id,
        )
        db.session.add(rec)
        db.session.flush()
        ensure_interstage_checks(rec)
        recs.append(rec)
    return recs


def _create_qar_for_check(rec, check, stage):
    """Tworzy raport QAR dla etapu ocenionego jako NG i wiąże go z kontrolą."""
    from qar.routes import _next_qar_number, _category_names
    label = stage['label']
    extra = ' | '.join(x for x in (rec.product_name, rec.client) if x)
    description = check.notes or (
        f'Niezgodność (NG) stwierdzona podczas kontroli międzyetapowej, etap {label}.')
    if extra:
        description += ' | Zlecenie: ' + extra
    category = stage['qar_category'] if stage['qar_category'] in _category_names() else None
    qar = QARReport(
        number=_next_qar_number(),
        zo_number=rec.zo_number,
        title=f'Kontrola międzyetapowa NG: {label}, ZO {rec.zo_number}',
        category=category,
        location=label,
        description=description,
        employee_id=check.employee_id,
        user_id=current_user.id,
    )
    db.session.add(qar)
    db.session.flush()
    check.qar_report_id = qar.id
    return qar


def _apply_checks(rec, form):
    """Zapisuje oceny etapów z formularza. Zwraca listę numerów utworzonych QAR."""
    ensure_interstage_checks(rec)
    db.session.flush()
    valid_emp = {e.id for e in DepartmentEmployee.query.all()}
    created = []
    now = datetime.now(UTC)
    by_stage = {c.stage: c for c in rec.checks}
    for st in INTERSTAGE_STAGES:
        key = st['key']
        check = by_stage[key]
        result = form.get(f'result_{key}') or None
        if result not in (None, 'OK', 'NG'):
            result = None
        emp_raw = form.get(f'employee_{key}') or ''
        emp_id = int(emp_raw) if emp_raw.isdigit() and int(emp_raw) in valid_emp else None
        notes = (form.get(f'notes_{key}') or '').strip() or None

        changed = (check.result, check.employee_id, check.notes) != (result, emp_id, notes)
        check.result, check.employee_id, check.notes = result, emp_id, notes
        if changed:
            check.checked_by_id = current_user.id if result else None
            check.checked_at = now if result else None
        if result == 'NG' and not check.qar_report_id:
            created.append(_create_qar_for_check(rec, check, st).number)
    rec.updated_at = now
    return created


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
                           total=len(records), stages=INTERSTAGE_STAGES)


# ── Szybkie dodanie wpisu do istniejącej grupy ZO (przycisk "+") ─────────────

@spawalnia_bp.route('/add_to_zo', methods=['POST'])
@login_required
@spawalnia_required
def add_to_zo():
    zo = _clean_zo(request.form.get('zo_number', ''))
    if not zo:
        flash('Brak numeru ZO.', 'warning')
        return redirect(url_for('spawalnia.list_records'))

    template = (SpawalniaRecord.query.filter_by(zo_number=zo)
                .order_by(SpawalniaRecord.created_at.desc()).first())
    rec = _create_records(zo, 1, current_user.id,
                          template.product_name if template else None,
                          template.client if template else None)[0]
    _audit('spawalnia_create', 'SpawalniaRecord', rec.id, f'ZO={zo} (quick-add)')
    db.session.commit()

    flash(f'Dodano nowy wpis do ZO {zo}.', 'success')
    return redirect(url_for('spawalnia.list_records', zo=zo))


# ── Nowy wpis: ZO + ilość (ręcznie) ──────────────────────────────────────────

@spawalnia_bp.route('/new', methods=['GET', 'POST'])
@login_required
@spawalnia_required
def new_record():
    if request.method == 'POST':
        zo = _clean_zo(request.form.get('zo_number', ''))
        if not zo:
            flash('Numer ZO jest wymagany.', 'warning')
            return render_template('spawalnia/new.html',
                                   prefill_zo=request.form.get('zo_number', ''))

        quantity = max(1, min(99, int(request.form.get('quantity', 1) or 1)))
        recs = _create_records(zo, quantity, current_user.id)
        _audit('spawalnia_create', 'SpawalniaRecord', recs[0].id,
               f'ZO={zo} qty={quantity}')
        db.session.commit()

        if quantity > 1:
            flash(f'Utworzono {quantity} wpisy kontroli dla ZO {zo}. '
                  f'Czekają na wypełnienie.', 'success')
        else:
            flash(f'Kontrola dla ZO {zo} gotowa do wypełnienia.', 'success')
        return redirect(url_for('spawalnia.list_records', zo=zo))

    return render_template('spawalnia/new.html',
                           prefill_zo=_clean_zo(request.args.get('zo', '')))


# ── Skan QR (ten sam kod co kontrola końcowa: p, c, o, q) ────────────────────

@spawalnia_bp.route('/scan')
@login_required
@spawalnia_required
def scan_page():
    return render_template('spawalnia/scan.html')


@spawalnia_bp.route('/from-qr', methods=['POST'])
@login_required
@spawalnia_required
def from_qr():
    data         = request.get_json(silent=True) or {}
    product_name = (data.get('p') or '').strip()
    client       = (data.get('c') or '').strip()
    zo           = _clean_zo(str(data.get('o') or ''))
    try:
        quantity = max(1, min(99, int(data.get('q', 1))))
    except (TypeError, ValueError):
        quantity = 1

    if not zo:
        return jsonify({'error': 'Brak numeru zlecenia („o") w kodzie QR'}), 400

    existing = (SpawalniaRecord.query.filter_by(zo_number=zo)
                .order_by(SpawalniaRecord.batch_index, SpawalniaRecord.created_at).all())
    if existing:
        target = next((r for r in existing if r.is_empty), existing[0])
        _audit('spawalnia_scan_existing', 'SpawalniaRecord', target.id, f'ZO={zo}')
        return jsonify({
            'ok': True,
            'redirect': url_for('spawalnia.edit_record', record_id=target.id),
            'msg': f'Kontrola dla ZO {zo} już istnieje, otwieram.',
        })

    recs = _create_records(zo, quantity, current_user.id, product_name or None, client or None)
    _audit('spawalnia_create', 'SpawalniaRecord', recs[0].id,
           f'ZO={zo} qty={quantity} product={product_name} (QR)')
    db.session.commit()
    return jsonify({
        'ok': True,
        'redirect': url_for('spawalnia.edit_record', record_id=recs[0].id),
        'msg': (f'Utworzono {quantity} wpisów kontroli.' if quantity > 1
                else 'Utworzono wpis kontroli.'),
    })


# ── Edycja / wypełnianie kontroli etapów ─────────────────────────────────────

@spawalnia_bp.route('/<int:record_id>/edit', methods=['GET', 'POST'])
@login_required
@spawalnia_required
def edit_record(record_id):
    rec = get_or_404(SpawalniaRecord, record_id)

    if request.method == 'POST':
        created_qar = _apply_checks(rec, request.form)
        _audit('spawalnia_edit', 'SpawalniaRecord', rec.id,
               f'ZO={rec.zo_number} qar={",".join(created_qar) or "-"}')
        db.session.commit()

        if created_qar:
            flash('Utworzono raport QAR: ' + ', '.join(created_qar) +
                  '. Uzupełnij opis i zdjęcia w module QAR.', 'warning')

        # Auto-przejście do następnego w serii
        if rec.batch_id and rec.batch_index and rec.batch_index < rec.batch_total:
            next_rec = SpawalniaRecord.query.filter_by(
                batch_id=rec.batch_id,
                batch_index=rec.batch_index + 1,
            ).first()
            if next_rec:
                flash(f'Wpis {rec.batch_index}/{rec.batch_total} zapisany. '
                      f'Wypełnij następny.', 'success')
                return redirect(url_for('spawalnia.edit_record', record_id=next_rec.id))

        flash(f'Kontrola ZO {rec.zo_number} zapisana.', 'success')
        return redirect(url_for('spawalnia.list_records'))

    ensure_interstage_checks(rec)
    db.session.commit()
    return render_template('spawalnia/form.html', record=rec, stages=INTERSTAGE_STAGES,
                           checks=rec.check_map, stage_employees=_stage_employees())


# ── Usuń pojedynczy wpis (admin + kontroler) ─────────────────────────────────

@spawalnia_bp.route('/<int:record_id>/delete', methods=['POST'])
@login_required
@spawalnia_editor_required
def delete_record(record_id):
    rec = get_or_404(SpawalniaRecord, record_id)
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
    flash(f'Usunięto {count} wpisów kontroli dla ZO {zo}.', 'success')
    return redirect(url_for('spawalnia.list_records'))


# ── Eksporty (admin + kontroler) ─────────────────────────────────────────────

def _filtered_records():
    zo_filter = request.args.get('zo', '').strip()
    q = SpawalniaRecord.query.order_by(SpawalniaRecord.created_at.desc())
    if zo_filter:
        q = q.filter(SpawalniaRecord.zo_number.ilike(f'%{zo_filter}%'))
    return zo_filter, q.all()


@spawalnia_bp.route('/export/pdf')
@login_required
@spawalnia_editor_required
def export_pdf():
    from .pdf_export import generate_pdf
    zo_filter, records = _filtered_records()
    resp = make_response(generate_pdf(records, zo_filter))
    resp.headers['Content-Type'] = 'application/pdf'
    fname = f'kontrola_miedzyetapowa_{zo_filter or "lista"}.pdf'
    resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp


@spawalnia_bp.route('/export/excel')
@login_required
@spawalnia_editor_required
def export_excel():
    from .excel_export import generate_excel
    zo_filter, records = _filtered_records()
    buf = generate_excel(records, zo_filter)
    resp = make_response(buf.getvalue())
    resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    fname = f'kontrola_miedzyetapowa_{zo_filter or "lista"}.xlsx'
    resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp


# ── Osoby: wspólna lista pracowników działów z modułu Marszruta ──────────────

@spawalnia_bp.route('/admin/operators')
@login_required
@spawalnia_editor_required
def admin_operators():
    return redirect(url_for('marszruta.admin_departments'))
