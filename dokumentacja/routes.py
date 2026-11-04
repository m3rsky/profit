import os
import re
import json
import math
import uuid
import mimetypes
from datetime import datetime, timezone

from flask import (render_template, redirect, url_for, request, flash,
                   abort, current_app, send_from_directory, make_response, jsonify)
from flask_login import login_required, current_user
from sqlalchemy import func
from werkzeug.utils import secure_filename

from models import db, get_or_404, AuditLog, DocEntry, DocFile, DocNote, DocMeasurement, DocElementName
from . import dokumentacja_bp

UTC = timezone.utc

# Konwersja zdjęć z iPhone (HEIC/HEIF) — opcjonalna, moduł nie jest wymagany do działania
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_OK = True
except Exception:  # pragma: no cover - zależne od środowiska
    HEIF_OK = False

# Rozszerzenia serwowane zawsze jako załącznik (ryzyko XSS przy podglądzie inline)
_FORCE_DOWNLOAD_EXT = {'svg', 'svgz', 'html', 'htm', 'xhtml', 'xml', 'js', 'mhtml'}

_IMG_MAX_SIDE = 2560
_THUMB_MAX_SIDE = 500


# ── Pomocnicze ────────────────────────────────────────────────────────────────

def _audit(action, target_id=None, detail=None):
    try:
        db.session.add(AuditLog(
            user_id=current_user.id, action=action, target_type='doc_entry',
            target_id=target_id, detail=detail, ip=request.remote_addr,
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Dokumentacja audit error: %s', exc)


def _ext(filename):
    return filename.rsplit('.', 1)[1].lower() if filename and '.' in filename else ''


def _is_image_ext(ext):
    return ext in current_app.config['DOC_IMAGE_EXTENSIONS']


def _is_document_ext(ext):
    return ext in current_app.config['DOC_DOCUMENT_EXTENSIONS']


def _is_step_ext(ext):
    return ext in current_app.config['DOC_STEP_EXTENSIONS']


def _stream_size(file):
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    return size


def _looks_like_step(file):
    """STEP (ISO 10303-21) zaczyna się od nagłówka ISO-10303-21."""
    file.stream.seek(0)
    head = file.stream.read(1024)
    file.stream.seek(0)
    return b'ISO-10303-21' in head


def _upload_dir():
    d = current_app.config['DOKUMENTACJA_UPLOAD_FOLDER']
    os.makedirs(d, exist_ok=True)
    return d


def _can_edit_entry(entry):
    return current_user.can('dokumentacja.manage_all') or entry.user_id == current_user.id


def _can_edit_file(f):
    return (current_user.can('dokumentacja.manage_all') or f.entry.user_id == current_user.id
            or f.user_id == current_user.id)


def _can_edit_note(note):
    return current_user.can('dokumentacja.manage_all') or note.user_id == current_user.id


def _remove_disk_files(f):
    """Usuwa z dysku wszystkie pliki powiązane z rekordem DocFile."""
    d = current_app.config['DOKUMENTACJA_UPLOAD_FOLDER']
    for name in (f.filename, f.preview_filename, f.thumb_filename):
        if not name:
            continue
        path = os.path.join(d, os.path.basename(name))
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as exc:
                current_app.logger.warning('Dokumentacja: nie usunięto %s: %s', path, exc)


def _store_image(file, ext):
    """Zapisuje zdjęcie: normalizuje orientację EXIF, przeskalowuje, robi
    miniaturę. Zwraca dict z danymi do DocFile albo None gdy plik nie jest
    prawidłowym obrazem."""
    from PIL import Image, ImageOps

    upload_dir = _upload_dir()
    try:
        file.stream.seek(0)
        img = Image.open(file.stream)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
    except Exception:
        file.stream.seek(0)
        return None

    base = uuid.uuid4().hex
    main_name = f'{base}.jpg'
    thumb_name = f'thumb_{base}.jpg'

    work = img.copy()
    work.thumbnail((_IMG_MAX_SIDE, _IMG_MAX_SIDE))
    work.save(os.path.join(upload_dir, main_name), 'JPEG', quality=85, optimize=True)

    thumb = img.copy()
    thumb.thumbnail((_THUMB_MAX_SIDE, _THUMB_MAX_SIDE))
    thumb.save(os.path.join(upload_dir, thumb_name), 'JPEG', quality=80, optimize=True)

    size = os.path.getsize(os.path.join(upload_dir, main_name))
    return {
        'kind': 'image',
        'filename': main_name,
        'preview_filename': None,
        'thumb_filename': thumb_name,
        'mime_type': 'image/jpeg',
        'size_bytes': size,
    }


def _store_raw(file, ext):
    """Zapisuje plik bez przetwarzania (dokument lub obraz, którego nie da się
    otworzyć w Pillow)."""
    upload_dir = _upload_dir()
    unique = f'{uuid.uuid4().hex}.{ext}' if ext else uuid.uuid4().hex
    path = os.path.join(upload_dir, unique)
    file.stream.seek(0)
    file.save(path)
    mime = mimetypes.guess_type(file.filename or unique)[0]
    return {
        'filename': unique,
        'preview_filename': None,
        'thumb_filename': None,
        'mime_type': mime,
        'size_bytes': os.path.getsize(path),
    }


def _add_file(entry, file, force_kind=None):
    """Przetwarza jeden przesłany plik i dopina go do teczki.
    Zwraca (ok: bool, komunikat_błędu: str|None)."""
    if not file or not file.filename:
        return False, None
    ext = _ext(file.filename)
    if not ext or (not _is_image_ext(ext) and not _is_document_ext(ext)):
        return False, f'Pominięto „{file.filename}” — niedozwolony typ pliku.'

    is_step = _is_step_ext(ext)
    size = _stream_size(file)
    max_bytes = (current_app.config['DOC_STEP_MAX_BYTES'] if is_step
                 else current_app.config['DOC_MAX_FILE_BYTES'])
    if size > max_bytes:
        return False, (f'Pominięto „{file.filename}”, plik ma {size / 1048576:.0f} MB, '
                       f'limit to {max_bytes // 1048576} MB.')
    if is_step and not _looks_like_step(file):
        return False, f'Pominięto „{file.filename}”, to nie jest prawidłowy plik STEP.'

    treat_as_image = _is_image_ext(ext) if force_kind is None else (force_kind == 'image')

    if treat_as_image:
        if ext in ('heic', 'heif') and not HEIF_OK:
            data = _store_raw(file, ext)
            data['kind'] = 'image'
        else:
            data = _store_image(file, ext)
            if data is None:
                return False, f'Pominięto „{file.filename}” — plik nie jest prawidłowym obrazem.'
    else:
        data = _store_raw(file, ext)
        data['kind'] = 'document'

    db.session.add(DocFile(
        entry_id=entry.id,
        original_name=secure_filename(file.filename) or file.filename,
        user_id=current_user.id,
        **data,
    ))
    return True, None


# ── Listy: Projekty i Dokumenty ─────────────────────────────────────────────

PER_PAGE_CHOICES = (10, 20, 25, 50, 100)

_PROJECT_SORTS = {
    'zo': DocEntry.zo_number, 'rys': DocEntry.drawing_number, 'klient': DocEntry.client,
    'produkt': DocEntry.product, 'data': DocEntry.created_at,
}
_DOCUMENT_SORTS = {
    'tytul': DocEntry.title, 'kategoria': DocEntry.category, 'data': DocEntry.created_at,
}


def _clip(value, n):
    """Pole formularza do bazy: przycięte, puste jako NULL (wszystkie pola projektu są opcjonalne)."""
    return (value or '').strip()[:n] or None


def _like(q):
    # znaki specjalne LIKE wpisane przez użytkownika traktujemy dosłownie
    esc = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{esc}%'


def _page_args(default_sort):
    try:
        per_page = int(request.args.get('per_page', 25))
    except ValueError:
        per_page = 25
    if per_page not in PER_PAGE_CHOICES:
        per_page = 25
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    sort = request.args.get('sort') or default_sort
    direction = 'asc' if request.args.get('dir') == 'asc' else 'desc'
    return page, per_page, sort, direction


def _entry_counts(ids):
    """Liczby plików (dokumenty, zdjęcia), notatek i powiązanych dokumentów dla wpisów na stronie."""
    out = {i: {'files': 0, 'images': 0, 'notes': 0, 'linked': 0} for i in ids}
    if not ids:
        return out
    for entry_id, kind, n in (db.session.query(DocFile.entry_id, DocFile.kind, func.count(DocFile.id))
                              .filter(DocFile.entry_id.in_(ids)).group_by(DocFile.entry_id, DocFile.kind)):
        out[entry_id]['images' if kind == 'image' else 'files'] = n
    for entry_id, n in (db.session.query(DocNote.entry_id, func.count(DocNote.id))
                        .filter(DocNote.entry_id.in_(ids)).group_by(DocNote.entry_id)):
        out[entry_id]['notes'] = n
    for project_id, n in (db.session.query(DocEntry.project_id, func.count(DocEntry.id))
                          .filter(DocEntry.project_id.in_(ids)).group_by(DocEntry.project_id)):
        out[project_id]['linked'] = n
    return out


def _tab_totals():
    return {
        'projects': DocEntry.query.filter_by(kind=DocEntry.KIND_PROJECT).count(),
        'documents': DocEntry.query.filter_by(kind=DocEntry.KIND_DOCUMENT).count(),
    }


@dokumentacja_bp.route('/')
@login_required
def list_entries():
    """Zakładka Projekty: lista po Nr ZO, Nr rysunku, Kliencie i Produkcie."""
    q = request.args.get('q', '').strip()
    klient = request.args.get('klient', '').strip()
    page, per_page, sort, direction = _page_args('data')
    if sort not in _PROJECT_SORTS:
        sort = 'data'
    query = DocEntry.query.filter_by(kind=DocEntry.KIND_PROJECT)
    if q:
        like = _like(q)
        query = query.filter(
            DocEntry.zo_number.ilike(like, escape='\\') | DocEntry.drawing_number.ilike(like, escape='\\')
            | DocEntry.client.ilike(like, escape='\\') | DocEntry.product.ilike(like, escape='\\')
            | DocEntry.title.ilike(like, escape='\\') | DocEntry.description.ilike(like, escape='\\'))
    if klient:
        query = query.filter(DocEntry.client == klient)
    col = _PROJECT_SORTS[sort]
    order = col.asc() if direction == 'asc' else col.desc()
    pager = query.order_by(order, DocEntry.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    clients = [c for (c,) in (db.session.query(DocEntry.client)
                              .filter(DocEntry.kind == DocEntry.KIND_PROJECT, DocEntry.client.isnot(None),
                                      DocEntry.client != '').distinct().order_by(DocEntry.client))]
    return render_template(
        'dokumentacja/list.html', pager=pager, q=q, klient=klient, clients=clients, per_page=per_page,
        per_page_choices=PER_PAGE_CHOICES, sort=sort, direction=direction,
        counts=_entry_counts([e.id for e in pager.items]),
        editable={e.id for e in pager.items if _can_edit_entry(e)}, totals=_tab_totals(), tab='projects')


@dokumentacja_bp.route('/dokumenty')
@login_required
def list_documents():
    """Zakładka Dokumenty: osobna dokumentacja (instrukcje, procedury, normy), opcjonalnie z projektem."""
    q = request.args.get('q', '').strip()
    kategoria = request.args.get('kategoria', '').strip()
    powiazanie = request.args.get('powiazanie', '')
    projekt = request.args.get('projekt', type=int)
    page, per_page, sort, direction = _page_args('data')
    if sort not in _DOCUMENT_SORTS:
        sort = 'data'
    query = DocEntry.query.filter_by(kind=DocEntry.KIND_DOCUMENT)
    if q:
        like = _like(q)
        query = query.filter(
            DocEntry.title.ilike(like, escape='\\') | DocEntry.category.ilike(like, escape='\\')
            | DocEntry.description.ilike(like, escape='\\'))
    if kategoria:
        query = query.filter(DocEntry.category == kategoria)
    if projekt:
        query = query.filter(DocEntry.project_id == projekt)
    elif powiazanie == 'tak':
        query = query.filter(DocEntry.project_id.isnot(None))
    elif powiazanie == 'nie':
        query = query.filter(DocEntry.project_id.is_(None))
    col = _DOCUMENT_SORTS[sort]
    order = col.asc() if direction == 'asc' else col.desc()
    pager = query.order_by(order, DocEntry.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    categories = [c for (c,) in (db.session.query(DocEntry.category)
                                 .filter(DocEntry.kind == DocEntry.KIND_DOCUMENT, DocEntry.category.isnot(None),
                                         DocEntry.category != '').distinct().order_by(DocEntry.category))]
    project_filter = db.session.get(DocEntry, projekt) if projekt else None
    return render_template(
        'dokumentacja/documents.html', pager=pager, q=q, kategoria=kategoria, powiazanie=powiazanie,
        projekt=projekt, project_filter=project_filter, categories=categories, per_page=per_page,
        per_page_choices=PER_PAGE_CHOICES, sort=sort, direction=direction,
        counts=_entry_counts([e.id for e in pager.items]),
        editable={e.id for e in pager.items if _can_edit_entry(e)}, totals=_tab_totals(), tab='documents')


@dokumentacja_bp.route('/usun-zaznaczone', methods=['POST'])
@login_required
def bulk_delete():
    ids = [int(i) for i in request.form.getlist('entry_ids') if i.isdigit()]
    back = 'dokumentacja.list_documents' if request.form.get('back') == 'documents' else 'dokumentacja.list_entries'
    if not ids:
        flash('Nie zaznaczono żadnej pozycji.', 'warning')
        return redirect(url_for(back))
    removed = skipped = 0
    for entry in DocEntry.query.filter(DocEntry.id.in_(ids)).all():
        if not _can_edit_entry(entry):
            skipped += 1
            continue
        _delete_entry_objects(entry)
        removed += 1
    db.session.commit()
    if removed:
        _audit('doc_bulk_delete', None, f'removed={removed}')
        flash(f'Usunięto pozycji: {removed}.', 'success')
    if skipped:
        flash(f'Pominięto pozycji, których nie możesz usunąć: {skipped}.', 'warning')
    return redirect(url_for(back))


def _delete_entry_objects(entry):
    """Kasuje wpis razem z plikami na dysku; dokumenty powiązane z kasowanym projektem zostają (bez projektu)."""
    for f in entry.files:
        _remove_disk_files(f)
    DocEntry.query.filter_by(project_id=entry.id).update({'project_id': None})
    db.session.delete(entry)


# ── Nowy wpis ───────────────────────────────────────────────────────────────

def _category_list():
    return [c for (c,) in (db.session.query(DocEntry.category)
                           .filter(DocEntry.kind == DocEntry.KIND_DOCUMENT, DocEntry.category.isnot(None),
                                   DocEntry.category != '').distinct().order_by(DocEntry.category))]


def _project_choices():
    return (DocEntry.query.filter_by(kind=DocEntry.KIND_PROJECT)
            .order_by(DocEntry.created_at.desc()).limit(500).all())


def _duplicate_project(zo, product, exclude_id=None):
    """Inny projekt z tym samym ZO i produktem (to tylko ostrzeżenie, nie blokada)."""
    if not zo or not product:
        return None
    q = DocEntry.query.filter(DocEntry.kind == DocEntry.KIND_PROJECT,
                              func.lower(DocEntry.zo_number) == zo.lower(),
                              func.lower(DocEntry.product) == product.lower())
    if exclude_id:
        q = q.filter(DocEntry.id != exclude_id)
    return q.first()


def _valid_project_id(raw):
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return None
    p = db.session.get(DocEntry, pid)
    return p.id if p and p.is_project else None


@dokumentacja_bp.route('/nowa', methods=['GET', 'POST'])
@login_required
def new_entry():
    src = request.form if request.method == 'POST' else request.args
    kind = DocEntry.KIND_DOCUMENT if src.get('rodzaj') == 'dokument' else DocEntry.KIND_PROJECT
    if request.method == 'POST':
        description = request.form.get('description', '').strip()
        if kind == DocEntry.KIND_DOCUMENT:
            title = request.form.get('title', '').strip()[:256]
            if not title:
                flash('Tytuł dokumentu jest wymagany.', 'error')
                return render_template('dokumentacja/new.html', form=request.form, kind=kind,
                                       projects=_project_choices(), categories=_category_list(),
                                       totals=_tab_totals(), tab='documents')
            entry = DocEntry(kind=kind, title=title, description=description or None,
                             category=_clip(request.form.get('category'), 128),
                             project_id=_valid_project_id(request.form.get('project_id')),
                             user_id=current_user.id)
        else:
            entry = DocEntry(kind=kind, title=(request.form.get('title', '').strip()[:256]),
                             description=description or None,
                             zo_number=_clip(request.form.get('zo_number'), 64),
                             drawing_number=_clip(request.form.get('drawing_number'), 128),
                             client=_clip(request.form.get('client'), 256),
                             product=_clip(request.form.get('product'), 256),
                             user_id=current_user.id)
        db.session.add(entry)
        db.session.commit()
        _audit('doc_create', entry.id, f'kind={kind} title={entry.display_title}')
        dup = _duplicate_project(entry.zo_number, entry.product, entry.id) if entry.is_project else None
        if dup:
            flash(f'Uwaga: istnieje już projekt z tym samym ZO i produktem (nr {dup.id}).', 'warning')
        flash('Projekt utworzony. Dodaj pliki, zdjęcia i notatki.' if entry.is_project
              else 'Dokument utworzony. Dodaj pliki i notatki.', 'success')
        return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id))
    form = {}
    pid = request.args.get('projekt', type=int)
    if kind == DocEntry.KIND_DOCUMENT and pid and _valid_project_id(pid):
        form = {'project_id': str(pid)}
    return render_template('dokumentacja/new.html', form=form, kind=kind, projects=_project_choices(),
                           categories=_category_list(), totals=_tab_totals(),
                           tab='documents' if kind == DocEntry.KIND_DOCUMENT else 'projects')


# ── Szczegóły wpisu ─────────────────────────────────────────────────────────

@dokumentacja_bp.route('/<int:entry_id>')
@login_required
def entry_detail(entry_id):
    entry = get_or_404(DocEntry, entry_id)
    linked = []
    if entry.is_project:
        linked = (entry.linked_documents.filter_by(kind=DocEntry.KIND_DOCUMENT)
                  .order_by(DocEntry.created_at.desc()).all())
    return render_template('dokumentacja/detail.html', entry=entry,
                           can_edit=_can_edit_entry(entry), linked=linked,
                           projects=_project_choices() if not entry.is_project else [],
                           categories=_category_list(),
                           tab='projects' if entry.is_project else 'documents', totals=_tab_totals(),
                           confirm_mb=current_app.config['DOC_CONFIRM_MB'],
                           step_max_mb=current_app.config['DOC_STEP_MAX_BYTES'] // 1048576)


@dokumentacja_bp.route('/<int:entry_id>/edytuj', methods=['POST'])
@login_required
def edit_entry(entry_id):
    entry = get_or_404(DocEntry, entry_id)
    if not _can_edit_entry(entry):
        abort(403)
    description = request.form.get('description', '').strip()
    if entry.is_project:
        entry.title = request.form.get('title', '').strip()[:256]
        entry.zo_number = _clip(request.form.get('zo_number'), 64)
        entry.drawing_number = _clip(request.form.get('drawing_number'), 128)
        entry.client = _clip(request.form.get('client'), 256)
        entry.product = _clip(request.form.get('product'), 256)
    else:
        title = request.form.get('title', '').strip()[:256]
        if not title:
            flash('Tytuł dokumentu jest wymagany.', 'error')
            return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id))
        entry.title = title
        entry.category = _clip(request.form.get('category'), 128)
        entry.project_id = _valid_project_id(request.form.get('project_id'))
    entry.description = description or None
    entry.updated_at = datetime.now(UTC)
    db.session.commit()
    _audit('doc_edit', entry.id, f'kind={entry.kind} title={entry.display_title}')
    flash('Zapisano zmiany.', 'success')
    return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id))


