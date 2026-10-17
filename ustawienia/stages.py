"""Etapy produkcji, maszyny i bufory (Ustawienia produkcji, strona "Etapy i bufory")."""
import re

from flask import (render_template, redirect, url_for, request, flash, abort,
                   jsonify, send_file)
from flask_login import login_required
from sqlalchemy import func

from models import db, get_or_404, ProductionDepartment, ProductionStage, Machine, Buffer
from . import ustawienia_bp
from .routes import settings_required, _audit
from .lookup import norm_code, find_buffer
from .pdfs import buffer_labels_pdf

MAX_RANGE = 500


def _back():
    return redirect(url_for('ustawienia.stages'))


def _stage_ordered():
    return ProductionStage.query.order_by(ProductionStage.order, ProductionStage.id).all()


def _department_or_none(raw):
    if not raw:
        return None
    dept = db.session.get(ProductionDepartment, int(raw)) if str(raw).isdigit() else None
    return dept.id if dept else None


def _stage_or_none(raw):
    if not raw:
        return None
    stage = db.session.get(ProductionStage, int(raw)) if str(raw).isdigit() else None
    return stage.id if stage else None


# ── Strona ────────────────────────────────────────────────────────────────────

@ustawienia_bp.route('/etapy')
@login_required
@settings_required
def stages():
    departments = ProductionDepartment.query.filter_by(is_active=True).order_by(
        ProductionDepartment.order, ProductionDepartment.name).all()
    unassigned = Buffer.query.filter(Buffer.stage_id.is_(None)).order_by(Buffer.code).all()
    return render_template('ustawienia/stages.html', stages=_stage_ordered(),
                           departments=departments, unassigned=unassigned,
                           total_buffers=Buffer.query.count())


# ── Etapy ─────────────────────────────────────────────────────────────────────

@ustawienia_bp.route('/etapy/nowy', methods=['POST'])
@login_required
@settings_required
def stage_add():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Nazwa etapu jest wymagana.', 'warning')
        return _back()
    order = (db.session.query(func.max(ProductionStage.order)).scalar() or 0) + 1
    stage = ProductionStage(name=name[:64], order=order)
    db.session.add(stage)
    db.session.commit()
    _audit('ustawienia_stage_add', 'ProductionStage', stage.id, name)
    flash(f'Dodano etap „{name}”.', 'success')
    return _back()


@ustawienia_bp.route('/etapy/<int:stage_id>/zapisz', methods=['POST'])
@login_required
@settings_required
def stage_save(stage_id):
    stage = get_or_404(ProductionStage, stage_id)
    name = request.form.get('name', '').strip()
    if not name:
        flash('Nazwa etapu jest wymagana.', 'warning')
        return _back()
    level = request.form.get('level')
    stage.name = name[:64]
    stage.department_id = _department_or_none(request.form.get('department_id'))
    stage.level = level if level in ProductionStage.LEVEL_LABELS else 'element'
    stage.control_required = request.form.get('control_required') == '1'
    stage.is_active = request.form.get('is_active') == '1'
    db.session.commit()
    _audit('ustawienia_stage_edit', 'ProductionStage', stage.id, stage.name)
    flash(f'Zapisano etap „{stage.name}”.', 'success')
    return _back()


@ustawienia_bp.route('/etapy/<int:stage_id>/przesun/<direction>', methods=['POST'])
@login_required
@settings_required
def stage_move(stage_id, direction):
    stages_ = _stage_ordered()
    idx = next((i for i, s in enumerate(stages_) if s.id == stage_id), None)
    if idx is None:
        abort(404)
    swap = idx - 1 if direction == 'up' else idx + 1
    if 0 <= swap < len(stages_):
        stages_[idx], stages_[swap] = stages_[swap], stages_[idx]
    for i, s in enumerate(stages_, 1):
        s.order = i
    db.session.commit()
    _audit('ustawienia_stage_move', 'ProductionStage', stage_id, direction)
    return _back()


