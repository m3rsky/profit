import os
import uuid
import mimetypes
from datetime import datetime, timezone

from flask import (render_template, redirect, url_for, request, flash,
                   abort, current_app, send_from_directory, make_response)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import db, get_or_404, AuditLog, DocEntry, DocFile, DocNote
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


def _upload_dir():
    d = current_app.config['DOKUMENTACJA_UPLOAD_FOLDER']
    os.makedirs(d, exist_ok=True)
    return d


def _can_edit_entry(entry):
    return current_user.is_admin or entry.user_id == current_user.id


def _can_edit_file(f):
    return (current_user.is_admin or f.entry.user_id == current_user.id
            or f.user_id == current_user.id)


def _can_edit_note(note):
    return current_user.is_admin or note.user_id == current_user.id


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


# ── Lista teczek ─────────────────────────────────────────────────────────────

@dokumentacja_bp.route('/')
@login_required
def list_entries():
    q = request.args.get('q', '').strip()
    query = DocEntry.query
    if q:
        like = f'%{q}%'
        query = query.filter(DocEntry.title.ilike(like) | DocEntry.description.ilike(like))
    entries = query.order_by(DocEntry.created_at.desc()).all()
    return render_template('dokumentacja/list.html', entries=entries, q=q)


# ── Nowa teczka ─────────────────────────────────────────────────────────────

@dokumentacja_bp.route('/nowa', methods=['GET', 'POST'])
@login_required
def new_entry():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        if not title:
            flash('Tytuł jest wymagany.', 'error')
            return render_template('dokumentacja/new.html', form=request.form)
        entry = DocEntry(title=title, description=description or None,
                         user_id=current_user.id)
        db.session.add(entry)
        db.session.commit()
        _audit('doc_create', entry.id, f'title={title}')
        flash('Teczka utworzona. Dodaj dokumenty, zdjęcia i notatki.', 'success')
        return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id))
    return render_template('dokumentacja/new.html', form={})


# ── Szczegóły teczki ────────────────────────────────────────────────────────

@dokumentacja_bp.route('/<int:entry_id>')
@login_required
def entry_detail(entry_id):
    entry = get_or_404(DocEntry, entry_id)
    return render_template('dokumentacja/detail.html', entry=entry,
                           can_edit=_can_edit_entry(entry))


@dokumentacja_bp.route('/<int:entry_id>/edytuj', methods=['POST'])
@login_required
def edit_entry(entry_id):
    entry = get_or_404(DocEntry, entry_id)
    if not _can_edit_entry(entry):
        abort(403)
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    if not title:
        flash('Tytuł jest wymagany.', 'error')
        return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id))
    entry.title = title
    entry.description = description or None
    entry.updated_at = datetime.now(UTC)
    db.session.commit()
    _audit('doc_edit', entry.id, f'title={title}')
    flash('Zapisano zmiany.', 'success')
    return redirect(url_for('dokumentacja.entry_detail', entry_id=entry.id))


@dokumentacja_bp.route('/<int:entry_id>/usun', methods=['POST'])
@login_required
def delete_entry(entry_id):
    entry = get_or_404(DocEntry, entry_id)
    if not _can_edit_entry(entry):
        abort(403)
    for f in entry.files:
        _remove_disk_files(f)
    title = entry.title
    db.session.delete(entry)
    db.session.commit()
    _audit('doc_delete', entry_id, f'title={title}')
    flash(f'Teczka „{title}” usunięta.', 'success')
    return redirect(url_for('dokumentacja.list_entries'))


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