@dokumentacja_bp.route('/<int:entry_id>/rodzaj', methods=['POST'])
@login_required
def change_kind(entry_id):
    """Przeniesienie wpisu między Projektami a Dokumentami (np. po automatycznej klasyfikacji starych teczek)."""
    entry = get_or_404(DocEntry, entry_id)
    if not _can_edit_entry(entry):
        abort(403)
    target = DocEntry.KIND_DOCUMENT if request.form.get('rodzaj') == 'dokument' else DocEntry.KIND_PROJECT
    if target == entry.kind:
        return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id))
    if target == DocEntry.KIND_DOCUMENT:
        entry.title = entry.display_title[:256]                      # dokument musi mieć tytuł
        DocEntry.query.filter_by(project_id=entry.id).update({'project_id': None})
    else:
        entry.project_id = None
        entry.category = None
    entry.kind = target
    entry.updated_at = datetime.now(UTC)
    db.session.commit()
    _audit('doc_kind', entry.id, f'kind={target}')
    flash('Przeniesiono do Dokumentów.' if target == DocEntry.KIND_DOCUMENT else 'Przeniesiono do Projektów.', 'success')
    return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id))


@dokumentacja_bp.route('/<int:entry_id>/usun', methods=['POST'])
@login_required
def delete_entry(entry_id):
    entry = get_or_404(DocEntry, entry_id)
    if not _can_edit_entry(entry):
        abort(403)
    name, back = entry.display_title, ('dokumentacja.list_entries' if entry.is_project else 'dokumentacja.list_documents')
    _delete_entry_objects(entry)
    db.session.commit()
    _audit('doc_delete', entry_id, f'title={name}')
    flash(f'„{name}” usunięte.', 'success')
    return redirect(url_for(back))


