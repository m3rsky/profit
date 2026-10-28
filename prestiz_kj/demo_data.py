"""Dane demonstracyjne modułu KJ Prestiż. Deterministyczne, fikcyjne osoby, bez odczytu danych produkcyjnych."""
from datetime import datetime, timedelta, timezone

from models import db
from .models import (PkjOperation, PkjMaterialGroup, PkjMaterial, PkjCheckPoint, PkjReason, PkjEmployee,
                     PkjOrder, PkjOrderOperation, PkjOrderMaterial,
                     SCOPE_ALL, SCOPE_OPERATION, SCOPE_MATERIAL, SCOPE_GROUP)
from .service import SETTING_DEFAULTS, set_setting
from . import orders as ord_

SCENARIO_NUMBER = 'ZP-DEMO-001'

# (nazwa, KJ)
OPERATIONS = [('Wycinanie', False), ('Gięcie', True), ('Spawanie', True),
              ('Szlifowanie', False), ('Malowanie', True), ('Składanie', True)]
# (nazwa, KJ, grupa)
MATERIALS = [('Korpus', True, 'Obudowa'), ('Drzwi', True, 'Obudowa'), ('Płyta montażowa', True, None),
             ('Aparatura', False, None)]
REASONS = ['Spoina niepełna', 'Rysa na powłoce', 'Wymiar poza tolerancją', 'Brak otworu',
           'Odkształcenie', 'Zanieczyszczenie przed malowaniem']
EMPLOYEES = [('Adam Wrona', 'Gięcie'), ('Beata Sowa', 'Gięcie'), ('Cezary Lis', 'Spawanie'),
             ('Dorota Kos', 'Spawanie'), ('Emil Jaskółka', 'Malowanie'), ('Filip Szpak', 'Składanie'),
             ('Grażyna Orlik', 'Składanie'), ('Henryk Kruk', 'Wycinanie'), ('Irena Czajka', 'Szlifowanie')]

# (zakres, cel, tytuł, rodzaj, jednostka, min, max, zdjęcie przy NOK)
POINTS = [
    (SCOPE_ALL, None, 'Oznaczenie i etykieta zgodne ze zleceniem', 'ok_nok', None, None, None, False),
    (SCOPE_ALL, None, 'Brak uszkodzeń transportowych i zabrudzeń', 'ok_nok', None, None, None, False),
    (SCOPE_ALL, None, 'Zgodność z dokumentacją rysunkową', 'ok_nok', None, None, None, False),
    (SCOPE_ALL, None, 'Brak śladów korozji', 'ok_nok', None, None, None, False),
    (SCOPE_OPERATION, 'Gięcie', 'Kąty gięcia zgodne z rysunkiem', 'ok_nok', None, None, None, False),
    (SCOPE_OPERATION, 'Gięcie', 'Wymiar gabarytowy', 'value', 'mm', 598.0, 602.0, True),
    (SCOPE_OPERATION, 'Spawanie', 'Spoiny ciągłe, bez pęknięć i porów', 'ok_nok', None, None, None, True),
    (SCOPE_OPERATION, 'Spawanie', 'Brak odprysków spawalniczych', 'ok_nok', None, None, None, False),
    (SCOPE_OPERATION, 'Malowanie', 'Grubość powłoki', 'value', 'µm', 60.0, 120.0, False),
    (SCOPE_OPERATION, 'Malowanie', 'Jednolity kolor, brak zacieków', 'ok_nok', None, None, None, True),
    (SCOPE_OPERATION, 'Składanie', 'Moment dokręcenia zawiasów', 'value', 'Nm', 8.0, 12.0, False),
    (SCOPE_OPERATION, 'Składanie', 'Uszczelka ciągła na całym obwodzie', 'ok_nok', None, None, None, False),
    (SCOPE_MATERIAL, 'Korpus', 'Różnica przekątnych korpusu', 'value', 'mm', 0.0, 2.0, False),
    (SCOPE_MATERIAL, 'Korpus', 'Brak odkształceń boków', 'ok_nok', None, None, None, False),
    (SCOPE_MATERIAL, 'Drzwi', 'Spoina narożna kompletna', 'ok_nok', None, None, None, True),
    (SCOPE_MATERIAL, 'Drzwi', 'Luz drzwi względem korpusu', 'value', 'mm', 1.0, 3.0, False),
    (SCOPE_MATERIAL, 'Drzwi', 'Zamek działa płynnie', 'ok_nok', None, None, None, False),
    (SCOPE_MATERIAL, 'Płyta montażowa', 'Rozmieszczenie otworów zgodne z rysunkiem', 'ok_nok', None, None, None, False),
    (SCOPE_MATERIAL, 'Płyta montażowa', 'Powłoka ocynku bez uszkodzeń', 'ok_nok', None, None, None, False),
    (SCOPE_GROUP, 'Obudowa', 'Zgodność kolorów korpusu i drzwi', 'ok_nok', None, None, None, True),
    (SCOPE_GROUP, 'Obudowa', 'Dopasowanie drzwi do korpusu', 'ok_nok', None, None, None, False),
]


def configuration_exists():
    return any(m.query.first() is not None for m in (PkjOperation, PkjMaterial, PkjCheckPoint, PkjReason,
                                                    PkjEmployee))


