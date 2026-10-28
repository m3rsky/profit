"""Logika domenowa modułu KJ Prestiż, bez zależności od Flaska poza konfiguracją folderu zdjęć."""
import os

from flask import current_app

from models import db
from .models import (PkjSetting, PkjPhoto, PkjCheckPoint, ORDER_TABLES_DELETE_ORDER,
                     CONFIG_TABLES_DELETE_ORDER)

SETTING_DEFAULTS = {
    'approval_mode': 'auto',        # auto: kontroler tworzy ZN od razu | manager: ZN czeka na zatwierdzenie
    'scrap_mode': 'new_element',    # new_element: tylko ZN na nowy element | with_cost: ZN + rozchód z kosztem
    'repair_hour_cost': '120',      # PLN za godzinę poprawki (raport kosztu zawrotów)
    'repair_op_hours': '1.5',       # godzin pracy na jedną operację naprawczą (raport kosztu zawrotów)
    'scrap_material_cost': '350',   # PLN, koszt materiału jednego zezłomowanego elementu
}
SETTING_CHOICES = {
    'approval_mode': {'auto': 'Kontroler tworzy zlecenie naprawcze od razu',
                      'manager': 'Zlecenie naprawcze czeka na zatwierdzenie kierownika'},
    'scrap_mode': {'new_element': 'Tylko zlecenie na nowy element',
                   'with_cost': 'Zlecenie na nowy element i dokument rozchodu materiału z kosztem'},
}


def get_setting(key):
    row = db.session.get(PkjSetting, key)
    return row.value if row and row.value is not None else SETTING_DEFAULTS.get(key)


def set_setting(key, value):
    row = db.session.get(PkjSetting, key)
    if row is None:
        db.session.add(PkjSetting(key=key, value=value))
    else:
        row.value = value


def upload_folder():
    folder = current_app.config.get('PKJ_UPLOAD_FOLDER', '')
    os.makedirs(folder, exist_ok=True)
    return folder


def remove_upload(filename):
    """Kasuje plik z folderu zdjęć modułu (bez błędu, gdy go nie ma)."""
    if not filename:
        return
    try:
        os.remove(os.path.join(upload_folder(), os.path.basename(filename)))
    except OSError:
        pass


def reset_orders():
    """Kasuje wyłącznie zlecenia demo i ich pochodne (tabele pkj_), razem ze zdjęciami. Konfiguracja zostaje."""
    for photo in PkjPhoto.query.all():
        remove_upload(photo.filename)
    for model in ORDER_TABLES_DELETE_ORDER:
        model.query.delete()
    db.session.commit()


def reset_all():
    """Kasuje zlecenia i całą konfigurację modułu (tylko tabele pkj_), razem z plikami."""
    reset_orders()
    for point in PkjCheckPoint.query.filter(PkjCheckPoint.image.isnot(None)).all():
        remove_upload(point.image)
    for model in CONFIG_TABLES_DELETE_ORDER:
        model.query.delete()
    db.session.commit()


def module_counts():
    """Liczniki na ekran Przegląd."""
    from .models import (PkjOperation, PkjMaterial, PkjCheckPoint as P, PkjReason, PkjEmployee, PkjOrder)
    return {
        'operations': PkjOperation.query.count(),
        'kj_operations': PkjOperation.query.filter_by(kj_required=True).count(),
        'materials': PkjMaterial.query.count(),
        'kj_materials': PkjMaterial.query.filter_by(kj_flag=True).count(),
        'points': P.query.count(),
        'reasons': PkjReason.query.count(),
        'employees': PkjEmployee.query.count(),
        'orders': PkjOrder.query.count(),
    }