# ── Podpowiedzi klienta i produktu po numerze ZO ────────────────────────────

@dokumentacja_bp.route('/zo-info')
@login_required
def zo_info():
    """Klient i produkt znane dla danego ZO (Kontrola etapowa, Lista produkcyjna, wcześniejsze projekty).
    Zwraca tylko dopasowania dokładnie do wpisanego numeru ZO, bez przeglądania list."""
    from models import ControlOrder, ProdRow, ProdSheet
    from produkcja import excel_io
    zo = request.args.get('zo', '').strip()
    if len(zo) < 2:
        return jsonify(items=[])
    key = zo.lower()
    found, seen = [], set()

    def add(client, product, source):
        client = (str(client).strip() if client not in (None, '') else '')[:256]
        product = (str(product).strip() if product not in (None, '') else '')[:256]
        if not (client or product):
            return
        sig = (client.lower(), product.lower())
        if sig in seen:
            return
        seen.add(sig)
        found.append({'client': client, 'product': product, 'source': source})

    for o in (ControlOrder.query.filter(func.lower(ControlOrder.zo_number) == key)
              .order_by(ControlOrder.id.desc()).limit(20)):
        add(o.client, o.product_name, 'Kontrola etapowa')
    mappings = {}
    for row in (ProdRow.query.filter(func.lower(ProdRow.zo) == key).order_by(ProdRow.id.desc()).limit(40)):
        if row.sheet_id not in mappings:
            sheet = db.session.get(ProdSheet, row.sheet_id)
            mappings[row.sheet_id] = sheet.mapping if sheet else {}
        try:
            fields = excel_io.row_fields(json.loads(row.data or '[]'), mappings[row.sheet_id])
        except (ValueError, TypeError):
            continue
        add(fields.get('client'), fields.get('product'), 'Lista produkcyjna')
    for p in (DocEntry.query.filter(DocEntry.kind == DocEntry.KIND_PROJECT, func.lower(DocEntry.zo_number) == key)
              .order_by(DocEntry.id.desc()).limit(20)):
        add(p.client, p.product, 'Dokumentacja')
    return jsonify(items=found[:12])


