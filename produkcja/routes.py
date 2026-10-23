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

from models import (db, get_or_404, AuditLog, User, Report,
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
    """Powiązane listy kontrolne (Kontrola Końcowa) dla wierszy strony: po kluczu QR
    (produkt|klient|ZO), który mają listy utworzone ze skanu albo z tej listy produkcyjnej."""
    qr_key = helper('qr_key')
    keys = {}
    for r in rows:
        sheet = r.sheet
        f = excel_io.row_fields(json.loads(r.data or '[]'), sheet.mapping)
        k = qr_key(f.get('product') or '', f.get('client') or '', f.get('zo') or '')
        if k:
            keys[r.id] = k
    by_key = {}
    if keys:
        for rep in (Report.query.filter(Report.report_type == 'kontroler',
                                        Report.qr_key.in_(set(keys.values()))).all()):
            st = rep.stats
            b = by_key.setdefault(rep.qr_key, {'total': 0, 'done': 0, 'ng': 0, 'ids': []})
            b['total'] += 1
            b['done'] += 1 if rep.status == 'completed' else 0
            b['ng'] += 1 if st['ng'] else 0
            b['ids'].append(rep.id)
    result = {}
    for r in rows:
        info = by_key.get(keys[r.id]) if keys.get(r.id) else None
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
                  .filter(ProdRow.checklists_at.isnot(None)).group_by(ProdRow.sheet_id).all())
    has_data = ProdSheet.query.first() is not None
    has_xlsx = _current_source() is not None
    return render_template('produkcja/index.html', sheets=sheets, counts=counts,
                           linked=linked, has_data=has_data, has_xlsx=has_xlsx)


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
        query = query.filter(ProdRow.checklists_at.isnot(None))
    elif state == 'unlinked':
        query = query.filter(ProdRow.checklists_at.is_(None))
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
    """Tworzy listy kontrolne (Kontrola Końcowa) z wiersza listy produkcyjnej.
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
    if existing:
        return False, f'{product}: listy kontrolne już istnieją ({len(existing)}).'

    tpl = helper('find_matching_template')(product, 'kontroler')
    if not tpl:
        return False, f'{product}: brak pasującego szablonu QA.'

    created = helper('create_checklist_series')(product, client, zo, qty, tpl,
                                                current_user.id, due=due)
    row.checklists_at = datetime.now(UTC)
    return True, f'{product}: utworzono {created} list kontrolnych (szablon: {tpl.name}).'


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

def _save_file(kind, original_name, note=None, ext='xlsx'):
    stored = f'{kind}_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}.{ext}'
    rec = ProdFile(kind=kind, original_name=original_name, stored_name=stored, note=note,
                   created_by_id=current_user.id)
    return rec, os.path.join(_folder(), stored)


def _current_source():
    """Wczytany plik .xlsx, na którym opiera się eksport (arkusze z CSV go nie używają)."""
    return (ProdFile.query.join(ProdSheet, ProdSheet.file_id == ProdFile.id)
            .filter(ProdFile.stored_name.like('%.xlsx')).first())


def _export(only_sheet_ids=None, kind='export', note=None):
    src = _current_source()
    if not src or not os.path.exists(os.path.join(_folder(), src.stored_name)):
        raise RuntimeError('Brak wczytanego pliku .xlsx. Dane z CSV pobierzesz jako CSV z widoku arkusza.')
    sheets = [s for s in ProdSheet.query.all() if s.file_id == src.id]
    if only_sheet_ids is not None and not any(s.id in only_sheet_ids for s in sheets):
        raise RuntimeError('Ten arkusz pochodzi z pliku CSV. Użyj przycisku „Pobierz CSV”.')
    name = os.path.splitext(src.original_name)[0] + ('_kopia' if kind == 'export' else '_backup') + '.xlsx'
    rec, dest = _save_file(kind, name, note)
    excel_io.export_workbook(os.path.join(_folder(), src.stored_name), dest, sheets, only_sheet_ids)
    rec.size = os.path.getsize(dest)
    db.session.add(rec)
    db.session.commit()
    return rec, dest


def _csv_bytes(sheet):
    rows = ProdRow.query.filter_by(sheet_id=sheet.id).order_by(ProdRow.position).all()
    return excel_io.sheet_to_csv(sheet, rows)


def _backup_sheets(old_sheets, note):
    """Kopia zapasowa arkuszy, które zaraz zostaną zastąpione: .xlsx dla arkuszy z pliku
    Excel i osobne CSV dla arkuszy wczytanych z CSV."""
    src = _current_source()
    if src and any(s.file_id == src.id for s in old_sheets):
        _export(kind='backup', note=note)
    for sh in old_sheets:
        if src is None or sh.file_id != src.id:
            rec, dest = _save_file('backup', f'{sh.name}_backup.csv', note, ext='csv')
            with open(dest, 'wb') as fh:
                fh.write(_csv_bytes(sh))
            rec.size = os.path.getsize(dest)
            db.session.add(rec)
    db.session.commit()


def _install_sheets(sheets, rec, replace_all):
    """Zapisuje wczytane arkusze. `replace_all` (plik .xlsx) zastępuje wszystkie arkusze,
    inaczej (CSV) tylko te o tych samych nazwach. Zachowuje dostęp do arkuszy, powiązania
    z listami kontrolnymi oraz ustawienia kolumn i etapów. Zwraca liczbę wczytanych wierszy."""
    names = {sd['name'] for sd in sheets}
    old_sheets = (ProdSheet.query.all() if replace_all
                  else ProdSheet.query.filter(ProdSheet.name.in_(names)).all())
    old_ids = [s.id for s in old_sheets]
    old_by_name = {s.name: s for s in old_sheets}
    name_by_id = {s.id: s.name for s in old_sheets}
    access_by_name, links, stage_cfg = {}, {}, {}
    if old_ids:
        for acc in ProdSheetAccess.query.filter(ProdSheetAccess.sheet_id.in_(old_ids)).all():
            access_by_name.setdefault(name_by_id[acc.sheet_id], []).append(acc.user_id)
        for r in ProdRow.query.filter(ProdRow.sheet_id.in_(old_ids), ProdRow.checklists_at.isnot(None),
                                      ProdRow.zo.isnot(None)).all():
            links[(r.sheet.name, r.zo, r.search)] = r.checklists_at
        for st in ProdStageColumn.query.filter(ProdStageColumn.sheet_id.in_(old_ids)).all():
            sh_old = db.session.get(ProdSheet, st.sheet_id)
            cols = sh_old.columns
            if st.col_index < len(cols):
                orig = sheetcfg.orig_name(sheetcfg.load(sh_old.name), cols, st.col_index)
                stage_cfg[(sh_old.name, orig)] = (st.label, st.department_id, st.is_active)
        row_ids = db.session.query(ProdRow.id).filter(ProdRow.sheet_id.in_(old_ids))
        ProdEntry.query.filter(ProdEntry.row_id.in_(row_ids)).update({'row_id': None},
                                                                     synchronize_session=False)
        ProdStageColumn.query.filter(ProdStageColumn.sheet_id.in_(old_ids)).delete()
        ProdSheetAccess.query.filter(ProdSheetAccess.sheet_id.in_(old_ids)).delete()
        ProdRow.query.filter(ProdRow.sheet_id.in_(old_ids)).delete()
        ProdSheet.query.filter(ProdSheet.id.in_(old_ids)).delete()
        db.session.flush()
    next_pos = (db.session.query(func.max(ProdSheet.position)).scalar() or 0) + 1

    total = 0
    for sd in sheets:
        names_cols, mapping = sheetcfg.apply_to_import(sd['name'], sd['columns'], sd['mapping'])
        sd['mapping'] = mapping
        old = old_by_name.get(sd['name'])
        if old is not None:
            position, hidden = old.position, old.hidden
        else:
            position, hidden = next_pos, sd['hidden']
            next_pos += 1
        sh = ProdSheet(name=sd['name'], position=position, hidden=hidden,
                       header_row=sd['header_row'],
                       columns_json=json.dumps(names_cols, ensure_ascii=False),
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
                'checklists_at': links.get((sd['name'], zo, search)) if zo else None,
                'updated_at': datetime.now(UTC),
            })
        db.session.bulk_insert_mappings(ProdRow, batch)
        total += len(batch)
        if any(k[0] == sd['name'] for k in stage_cfg):   # ponowny import: etapy skonfigurowane wcześniej
            idxs = [i for i, c in enumerate(sd['columns']) if (sd['name'], c) in stage_cfg]
        else:
            idxs = excel_io.detect_stage_columns(sd['columns'], sd['header_row'] is None)
        for i in idxs:
            label, dept, active = stage_cfg.get((sd['name'], sd['columns'][i]),
                                                (names_cols[i], None, True))
            db.session.add(ProdStageColumn(sheet_id=sh.id, col_index=i, label=label[:64],
                                           department_id=dept, is_active=active))
        for uid in access_by_name.get(sd['name'], []):
            db.session.add(ProdSheetAccess(user_id=uid, sheet_id=sh.id))
    return total


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

        old = ProdSheet.query.all()
        if old:
            try:
                _backup_sheets(old, 'Automatycznie przed ponownym wczytaniem')
            except Exception as exc:
                os.remove(path)
                current_app.logger.error('Produkcja backup: %s', exc)
                flash('Nie udało się utworzyć kopii zapasowej, wczytanie przerwane.', 'error')
                return redirect(url_for('produkcja.import_file'))
        db.session.add(rec)
        db.session.flush()
        total = _install_sheets(sheets, rec, replace_all=True)
        rec.size = os.path.getsize(path)
        rec.note = f'{len(sheets)} arkuszy, {total} wierszy'
        db.session.commit()
        _audit('prod_import', 'ProdFile', rec.id, rec.note)
        flash(f'Wczytano {len(sheets)} arkuszy ({total} wierszy).', 'success')
        return redirect(url_for('produkcja.index'))
    return render_template('produkcja/import.html', has_data=ProdSheet.query.first() is not None,
                           sheet_names=[s.name for s in ProdSheet.query.order_by(ProdSheet.position)])


@produkcja_bp.route('/import-csv', methods=['POST'])
@login_required
@admin_only
def import_csv():
    """Jeden plik CSV = jeden arkusz. Zastępuje tylko arkusze o podanych nazwach."""
    files = [f for f in request.files.getlist('csv') if f and f.filename]
    names = [n.strip()[:64] for n in request.form.getlist('sheet_name')]
    if not files or len(names) != len(files) or not all(names):
        flash('Wybierz pliki CSV i podaj nazwę arkusza dla każdego z nich.', 'error')
        return redirect(url_for('produkcja.import_file'))
    if len({n.lower() for n in names}) != len(names):
        flash('Każdy plik CSV musi trafić do innego arkusza.', 'error')
        return redirect(url_for('produkcja.import_file'))
    parsed = []
    for up, name in zip(files, names):
        try:
            sd = excel_io.read_csv(up.read(), name)
        except Exception as exc:
            flash(f'{up.filename}: nie udało się odczytać pliku CSV ({exc}).', 'error')
            return redirect(url_for('produkcja.import_file'))
        existing = ProdSheet.query.filter(func.lower(ProdSheet.name) == name.lower()).first()
        if existing:
            sd['name'] = existing.name   # nazwy arkuszy bez rozróżniania wielkości liter
        parsed.append((up, sd))
        up.seek(0)

    old = ProdSheet.query.filter(ProdSheet.name.in_([sd['name'] for _up, sd in parsed])).all()
    if old:
        try:
            _backup_sheets(old, 'Automatycznie przed wczytaniem CSV')
        except Exception as exc:
            current_app.logger.error('Produkcja backup CSV: %s', exc)
            flash('Nie udało się utworzyć kopii zapasowej, wczytanie przerwane.', 'error')
            return redirect(url_for('produkcja.import_file'))
    summary = []
    for up, sd in parsed:
        rec, path = _save_file('import', os.path.basename(up.filename), ext='csv')
        up.save(path)
        db.session.add(rec)
        db.session.flush()
        total = _install_sheets([sd], rec, replace_all=False)
        rec.size = os.path.getsize(path)
        rec.note = f'CSV: arkusz {sd["name"]}, {total} wierszy'
        db.session.commit()
        _audit('prod_import_csv', 'ProdFile', rec.id, rec.note)
        summary.append(f'{sd["name"]} ({total})')
    flash('Wczytano z CSV: ' + ', '.join(summary) + '.', 'success')
    return redirect(url_for('produkcja.index'))


@produkcja_bp.route('/sheet/<int:sheet_id>/csv')
@login_required
@planning_required
def sheet_csv(sheet_id):
    sheet = _sheet_or_403(sheet_id)
    _audit('prod_export_csv', 'ProdSheet', sheet.id, sheet.name)
    from io import BytesIO
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', sheet.name)
    return send_file(BytesIO(_csv_bytes(sheet)), mimetype='text/csv; charset=utf-8',
                     as_attachment=True, download_name=f'{safe}.csv')


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
    from models import roles_matching
    users = (User.query.filter(User.role.in_(roles_matching('technolog')))
             .order_by(User.username).all())
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