@ustawienia_bp.route('/etapy/<int:stage_id>/usun', methods=['POST'])
@login_required
@settings_required
def stage_delete(stage_id):
    stage = get_or_404(ProductionStage, stage_id)
    if stage.machines.count() or stage.buffers.count():
        flash(f'Nie można usunąć etapu „{stage.name}”: ma maszyny lub bufory. '
              f'Usuń je albo dezaktywuj etap.', 'warning')
        return _back()
    name = stage.name
    db.session.delete(stage)
    db.session.commit()
    _audit('ustawienia_stage_delete', 'ProductionStage', stage_id, name)
    flash(f'Usunięto etap „{name}”.', 'success')
    return _back()


# ── Maszyny ───────────────────────────────────────────────────────────────────

@ustawienia_bp.route('/maszyny/nowa', methods=['POST'])
@login_required
@settings_required
def machine_add():
    stage = get_or_404(ProductionStage, request.form.get('stage_id', type=int))
    name = request.form.get('name', '').strip()
    if not name:
        flash('Nazwa maszyny jest wymagana.', 'warning')
        return _back()
    if stage.machines.filter(func.lower(Machine.name) == name.lower()).first():
        flash(f'Maszyna „{name}” już istnieje w etapie „{stage.name}”.', 'warning')
        return _back()
    m = Machine(stage_id=stage.id, name=name[:64])
    db.session.add(m)
    db.session.commit()
    _audit('ustawienia_machine_add', 'Machine', m.id, f'{name} ({stage.name})')
    flash(f'Dodano maszynę „{name}”.', 'success')
    return _back()


@ustawienia_bp.route('/maszyny/<int:machine_id>/zapisz', methods=['POST'])
@login_required
@settings_required
def machine_save(machine_id):
    m = get_or_404(Machine, machine_id)
    name = request.form.get('name', '').strip()
    if not name:
        flash('Nazwa maszyny jest wymagana.', 'warning')
        return _back()
    m.name = name[:64]
    m.is_active = request.form.get('is_active') == '1'
    db.session.commit()
    _audit('ustawienia_machine_edit', 'Machine', m.id, m.name)
    flash(f'Zapisano maszynę „{m.name}”.', 'success')
    return _back()


@ustawienia_bp.route('/maszyny/<int:machine_id>/usun', methods=['POST'])
@login_required
@settings_required
def machine_delete(machine_id):
    m = get_or_404(Machine, machine_id)
    name = m.name
    db.session.delete(m)
    db.session.commit()
    _audit('ustawienia_machine_delete', 'Machine', machine_id, name)
    flash(f'Usunięto maszynę „{name}”.', 'success')
    return _back()


# ── Bufory ────────────────────────────────────────────────────────────────────

def _code_error(code, exclude_id=None):
    if not code:
        return 'Kod bufora jest wymagany.'
    other = Buffer.query.filter(Buffer.code == code)
    if exclude_id:
        other = other.filter(Buffer.id != exclude_id)
    if other.first():
        return f'Kod „{code}” ma już inny bufor.'
    return None


@ustawienia_bp.route('/bufory/nowy', methods=['POST'])
@login_required
@settings_required
def buffer_add():
    code = norm_code(request.form.get('code'))
    err = _code_error(code)
    if err:
        flash(err, 'warning')
        return _back()
    b = Buffer(code=code, name=request.form.get('name', '').strip()[:128] or None,
               number=request.form.get('number', '').strip()[:32] or None,
               stage_id=_stage_or_none(request.form.get('stage_id')))
    db.session.add(b)
    db.session.commit()
    _audit('ustawienia_buffer_add', 'Buffer', b.id, code)
    flash(f'Dodano bufor {code}.', 'success')
    return _back()


def _split_code(code):
    m = re.match(r'^(\D*)(\d+)$', code)
    return (m.group(1), m.group(2)) if m else None