# ── Dokumenty i zdjęcia ─────────────────────────────────────────────────────

@dokumentacja_bp.route('/<int:entry_id>/dokumenty', methods=['POST'])
@login_required
def add_documents(entry_id):
    entry = get_or_404(DocEntry, entry_id)
    files = request.files.getlist('files')
    added = 0
    for file in files:
        ok, err = _add_file(entry, file)  # obraz wśród dokumentów trafi do sekcji Zdjęcia
        if ok:
            added += 1
        elif err:
            flash(err, 'warning')
    if added:
        db.session.commit()
        _audit('doc_files_add', entry.id, f'documents +{added}')
        flash(f'Dodano plików: {added}.', 'success')
    elif not any(f and f.filename for f in files):
        flash('Nie wybrano żadnego pliku.', 'warning')
    return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id) + '#dokumenty')


@dokumentacja_bp.route('/<int:entry_id>/zdjecia', methods=['POST'])
@login_required
def add_images(entry_id):
    entry = get_or_404(DocEntry, entry_id)
    files = request.files.getlist('images')
    added = 0
    for file in files:
        if not file or not file.filename:
            continue
        if not _is_image_ext(_ext(file.filename)):
            flash(f'Pominięto „{file.filename}” — to nie jest zdjęcie.', 'warning')
            continue
        ok, err = _add_file(entry, file, force_kind='image')
        if ok:
            added += 1
        elif err:
            flash(err, 'warning')
    if added:
        db.session.commit()
        _audit('doc_files_add', entry.id, f'images +{added}')
        flash(f'Dodano zdjęć: {added}.', 'success')
    elif not any(f and f.filename for f in files):
        flash('Nie wybrano żadnego zdjęcia.', 'warning')
    return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id) + '#zdjecia')


