"""Zdjęcia: zmniejszanie przy zapisie, jednorazowa kompresja starych plików i retencja.

Ustawienia trzyma tabela `app_settings` (edytuje je tylko superuser w /admin/zdjecia).
Zdjęcia Kontroli Końcowej (tabela `photos`, folder UPLOAD_FOLDER) mają profil "kk", zdjęcia QAR
(folder QAR_UPLOAD_FOLDER) profil "qar", domyślnie bez zmniejszania, bo tam bywają potrzebne
szczegóły do oględzin.
"""
import io
import os
import time
from datetime import datetime, timedelta, UTC

from PIL import Image, ImageOps
from sqlalchemy import or_

from models import db, AppSetting, AuditLog, Photo, Report, ReportItem

# klucz: (domyślna wartość, minimum, maksimum, czy 0 jest dozwolone jako "wyłączone")
SETTINGS = {
    'photo_kk_max_px':        (1600, 400, 8000, True),    # dłuższy bok zdjęcia Kontroli Końcowej, 0 = bez zmniejszania
    'photo_kk_quality':       (80, 30, 95, False),        # jakość JPEG Kontroli Końcowej
    'photo_qar_max_px':       (0, 400, 8000, True),       # dłuższy bok zdjęcia QAR, 0 = bez zmniejszania
    'photo_qar_quality':      (88, 30, 95, False),
    'photo_compress_days':    (30, 1, 3650, True),        # zmniejsz zdjęcia KK starsze niż N dni, 0 = wyłączone
    'photo_retention_days':   (90, 1, 3650, True),        # usuń zdjęcia KK starsze niż N dni, 0 = wyłączone
    'disk_limit_gb':          (50, 1, 100000, False),     # limit planu hostingu (pasek zajętości)
}
JPEG_EXT = ('jpg', 'jpeg')
LABELS = {
    'photo_kk_max_px': 'Kontrola Końcowa: dłuższy bok zdjęcia (px, 0 = bez zmniejszania)',
    'photo_kk_quality': 'Kontrola Końcowa: jakość JPEG (30 do 95)',
    'photo_qar_max_px': 'QAR: dłuższy bok zdjęcia (px, 0 = bez zmniejszania)',
    'photo_qar_quality': 'QAR: jakość JPEG (30 do 95, działa gdy podano bok)',
    'photo_compress_days': 'Zmniejszaj zdjęcia Kontroli Końcowej starsze niż (dni, 0 = wyłączone)',
    'photo_retention_days': 'Usuwaj zdjęcia Kontroli Końcowej starsze niż (dni, 0 = wyłączone)',
    'disk_limit_gb': 'Limit miejsca na hostingu (GB)',
}


def _now():
    return datetime.now(UTC).replace(tzinfo=None)


def get_settings():
    out = {}
    for key, (default, lo, hi, zero_ok) in SETTINGS.items():
        try:
            val = int(AppSetting.get(key, default))
        except (TypeError, ValueError):
            val = default
        out[key] = val
    return out


def save_settings(form):
    """Zapisuje ustawienia z formularza. Zwraca listę błędów (puste: zapisano)."""
    errors, values = [], {}
    for key, (default, lo, hi, zero_ok) in SETTINGS.items():
        raw = str(form.get(key, '')).strip()
        try:
            val = int(raw)
        except ValueError:
            errors.append(f'{LABELS[key]}: podaj liczbę całkowitą.')
            continue
        if not ((zero_ok and val == 0) or lo <= val <= hi):
            rng = f'{lo} do {hi}' + (' albo 0' if zero_ok else '')
            errors.append(f'{LABELS[key]}: dozwolone {rng}.')
            continue
        values[key] = val
    if errors:
        return errors
    for key, val in values.items():
        AppSetting.set(key, val)
    db.session.commit()
    return []


def _encode(img, ext, quality):
    buf = io.BytesIO()
    if ext in JPEG_EXT:
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.save(buf, 'JPEG', quality=quality, optimize=True, progressive=True)
    else:
        img.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def _shrunk(src, max_px, quality, ext):
    """Zdjęcie po obróceniu wg EXIF i zmniejszeniu do `max_px` (0: bez zmniejszania), jako bajty."""
    img = Image.open(src)
    img = ImageOps.exif_transpose(img)
    if max_px and max(img.size) > max_px:
        img.thumbnail((max_px, max_px), Image.LANCZOS)
    return _encode(img, ext, quality), img.size


def save_upload(file, dest_path, max_px, quality):
    """Zapisuje przesłane zdjęcie. JPEG i PNG są zmniejszane do `max_px` (0: bez zmiany),
    inne formaty i błędy przetwarzania zapisują oryginał. Zwraca True, gdy plik został przetworzony."""
    ext = dest_path.rsplit('.', 1)[-1].lower()
    if max_px and ext in JPEG_EXT + ('png',):
        try:
            data, _ = _shrunk(file.stream, max_px, quality, ext)
            with open(dest_path, 'wb') as fh:
                fh.write(data)
            return True
        except Exception:
            try:
                file.stream.seek(0)
            except Exception:
                pass
    file.save(dest_path)
    return False


