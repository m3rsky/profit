import os
import shutil


def migrate_legacy_uploads(legacy_dir, new_dir, logger=None):
    """Przenosi pliki Dokumentacji ze starego folderu pod static/ do folderu
    poza static. Bezpieczne do wielokrotnego wywołania: istniejącego pliku nie
    nadpisuje, a po przeniesieniu usuwa pusty stary folder.
    Zwraca liczbę przeniesionych plików."""
    if not legacy_dir or not os.path.isdir(legacy_dir):
        return 0
    if os.path.abspath(legacy_dir) == os.path.abspath(new_dir):
        return 0
    os.makedirs(new_dir, exist_ok=True)
    moved = 0
    for name in os.listdir(legacy_dir):
        src = os.path.join(legacy_dir, name)
        dst = os.path.join(new_dir, name)
        if not os.path.isfile(src) or os.path.exists(dst):
            continue
        try:
            shutil.move(src, dst)
            moved += 1
        except OSError as exc:
            if logger:
                logger.warning('Dokumentacja: nie przeniesiono %s: %s', src, exc)
    try:
        os.rmdir(legacy_dir)  # tylko gdy pusty
    except OSError:
        pass
    if moved and logger:
        logger.info('Dokumentacja: przeniesiono %d plików do %s', moved, new_dir)
    return moved
