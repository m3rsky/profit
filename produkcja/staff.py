"""Baza pracowników produkcji, przydziały, plakietki QR, konfiguracja etapów, historia."""
import io
import re
import secrets

from flask import (render_template, redirect, url_for, request, flash, abort,
                   send_file, current_app)
from flask_login import login_required
from sqlalchemy import func

from models import (db, get_or_404, ProdEmployee, ProdEmployeeDept, ProdStageColumn,
                    ProdEntry, ProdSetting, ProdSheet, ProductionDepartment)
from . import produkcja_bp
from .routes import planning_required, admin_only, _audit, _sheet_or_403

BADGE_PREFIX = 'PSH-EMP:'


def _departments():
    return ProductionDepartment.query.filter_by(is_active=True).order_by(
        ProductionDepartment.order, ProductionDepartment.name).all()


def _new_token():
    return secrets.token_hex(16)


def norm_code(code):
    """Kod plakietki do porównań: bez spacji, wielkimi literami ("JR 12" = "jr12")."""
    return ''.join(str(code or '').split()).upper()[:64]


def _code_taken(code, exclude_id=None):
    q = ProdEmployee.query.filter(ProdEmployee.badge_code == code)
    if exclude_id:
        q = q.filter(ProdEmployee.id != exclude_id)
    return q.first()


def _valid_form(name, initials, badge_code='', exclude_id=None):
    if not name or not initials:
        return 'Imię i nazwisko oraz inicjały są wymagane.'
    if badge_code:
        other = _code_taken(badge_code, exclude_id)
        if other:
            return f'Kod plakietki „{badge_code}” ma już {other.name}.'
    return None


def _initials_warning(initials, exclude_id=None):
    clash = ProdEmployee.query.filter(func.lower(ProdEmployee.initials) == initials.lower(),
                                      ProdEmployee.is_active.is_(True))
    if exclude_id:
        clash = clash.filter(ProdEmployee.id != exclude_id)
    other = clash.first()
    if other:
        flash(f'Uwaga: inicjały „{initials}” ma też {other.name}. W komórce Excela obaj będą wyglądać tak samo.',
              'warning')


def _save_departments(emp, ids):
    valid = {d.id for d in _departments()}
    ProdEmployeeDept.query.filter_by(employee_id=emp.id).delete()
    for did in ids:
        if did in valid:
            db.session.add(ProdEmployeeDept(employee_id=emp.id, department_id=did))


# ── Pracownicy ────────────────────────────────────────────────────────────────

@produkcja_bp.route('/pracownicy')
@login_required
@planning_required
def employees():
    items = ProdEmployee.query.order_by(ProdEmployee.is_active.desc(), ProdEmployee.name).all()
    dept_names = {}
    for e in items:
        dept_names[e.id] = ', '.join(d.department.name for d in e.departments.all())
    return render_template('produkcja/employees.html', employees=items, dept_names=dept_names,
                           enforce=ProdSetting.get('enforce_assignments', '0') == '1')


@produkcja_bp.route('/pracownicy/nowy', methods=['GET', 'POST'])
@login_required
@planning_required
def employee_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        badge_code = norm_code(request.form.get('badge_code'))
        initials = request.form.get('initials', '').strip()
        err = _valid_form(name, initials, badge_code)
        if err:
            flash(err, 'error')
        else:
            emp = ProdEmployee(name=name, initials=initials, token=_new_token(),
                               badge_code=badge_code or None,
                               notes=request.form.get('notes', '').strip() or None)
            db.session.add(emp)
            db.session.flush()
            _save_departments(emp, [int(x) for x in request.form.getlist('departments') if x.isdigit()])
            db.session.commit()
            _audit('prod_employee_add', 'ProdEmployee', emp.id, f'{name} ({initials}) kod={badge_code}')
            flash('Dodano pracownika.', 'success')
            _initials_warning(initials, exclude_id=emp.id)
            return redirect(url_for('produkcja.employees'))
    return render_template('produkcja/employee_form.html', emp=None, departments=_departments(),
                           selected=set(), form=request.form)