@ustawienia_bp.route('/bufory/zakres', methods=['POST'])
@login_required
@settings_required
def buffer_range():
    """Zbiorcze dodanie buforów z zakresu kodów, np. A001 do A010 (do wczytania istniejących etykiet)."""
    first = _split_code(norm_code(request.form.get('from_code')))
    last = _split_code(norm_code(request.form.get('to_code')))
    if not first or not last or first[0] != last[0]:
        flash('Podaj zakres kodów z tym samym przedrostkiem i cyframi na końcu, np. A001 i A010.', 'warning')
        return _back()
    start, end, width = int(first[1]), int(last[1]), len(first[1])
    if end < start or end - start + 1 > MAX_RANGE:
        flash(f'Zakres musi być rosnący i liczyć najwyżej {MAX_RANGE} kodów.', 'warning')
        return _back()
    stage_id = _stage_or_none(request.form.get('stage_id'))
    existing = {c for (c,) in db.session.query(Buffer.code).all()}
    created = skipped = 0
    for n in range(start, end + 1):
        code = f'{first[0]}{n:0{width}d}'
        if code in existing:
            skipped += 1
            continue
        db.session.add(Buffer(code=code, stage_id=stage_id))
        created += 1
    db.session.commit()
    _audit('ustawienia_buffer_range', 'Buffer', None,
           f'{norm_code(request.form.get("from_code"))}-{norm_code(request.form.get("to_code"))} '
           f'dodano={created} pominięto={skipped}')
    flash(f'Dodano buforów: {created}' + (f', pominięto istniejące: {skipped}.' if skipped else '.'),
          'success')
    return _back()


@ustawienia_bp.route('/bufory/<int:buffer_id>/zapisz', methods=['POST'])
@login_required
@settings_required
def buffer_save(buffer_id):
    b = get_or_404(Buffer, buffer_id)
    code = norm_code(request.form.get('code'))
    err = _code_error(code, exclude_id=b.id)
    if err:
        flash(err, 'warning')
        return _back()
    old_code = b.code
    b.code = code
    b.name = request.form.get('name', '').strip()[:128] or None
    b.number = request.form.get('number', '').strip()[:32] or None
    b.stage_id = _stage_or_none(request.form.get('stage_id'))
    b.is_active = request.form.get('is_active') == '1'
    db.session.commit()
    _audit('ustawienia_buffer_edit', 'Buffer', b.id, f'{old_code} -> {code}' if old_code != code else code)
    if old_code != code:
        flash(f'Zmieniono kod bufora z {old_code} na {code}. Wydrukowana etykieta {old_code} '
              f'przestała pasować, wydrukuj nową.', 'warning')
    else:
        flash(f'Zapisano bufor {code}.', 'success')
    return _back()


@ustawienia_bp.route('/bufory/<int:buffer_id>/usun', methods=['POST'])
@login_required
@settings_required
def buffer_delete(buffer_id):
    b = get_or_404(Buffer, buffer_id)
    code = b.code
    db.session.delete(b)
    db.session.commit()
    _audit('ustawienia_buffer_delete', 'Buffer', buffer_id, code)
    flash(f'Usunięto bufor {code}.', 'success')
    return _back()


@ustawienia_bp.route('/bufory/znajdz')
@login_required
def buffer_lookup():
    """Bufor po kodzie z etykiety (JSON). Odczyt dla każdego zalogowanego, bo stan widzą wszyscy."""
    b = find_buffer(request.args.get('code'))
    if not b:
        return jsonify({'ok': False, 'error': 'Nie znaleziono bufora o tym kodzie'}), 404
    return jsonify({'ok': True, 'id': b.id, 'code': b.code, 'name': b.display_name,
                    'number': b.number, 'active': b.is_active,
                    'stage': {'id': b.stage.id, 'name': b.stage.name} if b.stage else None})


# ── Etykiety PDF (50x100 mm) ──────────────────────────────────────────────────

@ustawienia_bp.route('/bufory/etykiety.pdf')
@login_required
@settings_required
def buffer_labels():
    """Etykiety: `?ids=1,2,3`, `?stage_id=N` albo wszystkie bufory."""
    q = Buffer.query
    ids = [int(x) for x in request.args.get('ids', '').split(',') if x.strip().isdigit()]
    if ids:
        q = q.filter(Buffer.id.in_(ids))
    elif request.args.get('stage_id', type=int):
        q = q.filter(Buffer.stage_id == request.args.get('stage_id', type=int))
    buffers = q.order_by(Buffer.code).all()
    if not buffers:
        abort(404)
    return send_file(buffer_labels_pdf(buffers), mimetype='application/pdf',
                     download_name='etykiety_buforow.pdf')
