import json
import os
import re
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import (render_template, redirect, url_for, request, flash,
                   abort, jsonify, current_app, send_file)
from flask_login import login_required, current_user
from sqlalchemy import func

from models import (db, get_or_404, AuditLog, User, Order, Report,
                    ProdFile, ProdSheet, ProdRow, ProdSheetAccess, ProdStageColumn, ProdEntry)
from . import produkcja_bp, helper
from . import excel_io, sheetcfg

UTC = timezone.utc
PER_PAGE = 100
ISO_DATE = excel_io.ISO_DATE


# ── Dostęp ────────────────────────────────────────────────────────────────────

def planning_required(f):
    """admin + rola Planowanie."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_planning:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def admin_only(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _accessible_sheet_ids():
    """None = wszystkie (admin), inaczej zbiór id arkuszy przydzielonych użytkownikowi."""
    if current_user.is_admin:
        return None
    rows = ProdSheetAccess.query.filter_by(user_id=current_user.id).all()
    return {r.sheet_id for r in rows}


def _sheet_or_403(sheet_id):
    sheet = get_or_404(ProdSheet, sheet_id)
    ids = _accessible_sheet_ids()
    if ids is not None and sheet.id not in ids:
        abort(403)
    return sheet


def _row_or_403(row_id):
    row = get_or_404(ProdRow, row_id)
    _sheet_or_403(row.sheet_id)
    return row


def _audit(action, target_type=None, target_id=None, detail=None):
    try:
        db.session.add(AuditLog(
            user_id=current_user.id, action=action, target_type=target_type,
            target_id=target_id, detail=detail, ip=request.remote_addr,
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Produkcja audit error: %s', exc)


def _folder():
    path = current_app.config['PRODUKCJA_FOLDER']
    os.makedirs(path, exist_ok=True)
    return path


# ── Pomocnicze ────────────────────────────────────────────────────────────────

def _display(v):
    """Wartość komórki -> tekst w tabeli (daty jako dd.mm.rrrr)."""
    if v is None:
        return ''
    if isinstance(v, str) and ISO_DATE.match(v):
        return f'{v[8:10]}.{v[5:7]}.{v[:4]}'
    if isinstance(v, float):
        return f'{v:g}'
    return str(v)


def _data(row, ncols):
    data = json.loads(row.data or '[]')
    if len(data) < ncols:
        data = data + [None] * (ncols - len(data))
    return data


def _as_int(v, default=1):
    m = re.match(r'\s*(\d+)', str(v)) if v is not None else None
    return max(1, int(m.group(1))) if m else default


def _store_row_fields(row, data, mapping):
    """Odświeża kolumny pomocnicze wiersza (ZO, szukajka) po zmianie danych."""
    fields = excel_io.row_fields(data, mapping)
    zo = fields.get('zo')
    row.zo = str(zo).strip()[:64] if zo not in (None, '') else None
    row.data = json.dumps(excel_io._trim(list(data)), ensure_ascii=False)
    row.search = excel_io.build_search(data)
    row.updated_at = datetime.now(UTC)


def _pairing(rows):
    """Powiązane listy kontrolne (Raporty QA) dla wierszy strony: po zamówieniu
    (order_id) oraz po kluczu QR (produkt|klient|ZO) listy utworzonej ze skanu."""
    qr_key = helper('qr_key')
    keys, order_ids = {}, set()
    for r in rows:
        if r.order_id:
            order_ids.add(r.order_id)
        sheet = r.sheet
        f = excel_io.row_fields(json.loads(r.data or '[]'), sheet.mapping)
        k = qr_key(f.get('product') or '', f.get('client') or '', f.get('zo') or '')
        if k:
            keys[r.id] = k
    by_order, by_key = {}, {}
    if order_ids or keys:
        q = Report.query.filter(Report.report_type == 'kontroler')
        conds = []
        if order_ids:
            conds.append(Report.order_id.in_(order_ids))
        if keys:
            conds.append(Report.qr_key.in_(set(keys.values())))
        from sqlalchemy import or_
        for rep in q.filter(or_(*conds)).all():
            st = rep.stats
            for bucket, kk in ((by_order, rep.order_id), (by_key, rep.qr_key)):
                if kk is None:
                    continue
                b = bucket.setdefault(kk, {'total': 0, 'done': 0, 'ng': 0, 'ids': []})
                b['total'] += 1
                b['done'] += 1 if rep.status == 'completed' else 0
                b['ng'] += 1 if st['ng'] else 0
                b['ids'].append(rep.id)
    result = {}
    for r in rows:
        info = by_order.get(r.order_id) if r.order_id else None
        if not info and keys.get(r.id):
            info = by_key.get(keys[r.id])
        if info:
            result[r.id] = info
    return result


# ── Widoki ────────────────────────────────────────────────────────────────────

@produkcja_bp.route('/')
@login_required
@planning_required
def index():
    ids = _accessible_sheet_ids()
    q = ProdSheet.query.order_by(ProdSheet.position)
    if ids is not None:
        q = q.filter(ProdSheet.id.in_(ids)) if ids else q.filter(db.false())
    sheets = q.all()
    counts = dict(db.session.query(ProdRow.sheet_id, func.count(ProdRow.id))
                  .group_by(ProdRow.sheet_id).all())
    linked = dict(db.session.query(ProdRow.sheet_id, func.count(ProdRow.id))
                  .filter(ProdRow.order_id.isnot(None)).group_by(ProdRow.sheet_id).all())
    has_data = ProdSheet.query.first() is not None
    return render_template('produkcja/index.html', sheets=sheets, counts=counts,
                           linked=linked, has_data=has_data)


@produkcja_bp.route('/sheet/<int:sheet_id>')
@login_required
@planning_required
def sheet_view(sheet_id):
    sheet = _sheet_or_403(sheet_id)
    q = (request.args.get('q') or '').strip().lower()
    state = request.args.get('state', '')
    page = request.args.get('page', 1, type=int)
    query = ProdRow.query.filter_by(sheet_id=sheet.id)
    if q:
        esc = q.replace('!', '!!').replace('%', '!%').replace('_', '!_')
        query = query.filter(ProdRow.search.like(f'%{esc}%', escape='!'))
    if state == 'linked':
        query = query.filter(ProdRow.order_id.isnot(None))
    elif state == 'unlinked':
        query = query.filter(ProdRow.order_id.is_(None))
    pag = query.order_by(ProdRow.position).paginate(page=page, per_page=PER_PAGE, error_out=False)
    ncols = len(sheet.columns)
    rows = [{'row': r, 'cells': [_display(v) for v in _data(r, ncols)]} for r in pag.items]
    pairing = _pairing(pag.items)
    return render_template('produkcja/sheet.html', sheet=sheet, rows=rows, pag=pag,
                           q=q, state=state, pairing=pairing,
                           formula_cols=set(sheet.formula_cols),
                           mapping=sheet.mapping)


@produkcja_bp.route('/row/<int:row_id>/cell', methods=['POST'])
@login_required
@planning_required
def cell_update(row_id):
    row = _row_or_403(row_id)
    sheet = row.sheet
    payload = request.get_json(silent=True) or {}
    try:
        col = int(payload.get('col'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Nieprawidłowa kolumna'}), 400
    ncols = len(sheet.columns)
    if col < 0 or col >= ncols:
        return jsonify({'error': 'Nieprawidłowa kolumna'}), 400
    if col in sheet.formula_cols:
        return jsonify({'error': 'Ta kolumna jest liczona formułą w Excelu.'}), 400
    value = excel_io.parse_input(payload.get('value'))
    data = _data(row, ncols)
    if data[col] == value:
        return jsonify({'ok': True, 'cells': {}})
    data[col] = value
    changed = set(json.loads(row.changed)) if row.changed else set()
    changed.add(col)
    row.changed = json.dumps(sorted(changed))
    updated = {col: _display(value)}
    m = sheet.mapping
    if ({'due', 'order_date', 'lead_days'} <= set(m) and m['due'] in sheet.formula_cols
            and col in (m['order_date'], m['lead_days'])):
        start, days = data[m['order_date']], data[m['lead_days']]
        if isinstance(start, str) and ISO_DATE.match(start) and isinstance(days, (int, float)):
            data[m['due']] = excel_io.add_workdays(start, days)
            updated[m['due']] = _display(data[m['due']])
    _store_row_fields(row, data, m)
    db.session.commit()
    _audit('prod_cell_edit', 'ProdRow', row.id,
           f'sheet={sheet.name} row={row.position} col={sheet.columns[col]} value={value}')
    return jsonify({'ok': True, 'cells': updated})


@produkcja_bp.route('/sheet/<int:sheet_id>/new', methods=['GET', 'POST'])
@login_required
@planning_required
def row_new(sheet_id):
    sheet = _sheet_or_403(sheet_id)
    m = sheet.mapping
    fields = [(k, label) for k, label in (
        ('order_date', 'Data zamówienia'), ('product', 'Nazwa towaru'), ('qty', 'Ilość'),
        ('client', 'Odbiorca'), ('zo', 'Numer ZO'), ('color', 'Kolor'),
        ('lead_days', 'Czas realizacji (dni robocze)')) if k in m]
    if request.method == 'POST':
        ncols = len(sheet.columns)
        data = [None] * ncols
        changed = []
        for key, _label in fields:
            val = excel_io.parse_input(request.form.get(key))
            if val is not None:
                data[m[key]] = val
                changed.append(m[key])
        if 'product' in m and data[m['product']] is None:
            flash('Nazwa towaru jest wymagana.', 'error')
            return render_template('produkcja/row_new.html', sheet=sheet, fields=fields,
                                   form=request.form, today=datetime.now().strftime('%Y-%m-%d'))
        if (m.get('due') in sheet.formula_cols and 'order_date' in m and 'lead_days' in m):
            start, days = data[m['order_date']], data[m['lead_days']]
            if isinstance(start, str) and ISO_DATE.match(start) and isinstance(days, (int, float)):
                data[m['due']] = excel_io.add_workdays(start, days)
        last = db.session.query(func.max(ProdRow.position)).filter_by(sheet_id=sheet.id).scalar()
        row = ProdRow(sheet_id=sheet.id, position=(last or sheet.header_row or 0) + 1, src_row=None,
                      changed=json.dumps(changed))
        _store_row_fields(row, data, m)
        db.session.add(row)
        db.session.commit()
        _audit('prod_row_add', 'ProdRow', row.id, f'sheet={sheet.name} row={row.position}')
        flash('Dodano zlecenie.', 'success')
        pages = max(1, -(-ProdRow.query.filter_by(sheet_id=sheet.id).count() // PER_PAGE))
        return redirect(url_for('produkcja.sheet_view', sheet_id=sheet.id, page=pages) + f'#row-{row.id}')
    return render_template('produkcja/row_new.html', sheet=sheet, fields=fields, form={},
                           today=datetime.now().strftime('%Y-%m-%d'))


def _delete_rows(sheet, rows):
    deleted = sheet.deleted_rows
    for r in rows:
        if r.src_row is not None:
            deleted.append(r.src_row)
        ProdEntry.query.filter_by(row_id=r.id).update({'row_id': None})
        db.session.delete(r)
    sheet.deleted_rows_json = json.dumps(sorted(set(deleted)))


@produkcja_bp.route('/sheet/<int:sheet_id>/delete', methods=['POST'])
@login_required
@planning_required
def rows_delete(sheet_id):
    sheet = _sheet_or_403(sheet_id)
    ids = [int(x) for x in request.form.getlist('row_ids') if x.isdigit()]
    rows = ProdRow.query.filter(ProdRow.sheet_id == sheet.id, ProdRow.id.in_(ids)).all() if ids else []
    if not rows:
        flash('Nie zaznaczono zleceń do usunięcia.', 'info')
    else:
        _delete_rows(sheet, rows)
        db.session.commit()
        _audit('prod_row_delete', 'ProdSheet', sheet.id, f'sheet={sheet.name} count={len(rows)}')
        flash(f'Usunięto zleceń: {len(rows)}.', 'success')
    return redirect(request.referrer or url_for('produkcja.sheet_view', sheet_id=sheet.id))


# ── Listy kontrolne z zamówień ────────────────────────────────────────────────

def _create_checklists_for_row(row):
    """Tworzy zamówienie i listy kontrolne (Raporty QA) z wiersza listy produkcyjnej.
    Zwraca (ok, komunikat)."""
    sheet = row.sheet
    f = excel_io.row_fields(_data(row, len(sheet.columns)), sheet.mapping)
    product = (str(f.get('product')).strip() if f.get('product') is not None else '')
    client = (str(f.get('client')).strip() if f.get('client') is not None else '')
    zo = (str(f.get('zo')).strip() if f.get('zo') is not None else '')
    if not product:
        return False, f'Wiersz {row.position}: brak nazwy towaru.'
    qty = min(99, _as_int(f.get('qty')))
    due = None
    if isinstance(f.get('due'), str) and ISO_DATE.match(f['due']):
        due = datetime.strptime(f['due'], '%Y-%m-%d').date()

    qr_key = helper('qr_key')
    key = qr_key(product, client, zo)
    existing = helper('find_qr_reports')(product, client, zo) if key else []
    if row.order_id:
        existing = existing or Report.query.filter_by(order_id=row.order_id).all()
    if existing:
        return False, f'{product}: listy kontrolne już istnieją ({len(existing)}).'

    tpl = helper('find_matching_template')(product, 'kontroler')
    if not tpl:
        return False, f'{product}: brak pasującego szablonu QA.'
    monter_tpl = helper('find_matching_template')(product, 'monter')

    number = zo or f'PROD-{sheet.id}-{row.id}'
    order = Order.query.filter_by(number=number).first()
    if order and (order.product_name != product or order.client != (client or '-')):
        number = f'{number}-{row.id}'
        order = None
    if not order:
        order = Order(number=number, product_name=product, client=client or '-', quantity=qty,
                      due_date=due, notes=f'Z listy produkcyjnej: {sheet.name}, wiersz {row.position}',
                      created_by_id=current_user.id)
        db.session.add(order)
        db.session.flush()
    helper('create_order_reports')(order, tpl, monter_tpl, user_id=current_user.id)
    if key:
        for rep in Report.query.filter_by(order_id=order.id, report_type='kontroler').all():
            rep.qr_key = key
    row.order_id = order.id
    return True, f'{product}: utworzono {order.quantity} list kontrolnych (szablon: {tpl.name}).'


@produkcja_bp.route('/sheet/<int:sheet_id>/checklists', methods=['POST'])
@login_required
@planning_required
def create_checklists(sheet_id):
    sheet = _sheet_or_403(sheet_id)
    ids = [int(x) for x in request.form.getlist('row_ids') if x.isdigit()]
    rows = (ProdRow.query.filter(ProdRow.sheet_id == sheet.id, ProdRow.id.in_(ids))
            .order_by(ProdRow.position).all()) if ids else []
    if not rows:
        flash('Zaznacz zlecenia, dla których utworzyć listy kontrolne.', 'info')
        return redirect(request.referrer or url_for('produkcja.sheet_view', sheet_id=sheet.id))
    created = 0
    for row in rows:
        try:
            ok, msg = _create_checklists_for_row(row)
            if ok:
                db.session.commit()
                created += 1
                _audit('prod_checklists_create', 'ProdRow', row.id, msg)
                flash(msg, 'success')
            else:
                db.session.rollback()
                flash(msg, 'warning')
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error('Produkcja listy kontrolne: %s', exc)
            flash(f'Wiersz {row.position}: błąd tworzenia list kontrolnych.', 'error')
    return redirect(request.referrer or url_for('produkcja.sheet_view', sheet_id=sheet.id))


# ── Wczytanie pliku, kopie ────────────────────────────────────────────────────

def _save_file(kind, original_name, note=None):
    stored = f'{kind}_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}.xlsx'
    rec = ProdFile(kind=kind, original_name=original_name, stored_name=stored, note=note,
                   created_by_id=current_user.id)
    return rec, os.path.join(_folder(), stored)


def _current_source():
    """Plik źródłowy ostatniego wczytania (na nim opiera się eksport)."""
    sheet = ProdSheet.query.filter(ProdSheet.file_id.isnot(None)).first()
    return db.session.get(ProdFile, sheet.file_id) if sheet else None


def _export(only_sheet_ids=None, kind='export', note=None):
    src = _current_source()
    if not src or not os.path.exists(os.path.join(_folder(), src.stored_name)):
        raise RuntimeError('Brak wczytanego pliku źródłowego.')
    name = os.path.splitext(src.original_name)[0] + ('_kopia' if kind == 'export' else '_backup') + '.xlsx'
    rec, dest = _save_file(kind, name, note)
    excel_io.export_workbook(os.path.join(_folder(), src.stored_name), dest,
                             ProdSheet.query.all(), only_sheet_ids)
    rec.size = os.path.getsize(dest)
    db.session.add(rec)
    db.session.commit()
    return rec, dest


@produkcja_bp.route('/import', methods=['GET', 'POST'])
@login_required
@admin_only
def import_file():
    if request.method == 'POST':
        up = request.files.get('file')
        if not up or not up.filename.lower().endswith('.xlsx'):
            flash('Wybierz plik w formacie .xlsx.', 'error')
            return redirect(url_for('produkcja.import_file'))
        rec, path = _save_file('import', os.path.basename(up.filename))
        up.save(path)
        try:
            sheets = excel_io.read_workbook(path)
        except Exception as exc:
            os.remove(path)
            current_app.logger.error('Produkcja import: %s', exc)
            flash('Nie udało się odczytać pliku. Sprawdź, czy to poprawny plik .xlsx.', 'error')
            return redirect(url_for('produkcja.import_file'))
        if not sheets:
            os.remove(path)
            flash('Plik nie zawiera danych.', 'error')
            return redirect(url_for('produkcja.import_file'))

        # backup poprzedniego stanu (z wprowadzonymi zmianami) przed zastąpieniem
        access_by_name, links, stage_cfg = {}, {}, {}
        if ProdSheet.query.first():
            try:
                _export(kind='backup', note='Automatycznie przed ponownym wczytaniem')
            except Exception as exc:
                os.remove(path)
                current_app.logger.error('Produkcja backup: %s', exc)
                flash('Nie udało się utworzyć kopii zapasowej, wczytanie przerwane.', 'error')
                return redirect(url_for('produkcja.import_file'))
            for acc in ProdSheetAccess.query.all():
                access_by_name.setdefault(db.session.get(ProdSheet, acc.sheet_id).name, []).append(acc.user_id)
            for r in ProdRow.query.filter(ProdRow.order_id.isnot(None), ProdRow.zo.isnot(None)).all():
                links[(r.sheet.name, r.zo, r.search)] = r.order_id
            for st in ProdStageColumn.query.all():
                sh_old = db.session.get(ProdSheet, st.sheet_id)
                cols = sh_old.columns
                if st.col_index < len(cols):
                    orig = sheetcfg.orig_name(sheetcfg.load(sh_old.name), cols, st.col_index)
                    stage_cfg[(sh_old.name, orig)] = (st.label, st.department_id, st.is_active)
            ProdEntry.query.update({'row_id': None})
            ProdStageColumn.query.delete()
            ProdSheetAccess.query.delete()
            ProdRow.query.delete()
            ProdSheet.query.delete()
            db.session.flush()

        db.session.add(rec)
        db.session.flush()
        total = 0
        for sd in sheets:
            names, mapping = sheetcfg.apply_to_import(sd['name'], sd['columns'], sd['mapping'])
            sd['mapping'] = mapping
            sh = ProdSheet(name=sd['name'], position=sd['position'], hidden=sd['hidden'],
                           header_row=sd['header_row'],
                           columns_json=json.dumps(names, ensure_ascii=False),
                           mapping_json=json.dumps(mapping),
                           formula_cols_json=json.dumps(sd['formula_cols']),
                           file_id=rec.id)
            db.session.add(sh)
            db.session.flush()
            batch = []
            for r in sd['rows']:
                fields = excel_io.row_fields(r['data'], sd['mapping'])
                zo = fields.get('zo')
                zo = str(zo).strip()[:64] if zo not in (None, '') else None
                search = excel_io.build_search(r['data'])
                batch.append({
                    'sheet_id': sh.id, 'position': r['src_row'], 'src_row': r['src_row'],
                    'data': json.dumps(r['data'], ensure_ascii=False),
                    'formulas': json.dumps(r['formulas']) if r['formulas'] else None,
                    'zo': zo, 'search': search,
                    'order_id': links.get((sd['name'], zo, search)) if zo else None,
                    'updated_at': datetime.now(UTC),
                })
            db.session.bulk_insert_mappings(ProdRow, batch)
            total += len(batch)
            if any(k[0] == sd['name'] for k in stage_cfg):   # ponowny import: etapy skonfigurowane wcześniej (po nazwie kolumny)
                idxs = [i for i, c in enumerate(sd['columns']) if (sd['name'], c) in stage_cfg]
            else:
                idxs = excel_io.detect_stage_columns(sd['columns'], sd['header_row'] is None)
            for i in idxs:
                label, dept, active = stage_cfg.get((sd['name'], sd['columns'][i]),
                                                    (names[i], None, True))
                db.session.add(ProdStageColumn(sheet_id=sh.id, col_index=i, label=label[:64],
                                               department_id=dept, is_active=active))
            for uid in access_by_name.get(sd['name'], []):
                db.session.add(ProdSheetAccess(user_id=uid, sheet_id=sh.id))
        rec.size = os.path.getsize(path)
        rec.note = f'{len(sheets)} arkuszy, {total} wierszy'
        db.session.commit()
        _audit('prod_import', 'ProdFile', rec.id, rec.note)
        flash(f'Wczytano {len(sheets)} arkuszy ({total} wierszy).', 'success')
        return redirect(url_for('produkcja.index'))
    return render_template('produkcja/import.html', has_data=ProdSheet.query.first() is not None)


@produkcja_bp.route('/export', methods=['POST'])
@login_required
@planning_required
def export_file():
    sheet_id = request.form.get('sheet_id', type=int)
    if sheet_id:
        _sheet_or_403(sheet_id)
        only = {sheet_id}
    elif current_user.is_admin:
        only = None
    else:
        abort(403)
    try:
        rec, dest = _export(only, note='Arkusz: ' + db.session.get(ProdSheet, sheet_id).name if sheet_id else None)
    except RuntimeError as exc:
        flash(str(exc), 'error')
        return redirect(request.referrer or url_for('produkcja.index'))
    _audit('prod_export', 'ProdFile', rec.id, rec.original_name)
    return send_file(dest, as_attachment=True, download_name=rec.original_name)


@produkcja_bp.route('/files')
@login_required
@admin_only
def files():
    items = ProdFile.query.order_by(ProdFile.created_at.desc()).all()
    return render_template('produkcja/files.html', files=items)


@produkcja_bp.route('/files/<int:file_id>/download')
@login_required
@admin_only
def file_download(file_id):
    rec = get_or_404(ProdFile, file_id)
    path = os.path.join(_folder(), rec.stored_name)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=rec.original_name)


# ── Dostęp do arkuszy ─────────────────────────────────────────────────────────

@produkcja_bp.route('/access', methods=['GET', 'POST'])
@login_required
@admin_only
def access():
    users = User.query.filter_by(role='planowanie').order_by(User.username).all()
    sheets = ProdSheet.query.order_by(ProdSheet.position).all()
    if request.method == 'POST':
        wanted = {(int(u), int(s)) for u, s in
                  (v.split(':') for v in request.form.getlist('access') if re.match(r'^\d+:\d+$', v))}
        valid_users = {u.id for u in users}
        valid_sheets = {s.id for s in sheets}
        ProdSheetAccess.query.filter(ProdSheetAccess.user_id.in_(valid_users or [0])).delete(
            synchronize_session=False)
        for uid, sid in wanted:
            if uid in valid_users and sid in valid_sheets:
                db.session.add(ProdSheetAccess(user_id=uid, sheet_id=sid))
        db.session.commit()
        _audit('prod_access_update', 'ProdSheetAccess', None, f'assignments={len(wanted)}')
        flash('Zapisano dostęp do arkuszy.', 'success')
        return redirect(url_for('produkcja.access'))
    granted = {(a.user_id, a.sheet_id) for a in ProdSheetAccess.query.all()}
    return render_template('produkcja/access.html', users=users, sheets=sheets, granted=granted)