def shrink_file(path, max_px, quality):
    """Zmniejsza istniejący plik JPEG w miejscu (przez plik tymczasowy). Zwraca (stary, nowy rozmiar).
    Plik jest podmieniany tylko wtedy, gdy nowy jest mniejszy."""
    old = os.path.getsize(path)
    ext = path.rsplit('.', 1)[-1].lower()
    if ext not in JPEG_EXT:
        return old, old
    data, _ = _shrunk(path, max_px, quality, ext)
    if len(data) >= old:
        return old, old
    tmp = path + '.tmp'
    try:
        with open(tmp, 'wb') as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return old, len(data)


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _compress_query(settings):
    days = settings['photo_compress_days']
    if not days:
        return None
    cutoff = _now() - timedelta(days=days)
    return Photo.query.filter(Photo.optimized_at.is_(None),
                              or_(Photo.uploaded_at < cutoff, Photo.uploaded_at.is_(None)))


def _purge_query(settings):
    days = settings['photo_retention_days']
    if not days:
        return None
    cutoff = _now() - timedelta(days=days)
    return (Photo.query.join(ReportItem, ReportItem.id == Photo.report_item_id)
            .join(Report, Report.id == ReportItem.report_id)
            .filter(Report.status == 'completed', Photo.uploaded_at < cutoff))


def _file_bytes(photos, folder):
    total = 0
    for p in photos:
        try:
            total += os.path.getsize(os.path.join(folder, p.filename))
        except OSError:
            pass
    return total


def preview(upload_folder, qar_folder, base_dir):
    """Liczby do strony ustawień: zajętość i szacunek skutków obu operacji."""
    s = get_settings()
    out = {'settings': s}
    uploads = dir_size(upload_folder)
    qar = dir_size(qar_folder)
    total = dir_size(base_dir)
    out['usage'] = {'uploads': uploads, 'qar': qar, 'total': total, 'limit': s['disk_limit_gb'] * 1024 ** 3}

    cq = _compress_query(s)
    if cq is None:
        out['compress'] = {'enabled': False}
    else:
        photos = cq.all()
        size = _file_bytes(photos, upload_folder)
        ratio, sampled = 0.1, 0
        old_sum = new_sum = 0
        for p in photos:
            if sampled >= 5:
                break
            path = os.path.join(upload_folder, p.filename)
            ext = p.filename.rsplit('.', 1)[-1].lower()
            if ext not in JPEG_EXT or not os.path.exists(path):
                continue
            try:
                data, _ = _shrunk(path, s['photo_kk_max_px'], s['photo_kk_quality'], ext)
                old_sum += os.path.getsize(path)
                new_sum += min(len(data), os.path.getsize(path))
                sampled += 1
            except Exception:
                continue
        if old_sum:
            ratio = new_sum / old_sum
        out['compress'] = {'enabled': True, 'count': len(photos), 'bytes': size,
                           'estimated_saving': int(size * (1 - ratio))}

    pq = _purge_query(s)
    if pq is None:
        out['purge'] = {'enabled': False}
    else:
        photos = pq.all()
        out['purge'] = {'enabled': True, 'count': len(photos), 'bytes': _file_bytes(photos, upload_folder)}
    return out


def run_compress(upload_folder, budget_seconds=15):
    """Jedna partia kompresji starych zdjęć w limicie czasu. Zwraca statystyki i liczbę pozostałych."""
    s = get_settings()
    q = _compress_query(s)
    if q is None:
        return None
    started = time.monotonic()
    done = errors = saved = 0
    while time.monotonic() - started < budget_seconds:
        batch = q.order_by(Photo.id).limit(20).all()
        if not batch:
            break
        for p in batch:
            path = os.path.join(upload_folder, p.filename)
            try:
                if os.path.exists(path):
                    old, new = shrink_file(path, s['photo_kk_max_px'], s['photo_kk_quality'])
                    saved += old - new
            except Exception:
                errors += 1
            p.optimized_at = _now()    # także przy błędzie i braku pliku, żeby partia nie wracała w kółko
            done += 1
        db.session.commit()
        if time.monotonic() - started >= budget_seconds:
            break
    remaining = q.count()
    return {'done': done, 'errors': errors, 'saved': saved, 'remaining': remaining}


def run_purge(upload_folder, user_id=None, budget_seconds=15):
    """Jedna partia usuwania zdjęć starszych niż okres retencji (tylko z zakończonych raportów)."""
    s = get_settings()
    q = _purge_query(s)
    if q is None:
        return None
    started = time.monotonic()
    done = freed = 0
    while time.monotonic() - started < budget_seconds:
        batch = q.order_by(Photo.id).limit(50).all()
        if not batch:
            break
        paths = []
        for p in batch:
            paths.append(os.path.join(upload_folder, p.filename))
            db.session.delete(p)
            done += 1
        db.session.commit()    # najpierw wiersze: przerwanie procesu zostawi najwyżej plik bez wiersza, nie wiersz bez pliku
        for path in paths:
            try:
                size = os.path.getsize(path)
                os.remove(path)
                freed += size
            except OSError:
                pass
    remaining = q.count()
    if done:
        db.session.add(AuditLog(user_id=user_id, action='photos_purge', target_type='photos',
                                detail=f'usunięto {done} zdjęć, zwolniono {freed} B, retencja {s["photo_retention_days"]} dni'))
        db.session.commit()
    return {'done': done, 'freed': freed, 'remaining': remaining}