@dokumentacja_bp.route('/plik/<int:file_id>/usun', methods=['POST'])
@login_required
def delete_file(file_id):
    f = get_or_404(DocFile, file_id)
    if not _can_edit_file(f):
        abort(403)
    entry_id = f.entry_id
    anchor = '#zdjecia' if f.kind == 'image' else '#dokumenty'
    _remove_disk_files(f)
    db.session.delete(f)
    db.session.commit()
    _audit('doc_file_delete', entry_id, f'file={f.original_name}')
    flash('Plik usunięty.', 'success')
    return redirect(url_for('dokumentacja.entry_detail', entry_id=entry_id) + anchor)


@dokumentacja_bp.route('/plik/<int:file_id>/podpis', methods=['POST'])
@login_required
def update_file_caption(file_id):
    f = get_or_404(DocFile, file_id)
    if not _can_edit_file(f):
        abort(403)
    f.caption = (request.form.get('caption', '').strip() or None)
    db.session.commit()
    anchor = '#zdjecia' if f.kind == 'image' else '#dokumenty'
    return redirect(url_for('dokumentacja.entry_detail', entry_id=f.entry_id) + anchor)


# ── Notatki ─────────────────────────────────────────────────────────────────

@dokumentacja_bp.route('/<int:entry_id>/notatki', methods=['POST'])
@login_required
def add_note(entry_id):
    entry = get_or_404(DocEntry, entry_id)
    body = request.form.get('body', '').strip()
    if not body:
        flash('Treść notatki jest pusta.', 'warning')
        return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id) + '#notatki')
    db.session.add(DocNote(entry_id=entry.id, body=body, user_id=current_user.id))
    db.session.commit()
    _audit('doc_note_add', entry.id)
    flash('Notatka dodana.', 'success')
    return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id) + '#notatki')