def seed_configuration():
    """Zakłada konfigurację startową. Zwraca False (bez zmian), gdy w module są już dane konfiguracji."""
    if configuration_exists():
        return False
    ops = {}
    for i, (name, kj) in enumerate(OPERATIONS, 1):
        ops[name] = PkjOperation(name=name, order=i, kj_required=kj)
        db.session.add(ops[name])
    groups = {}
    for name in sorted({g for _, _, g in MATERIALS if g}):
        groups[name] = PkjMaterialGroup(name=name)
        db.session.add(groups[name])
    mats = {}
    for i, (name, kj, group) in enumerate(MATERIALS, 1):
        mats[name] = PkjMaterial(name=name, kj_flag=kj, order=i, group=groups.get(group))
        db.session.add(mats[name])
    for i, name in enumerate(REASONS, 1):
        db.session.add(PkjReason(name=name, order=i))
    for name, dept in EMPLOYEES:
        db.session.add(PkjEmployee(name=name, department=dept))
    for i, (scope, target, title, kind, unit, vmin, vmax, photo) in enumerate(POINTS, 1):
        point = PkjCheckPoint(title=title, kind=kind, unit=unit, min_value=vmin, max_value=vmax,
                              photo_required_on_nok=photo, scope=scope, order=i)
        if scope == SCOPE_OPERATION:
            point.operation = ops[target]
        elif scope == SCOPE_MATERIAL:
            point.material = mats[target]
        elif scope == SCOPE_GROUP:
            point.group = groups[target]
        db.session.add(point)
    for key, value in SETTING_DEFAULTS.items():
        set_setting(key, value)
    db.session.commit()
    return True


# ── Zlecenia demo (etap 2: bez kontroli, kontrole i zawroty dochodzą w kolejnych etapach) ─────────────────

def _employee_for(op_name):
    emp = PkjEmployee.query.filter_by(department=op_name, is_active=True).order_by(PkjEmployee.id).first()
    return emp or PkjEmployee.query.filter_by(is_active=True).order_by(PkjEmployee.id).first()


def _report(order, op_name, when):
    oop = PkjOrderOperation.query.filter_by(order_id=order.id, operation_name=op_name).one()
    ord_.report_operation(oop, _employee_for(op_name), actor='Dane demo', when=when)
    return oop


def _pass_control_history(oop, when):
    """Operacja KJ z historii: kontrola zgodna, RW wydane. Szczegółowe kontrole generuje etap 5."""
    for m in PkjOrderMaterial.query.filter_by(order_operation_id=oop.id).all():
        m.rw_status, m.element_status = 'wydane', 'zgodny'
    oop.status = 'zakonczona'
    ord_.add_event(oop.order_id, 'kontrola', f'Kontrola KJ operacji {oop.operation_name}: zgodna (dane historyczne)',
                   actor='Dane demo', when=when)
    ord_.add_event(oop.order_id, 'rw_wydane', 'RW na materiały wydane (symulacja)', actor='Dane demo', when=when)
    ord_.refresh_status(db.session.get(PkjOrder, oop.order_id))


def create_scenario_order():
    """ZP-DEMO-001 do pokazu na żywo: Wycinanie i Gięcie za sobą, Spawanie do zameldowania."""
    now = datetime.now(timezone.utc)
    order = ord_.create_order(SCENARIO_NUMBER, 'Szafa PSH 600x800x300', 'Klient demo', 1, actor='Dane demo',
                              scenario=True, when=now - timedelta(days=2))
    _report(order, 'Wycinanie', now - timedelta(days=2, hours=-1))
    giecie = _report(order, 'Gięcie', now - timedelta(days=1, hours=-2))
    _pass_control_history(giecie, now - timedelta(days=1, hours=-3))
    return order


def seed_orders():
    """Zakłada zlecenia demo, jeśli nie ma żadnych i konfiguracja ma operacje. Zwraca, czy coś dodano."""
    if PkjOrder.query.first() is not None or PkjOperation.query.first() is None:
        return False
    now = datetime.now(timezone.utc)
    create_scenario_order()
    o2 = ord_.create_order('ZP-DEMO-002', 'Szafa PSH 800x1000x300 IP65', 'Klient Alfa', 2, actor='Dane demo',
                           when=now - timedelta(hours=5))
    o3 = ord_.create_order('ZP-DEMO-003', 'Szafa PSH 600x600x250', 'Klient Beta', 1, actor='Dane demo',
                           when=now - timedelta(days=3))
    _report(o3, 'Wycinanie', now - timedelta(days=3, hours=-2))
    o4 = ord_.create_order('ZP-DEMO-004', 'Szafa PSH INOX 800x800x300', 'Klient Gamma', 3, actor='Dane demo',
                           when=now - timedelta(days=4))
    _report(o4, 'Wycinanie', now - timedelta(days=4, hours=-2))
    _report(o4, 'Gięcie', now - timedelta(days=3, hours=-1))
    db.session.commit()
    return True


def reset_scenario():
    """Przywraca ZP-DEMO-001 do stanu początkowego (inne zlecenia zostają)."""
    old = [o.id for o in PkjOrder.query.filter_by(is_demo_scenario=True).all()]
    if old:
        ord_.delete_orders(old)
    create_scenario_order()
    db.session.commit()