@produkcja_bp.route('/pracownicy/<int:emp_id>/edytuj', methods=['GET', 'POST'])
@login_required
@planning_required
def employee_edit(emp_id):
    emp = get_or_404(ProdEmployee, emp_id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        badge_code = norm_code(request.form.get('badge_code'))
        initials = request.form.get('initials', '').strip()
        active = request.form.get('is_active') == '1'
        err = _valid_form(name, initials, badge_code, exclude_id=emp.id)
        if err:
            flash(err, 'error')
        else:
            emp.name, emp.initials, emp.is_active = name, initials, active
            emp.badge_code = badge_code or None
            emp.notes = request.form.get('notes', '').strip() or None
            _save_departments(emp, [int(x) for x in request.form.getlist('departments') if x.isdigit()])
            db.session.commit()
            _audit('prod_employee_edit', 'ProdEmployee', emp.id, f'{name} ({initials}) active={active}')
            flash('Zapisano zmiany.', 'success')
            if active:
                _initials_warning(initials, exclude_id=emp.id)
            return redirect(url_for('produkcja.employees'))
    return render_template('produkcja/employee_form.html', emp=emp, departments=_departments(),
                           selected=emp.department_ids, form=request.form)


@produkcja_bp.route('/pracownicy/import', methods=['GET', 'POST'])
@login_required
@planning_required
def employees_bulk():
    """Zbiorcze dodanie pracowników z istniejącymi plakietkami: linia = kod;imię i nazwisko[;inicjały]."""
    report = None
    if request.method == 'POST':
        added, skipped, errors = 0, [], []
        seen = set()
        for n, line in enumerate((request.form.get('lines') or '').splitlines(), 1):
            if not line.strip():
                continue
            parts = [p.strip() for p in re.split(r'[;\t]', line)]
            code = norm_code(parts[0])
            name = parts[1] if len(parts) > 1 else ''
            initials = parts[2] if len(parts) > 2 else ''
            if not code or not name or not initials:
                errors.append(f'Linia {n}: potrzebny kod, imię i nazwisko oraz inicjały ({line.strip()[:40]})')
            elif code in seen or _code_taken(code):
                skipped.append(f'{code} ({name}): kod już istnieje')
            else:
                seen.add(code)
                db.session.add(ProdEmployee(name=name, initials=initials[:16], badge_code=code,
                                            token=_new_token()))
                added += 1
        db.session.commit()
        _audit('prod_employee_bulk', 'ProdEmployee', None, f'added={added} skipped={len(skipped)}')
        report = {'added': added, 'skipped': skipped, 'errors': errors}
    return render_template('produkcja/employees_bulk.html', report=report)


@produkcja_bp.route('/pracownicy/<int:emp_id>/usun', methods=['POST'])
@login_required
@planning_required
def employee_delete(emp_id):
    """Usuwa pracownika z bazy. Wpisy w historii zostają, z zapisanym imieniem autora;
    inicjały już wpisane w komórki arkuszy nie są zmieniane."""
    emp = get_or_404(ProdEmployee, emp_id)
    name, initials = emp.name, emp.initials
    ProdEntry.query.filter_by(employee_id=emp.id).update(
        {'employee_name': name, 'employee_id': None}, synchronize_session=False)
    db.session.delete(emp)   # przydziały do działów usuwa kaskada
    db.session.commit()
    _audit('prod_employee_delete', 'ProdEmployee', emp_id, f'{name} ({initials})')
    flash(f'Usunięto pracownika: {name}. Jego plakietka przestała działać.', 'success')
    return redirect(url_for('produkcja.employees'))


@produkcja_bp.route('/pracownicy/<int:emp_id>/token', methods=['POST'])
@login_required
@planning_required
def employee_new_token(emp_id):
    emp = get_or_404(ProdEmployee, emp_id)
    emp.token = _new_token()
    db.session.commit()
    _audit('prod_employee_token', 'ProdEmployee', emp.id, emp.name)
    flash(f'Wygenerowano nowy kod dla: {emp.name}. Stara plakietka przestała działać.', 'success')
    return redirect(url_for('produkcja.employees'))


@produkcja_bp.route('/pracownicy/ustawienia', methods=['POST'])
@login_required
@admin_only
def employees_settings():
    on = request.form.get('enforce_assignments') == '1'
    ProdSetting.set('enforce_assignments', '1' if on else '0')
    db.session.commit()
    _audit('prod_settings', 'ProdSetting', None, f'enforce_assignments={int(on)}')
    flash('Wpisy ograniczone do przydziałów.' if on else 'Każdy pracownik może wpisać się w każdy etap.',
          'success')
    return redirect(url_for('produkcja.employees'))


# ── Plakietki QR (PDF) ────────────────────────────────────────────────────────

def _badges_pdf(emps):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF
    from reportlab.pdfbase import pdfmetrics
    try:
        import pdf_generator
        pdf_generator._register_fonts()
        font, bold = 'DVSans', 'DVSans-Bold'
        pdfmetrics.getFont(bold)
    except Exception:
        font, bold = 'Helvetica', 'Helvetica-Bold'

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    cw, ch = 90 * mm, 54 * mm
    cols, rows = 2, 5
    x0 = (page_w - cols * cw) / 2
    y0 = (page_h - rows * ch) / 2
    for i, emp in enumerate(emps):
        slot = i % (cols * rows)
        if i and slot == 0:
            c.showPage()
        x = x0 + (slot % cols) * cw
        y = page_h - y0 - (slot // cols + 1) * ch
        c.setStrokeColorRGB(.6, .6, .6)
        c.setDash(2, 2)
        c.rect(x, y, cw, ch)
        c.setDash()
        qr = QrCodeWidget(BADGE_PREFIX + emp.token)
        b = qr.getBounds()
        size = 38 * mm
        d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
        d.add(qr)
        renderPDF.draw(d, c, x + 4 * mm, y + (ch - size) / 2)
        tx = x + 45 * mm
        c.setFillColorRGB(0, 0, 0)
        c.setFont(bold, 22)
        c.drawString(tx, y + ch - 20 * mm, emp.initials[:8])
        c.setFont(font, 10)
        name = emp.name
        while pdfmetrics.stringWidth(name, font, 10) > cw - 49 * mm and len(name) > 4:
            name = name[:-1]
        c.drawString(tx, y + ch - 28 * mm, name)
        c.setFont(font, 7)
        c.setFillColorRGB(.4, .4, .4)
        c.drawString(tx, y + 6 * mm, 'Skanuj przed wpisem etapu')
    c.save()
    buf.seek(0)
    return buf


@produkcja_bp.route('/pracownicy/<int:emp_id>/plakietka.pdf')
@login_required
@planning_required
def employee_badge(emp_id):
    emp = get_or_404(ProdEmployee, emp_id)
    return send_file(_badges_pdf([emp]), mimetype='application/pdf',
                     download_name=f'plakietka_{re.sub(r"[^A-Za-z0-9]", "", emp.initials) or emp.id}.pdf')


@produkcja_bp.route('/pracownicy/plakietki.pdf')
@login_required
@planning_required
def employee_badges_all():
    emps = ProdEmployee.query.filter_by(is_active=True).order_by(ProdEmployee.name).all()
    if not emps:
        abort(404)
    return send_file(_badges_pdf(emps), mimetype='application/pdf', download_name='plakietki.pdf')


# ── Kolumny arkusza: nazwy, role i etapy ──────────────────────────────────────

def _column_examples(sheet, limit=4, scan_rows=400):
    """Kilka różnych, niepustych wartości z każdej kolumny (żeby było wiadomo, co w niej jest)."""
    import json
    from models import ProdRow
    from .routes import _display
    ncols = len(sheet.columns)
    found = [[] for _ in range(ncols)]
    rows = ProdRow.query.filter_by(sheet_id=sheet.id).order_by(ProdRow.position).limit(scan_rows).all()
    # próbka z początku i końca arkusza, bo układ starszych i nowszych wierszy bywa różny
    tail = ProdRow.query.filter_by(sheet_id=sheet.id).order_by(ProdRow.position.desc()).limit(scan_rows).all()
    for r in rows + tail:
        for j, v in enumerate(json.loads(r.data or '[]')[:ncols]):
            txt = _display(v)
            if txt and txt not in found[j] and len(found[j]) < limit:
                found[j].append(txt[:24])
    return found


@produkcja_bp.route('/sheet/<int:sheet_id>/etapy', methods=['GET', 'POST'])
@login_required
@planning_required
def sheet_stages(sheet_id):
    import json
    from models import ProdRow
    from . import excel_io, sheetcfg
    sheet = _sheet_or_403(sheet_id)
    columns = sheet.columns
    existing = {s.col_index: s for s in ProdStageColumn.query.filter_by(sheet_id=sheet.id).all()}
    cfg = sheetcfg.load(sheet.name)
    if request.method == 'POST':
        n = len(columns)
        valid_depts = {d.id for d in _departments()}
        # nazwy kolumn
        new_names = list(columns)
        for idx in range(n):
            nm = (request.form.get(f'name_{idx}') or '').strip()[:64]
            if nm:
                new_names[idx] = nm
        for idx in range(n):
            orig = sheetcfg.orig_name(cfg, columns, idx)
            if new_names[idx] != orig:
                cfg['names'][str(idx)] = {'orig': orig, 'name': new_names[idx]}
            else:
                cfg['names'].pop(str(idx), None)
        # role kolumn (pole logiczne -> kolumna), każda rola tylko raz
        mapping = {}
        for idx in range(n):
            role = request.form.get(f'role_{idx}')
            if role in excel_io.ROLE_LABELS:
                mapping[role] = idx
        cfg['mapping'] = {role: {'idx': idx, 'orig': sheetcfg.orig_name(cfg, columns, idx)}
                          for role, idx in mapping.items()}
        mapping_changed = mapping != sheet.mapping
        sheet.columns_json = json.dumps(new_names, ensure_ascii=False)
        sheet.mapping_json = json.dumps(mapping)
        sheetcfg.save(sheet.name, cfg)
        # etapy
        chosen = {int(x) for x in request.form.getlist('stage') if x.isdigit() and int(x) < n}
        for idx in list(existing):
            if idx not in chosen:
                db.session.delete(existing.pop(idx))
        for idx in sorted(chosen):
            label = (request.form.get(f'label_{idx}') or '').strip()[:64]
            if not label or label == columns[idx]:   # nazwa etapu podąża za nazwą kolumny
                label = new_names[idx]
            dept = request.form.get(f'dept_{idx}', type=int)
            dept = dept if dept in valid_depts else None
            st = existing.get(idx)
            if st:
                st.label, st.department_id, st.is_active = label, dept, True
            else:
                db.session.add(ProdStageColumn(sheet_id=sheet.id, col_index=idx, label=label,
                                               department_id=dept, is_active=True))
        if mapping_changed:   # ZO zależy od roli kolumn, więc odświeżamy je w całym arkuszu
            from .routes import _store_row_fields, _data
            for row in ProdRow.query.filter_by(sheet_id=sheet.id).all():
                _store_row_fields(row, _data(row, n), mapping)
        db.session.commit()
        _audit('prod_columns_update', 'ProdSheet', sheet.id,
               f'sheet={sheet.name} stages={len(chosen)} roles={len(mapping)}')
        flash('Zapisano ustawienia kolumn.', 'success')
        return redirect(url_for('produkcja.sheet_stages', sheet_id=sheet.id))
    from . import excel_io as eio
    role_of = {idx: role for role, idx in sheet.mapping.items()}
    return render_template('produkcja/stages.html', sheet=sheet, columns=columns,
                           existing=existing, departments=_departments(),
                           examples=_column_examples(sheet), roles=eio.ROLE_LABELS,
                           role_of=role_of, headerless=sheet.header_row is None)


# ── Historia wpisów ───────────────────────────────────────────────────────────

@produkcja_bp.route('/historia')
@login_required
@planning_required
def history():
    q = (request.args.get('q') or '').strip()
    query = ProdEntry.query
    if q:
        like = f'%{q}%'
        query = query.outerjoin(ProdEmployee, ProdEntry.employee_id == ProdEmployee.id).filter(
            db.or_(ProdEntry.zo.ilike(like), ProdEntry.product.ilike(like),
                   ProdEntry.sheet_name.ilike(like), ProdEmployee.name.ilike(like),
                   ProdEmployee.initials.ilike(like)))
    entries = query.order_by(ProdEntry.created_at.desc()).limit(300).all()
    return render_template('produkcja/history.html', entries=entries, q=q)