@dokumentacja_bp.route('/notatka/<int:note_id>/edytuj', methods=['POST'])
@login_required
def edit_note(note_id):
    note = get_or_404(DocNote, note_id)
    if not _can_edit_note(note):
        abort(403)
    body = request.form.get('body', '').strip()
    if not body:
        flash('Treść notatki jest pusta.', 'warning')
    else:
        note.body = body
        note.updated_at = datetime.now(UTC)
        db.session.commit()
        _audit('doc_note_edit', note.entry_id)
        flash('Notatka zapisana.', 'success')
    return redirect(url_for('dokumentacja.entry_detail', entry_id=note.entry_id) + '#notatki')


@dokumentacja_bp.route('/notatka/<int:note_id>/usun', methods=['POST'])
@login_required
def delete_note(note_id):
    note = get_or_404(DocNote, note_id)
    if not _can_edit_note(note):
        abort(403)
    entry_id = note.entry_id
    db.session.delete(note)
    db.session.commit()
    _audit('doc_note_delete', entry_id)
    flash('Notatka usunięta.', 'success')
    return redirect(url_for('dokumentacja.entry_detail', entry_id=entry_id) + '#notatki')


# ── Podgląd 3D (STEP) ───────────────────────────────────────────────────────

@dokumentacja_bp.route('/podglad3d/<int:file_id>')
@login_required
def view_3d(file_id):
    """Osobna strona z podglądem modelu STEP. Model wskazuje id rekordu, a nie
    ścieżka z adresu, więc nie da się wczytać dowolnego pliku."""
    f = get_or_404(DocFile, file_id)
    if not f.is_step:
        abort(404)
    return render_template(
        'dokumentacja/viewer3d.html', f=f, entry=f.entry,
        model_url=url_for('dokumentacja.serve_file', filename=f.filename),
        download_url=url_for('dokumentacja.serve_file', filename=f.filename, download=1),
        measurements_url=url_for('dokumentacja.measurements', file_id=f.id),
        element_names={n.node_key: n.name for n in f.element_names},
        can_rename_elements=current_user.can('dokumentacja.rename_elements'),
        element_names_url=url_for('dokumentacja.element_names', file_id=f.id),
        measurement_edit_url=url_for('dokumentacja.edit_measurement', mid=0),
        measurement_delete_url=url_for('dokumentacja.delete_measurement', mid=0),
    )


# ── Pomiary zapisane w podglądzie 3D ────────────────────────────────────────

_MEAS_KINDS = {'pt', 'circle', 'line', 'plane', 'cyl'}
_MEAS_VEC_KEYS = ('p', 'c', 'n', 'a', 'b', 'dir', 'anchor', 'o')     # wektory 3D
_MEAS_NUM_KEYS = ('r', 'len', 'area', 'part', 'faceId')
_MEAS_BOOL_KEYS = ('convex', 'free', 'arc')
_MEAS_MAX_BYTES = 20000


def _num(x):
    """Liczba skończona albo ValueError (JSON z przeglądarki może nieść NaN/Infinity)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError('liczba')
    return float(x)


def _vec3(v):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise ValueError('wektor')
    return [_num(x) for x in v]


def _clean_feature(f):
    if not isinstance(f, dict) or f.get('k') not in _MEAS_KINDS:
        raise ValueError('cecha')
    out = {'k': f['k']}
    for k in _MEAS_VEC_KEYS:
        if k in f:
            out[k] = _vec3(f[k])
    for k in _MEAS_NUM_KEYS:
        if k in f:
            out[k] = _num(f[k])
    for k in _MEAS_BOOL_KEYS:
        if k in f:
            out[k] = bool(f[k])
    return out


_OFFSET_VIEWS = ('3d', 'flat')


def _clean_offsets(offs):
    """Położenie etykiety wymiaru względem domyślnego: {'3d': [dx, dy] w pikselach, 'flat': [dx, dy] w mm}.
    Wartość None (tylko przy zmianie) oznacza powrót do położenia domyślnego."""
    if not isinstance(offs, dict):
        raise ValueError('przesunięcia')
    out = {}
    for view, v in offs.items():
        if view not in _OFFSET_VIEWS:
            raise ValueError('widok')
        if v is None:
            out[view] = None
            continue
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            raise ValueError('przesunięcie')
        out[view] = [max(-5000.0, min(5000.0, _num(v[0]))), max(-5000.0, min(5000.0, _num(v[1])))]
    return out


def _clean_measurement(data):
    """Zostawia tylko znane pola pomiaru (cechy, wiersze wyniku, odcinki) i odrzuca resztę."""
    if not isinstance(data, dict):
        raise ValueError('dane')
    rows = data.get('rows')
    segs = data.get('segs', [])
    if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
        raise ValueError('wiersze')
    clean_rows = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) != 2 or not all(isinstance(x, str) for x in r):
            raise ValueError('wiersz')
        clean_rows.append([r[0][:80], r[1][:80]])
    if not isinstance(segs, list) or len(segs) > 6:
        raise ValueError('odcinki')
    clean_segs = []
    for sg in segs:
        if not isinstance(sg, (list, tuple)) or len(sg) != 2:
            raise ValueError('odcinek')
        clean_segs.append([_vec3(sg[0]), _vec3(sg[1])])
    space = data.get('space', '3d')
    if space not in ('3d', 'flat'):
        raise ValueError('przestrzeń')
    out = {'A': _clean_feature(data.get('A')), 'B': _clean_feature(data.get('B')),
           'rows': clean_rows, 'segs': clean_segs, 'space': space}
    if 'K' in data:                       # współczynnik K, dla którego policzono pomiar na wykroju
        k = _num(data['K'])
        if not 0 <= k <= 1.5:
            raise ValueError('K')
        out['K'] = k
    if data.get('offsets') is not None:
        offs = {k: v for k, v in _clean_offsets(data['offsets']).items() if v is not None}
        if offs:
            out['offsets'] = offs
    if len(json.dumps(out)) > _MEAS_MAX_BYTES:
        raise ValueError('rozmiar')
    return out


def _step_file_or_404(file_id):
    f = get_or_404(DocFile, file_id)
    if not f.is_step:
        abort(404)
    return f


def _can_edit_measurement(m):
    return (m.user_id == current_user.id or _can_edit_file(m.file)
            or current_user.can('dokumentacja.manage_all'))


def _meas_json(m):
    return {
        'id': m.id, 'label': m.label or '', 'element': m.element or '',
        'data': json.loads(m.data),
        'author': m.author.username if m.author else '',
        'created_at': m.created_at.isoformat() if m.created_at else None,
        'can_edit': _can_edit_measurement(m),
    }


@dokumentacja_bp.route('/podglad3d/<int:file_id>/pomiary', methods=['GET', 'POST'])
@login_required
def measurements(file_id):
    f = _step_file_or_404(file_id)
    if request.method == 'GET':
        items = f.measurements.order_by(DocMeasurement.id).all()
        return jsonify([_meas_json(m) for m in items])
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error='Brak danych pomiaru.'), 400
    try:
        data = _clean_measurement(payload.get('data'))
    except ValueError:
        return jsonify(error='Nieprawidłowe dane pomiaru.'), 400
    if f.measurements.count() >= 500:
        return jsonify(error='Za dużo zapisanych pomiarów dla tego pliku.'), 400
    m = DocMeasurement(
        file_id=f.id, user_id=current_user.id, data=json.dumps(data),
        label=(str(payload.get('label') or '').strip()[:128] or None),
        element=(str(payload.get('element') or '').strip()[:256] or None),
    )
    db.session.add(m)
    db.session.commit()
    _audit('doc_measure_add', f.entry_id, f'file={f.original_name} measurement={m.id}')
    return jsonify(_meas_json(m)), 201


@dokumentacja_bp.route('/pomiar/<int:mid>/edytuj', methods=['POST'])
@login_required
def edit_measurement(mid):
    m = get_or_404(DocMeasurement, mid)
    if not _can_edit_measurement(m):
        abort(403)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error='Brak danych.'), 400
    if 'label' in payload:
        m.label = (str(payload.get('label') or '').strip()[:128] or None)
    if 'offsets' in payload:                                  # położenie etykiety wymiaru w danym widoku
        try:
            new_offs = _clean_offsets(payload['offsets'])
        except ValueError:
            return jsonify(error='Nieprawidłowe położenie etykiety.'), 400
        data = json.loads(m.data)
        offs = data.get('offsets', {})
        for view, v in new_offs.items():
            if v is None:
                offs.pop(view, None)
            else:
                offs[view] = v
        if offs:
            data['offsets'] = offs
        else:
            data.pop('offsets', None)
        m.data = json.dumps(data)
    db.session.commit()
    return jsonify(_meas_json(m))


@dokumentacja_bp.route('/pomiar/<int:mid>/usun', methods=['POST'])
@login_required
def delete_measurement(mid):
    m = get_or_404(DocMeasurement, mid)
    if not _can_edit_measurement(m):
        abort(403)
    entry_id, name = m.file.entry_id, m.file.original_name
    db.session.delete(m)
    db.session.commit()
    _audit('doc_measure_delete', entry_id, f'file={name} measurement={mid}')
    return jsonify(ok=True)


# ── Własne nazwy elementów w podglądzie 3D ──────────────────────────────────

_NODE_KEY_RE = re.compile(r'^r(/\d{1,5}){0,24}$')       # ścieżka indeksów w drzewie podzespołów


@dokumentacja_bp.route('/podglad3d/<int:file_id>/nazwy', methods=['POST'])
@login_required
def element_names(file_id):
    """Ustawia (albo kasuje, gdy nazwa pusta) własną nazwę elementu modelu. Wymaga uprawnienia
    'dokumentacja.rename_elements' (rola albo wyjątek na koncie użytkownika). Nazwy są wspólne dla wszystkich
    oglądających plik. Zapisane pomiary pliku dostają nową nazwę w polu elementu."""
    f = _step_file_or_404(file_id)
    if not current_user.can('dokumentacja.rename_elements'):
        return jsonify(error='Brak uprawnienia do zmiany nazw elementów.'), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error='Brak danych.'), 400
    key = payload.get('key')
    if not isinstance(key, str) or not _NODE_KEY_RE.match(key):
        return jsonify(error='Nieprawidłowy element.'), 400
    name = str(payload.get('name') or '').strip()[:128]
    row = f.element_names.filter_by(node_key=key).first()
    if name:
        if row:
            row.name, row.user_id, row.updated_at = name, current_user.id, datetime.now(UTC)
        else:
            if f.element_names.count() >= 2000:
                return jsonify(error='Za dużo nazw elementów dla tego pliku.'), 400
            db.session.add(DocElementName(file_id=f.id, node_key=key, name=name, user_id=current_user.id))
    elif row:
        db.session.delete(row)
    # pomiary zapisane z dawną nazwą elementu: podmieniamy ją w polu elementu ("A / B")
    old = str(payload.get('old_label') or '').strip()[:128]
    new = str(payload.get('new_label') or '').strip()[:128]
    if old and new and old != new:
        for m in f.measurements:
            if m.element:
                parts = m.element.split(' / ')
                if old in parts:
                    m.element = ' / '.join(new if p == old else p for p in parts)[:256]
    db.session.commit()
    _audit('doc_element_rename', f.entry_id, f'file={f.original_name} key={key} name={name}')
    return jsonify(key=key, name=name)


# ── Serwowanie plików ──────────────────────────────────────────────────────

@dokumentacja_bp.route('/plik/<path:filename>')
@login_required
def serve_file(filename):
    filename = os.path.basename(filename)
    folder = current_app.config['DOKUMENTACJA_UPLOAD_FOLDER']
    if not os.path.exists(os.path.join(folder, filename)):
        abort(404)

    rec = (DocFile.query.filter(
        (DocFile.filename == filename) |
        (DocFile.preview_filename == filename) |
        (DocFile.thumb_filename == filename)
    ).first())

    ext = _ext(filename)
    want_download = request.args.get('download') == '1'
    force_dl = ext in _FORCE_DOWNLOAD_EXT
    as_attachment = want_download or force_dl

    download_name = None
    if rec and rec.original_name:
        stem = rec.original_name.rsplit('.', 1)[0]
        download_name = f'{stem}.{ext}' if ext else rec.original_name

    resp = make_response(send_from_directory(
        folder, filename,
        as_attachment=as_attachment,
        download_name=download_name if as_attachment else None,
    ))
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    if force_dl:
        resp.headers['Content-Type'] = 'application/octet-stream'
    return resp
