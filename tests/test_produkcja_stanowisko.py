"""Testy stanowiska produkcji: pracownicy, plakietki QR, wpisy skanem w kolumny etapów."""
import json

import pytest

from app import db
from models import (User, ProdEmployee, ProdEmployeeDept, ProdStageColumn, ProdEntry,
                    ProdRow, ProductionDepartment)
from test_app import login, logout, _csrf
from test_produkcja import (prod_folder, planner, _import, _admin, _sheet_id,  # noqa: F401
                            _row_id)

BADGE = 'PSH-EMP:'


@pytest.fixture
def station(app):
    with app.app_context():
        u = User(username='stanowisko', email='stanowisko@test.pl', role='produkcja')
        u.set_password('Stan1234!')
        db.session.add(u)
        db.session.commit()
    return 'stanowisko'


def _add_employee(client, name, initials, departments=()):
    token = _csrf(client)
    resp = client.post('/produkcja/pracownicy/nowy', data={
        'name': name, 'initials': initials, 'departments': list(departments),
        '_csrf_token': token}, follow_redirects=True)
    return resp


def _emp(app, initials):
    with app.app_context():
        e = ProdEmployee.query.filter_by(initials=initials).first()
        return e.id, e.token


def _stage_id(app, sheet, label):
    with app.app_context():
        return ProdStageColumn.query.filter_by(
            sheet_id=_sheet_id(app, sheet), label=label).first().id


def _scan_emp(client, token):
    return client.post('/produkcja/skan/pracownik', json={'code': BADGE + token},
                       headers={'X-CSRF-Token': _csrf(client)})


def _entry(client, row_id, stage_id, confirm=False):
    return client.post('/produkcja/skan/wpis', json={
        'row_id': row_id, 'stage_id': stage_id, 'confirm': confirm},
        headers={'X-CSRF-Token': _csrf(client)})


def test_stage_columns_detected_on_import(app, client):
    _admin(client)
    _import(client)
    with app.app_context():
        labels = {s.label for s in ProdStageColumn.query.filter_by(sheet_id=_sheet_id(app, 'PSH'))}
    assert labels == {'SPAW.', 'KJ'}


def test_employee_crud_and_duplicate_initials_only_warn(app, client, planner):
    _admin(client)
    _add_employee(client, 'Jan Kowalski', 'JK')
    resp = _add_employee(client, 'Janina Kowalska', 'jk')   # te same inicjały: tylko ostrzeżenie
    assert 'ma też Jan Kowalski' in resp.get_data(as_text=True)
    with app.app_context():
        assert ProdEmployee.query.filter(ProdEmployee.initials.in_(['JK', 'jk'])).count() == 2
        ProdEmployee.query.filter_by(initials='jk').delete()
        db.session.commit()
    eid, token = _emp(app, 'JK')
    assert len(token) == 32
    token_before = token
    client.post(f'/produkcja/pracownicy/{eid}/token', data={'_csrf_token': _csrf(client)})
    assert _emp(app, 'JK')[1] != token_before


def test_badge_pdf(app, client):
    _admin(client)
    _add_employee(client, 'Zażółć Gęślą', 'ZG')
    eid, _ = _emp(app, 'ZG')
    resp = client.get(f'/produkcja/pracownicy/{eid}/plakietka.pdf')
    assert resp.status_code == 200 and resp.data.startswith(b'%PDF')
    assert client.get('/produkcja/pracownicy/plakietki.pdf').data.startswith(b'%PDF')


def test_scan_requires_station_role(app, client):
    login(client, 'oper', 'Oper1234!')     # kontroler
    assert client.get('/produkcja/skan').status_code == 403


def test_station_flow_writes_initials_and_history(app, client, station):
    _admin(client)
    _import(client)
    _add_employee(client, 'Jan Kowalski', 'JK')
    _add_employee(client, 'Adam Nowak', 'AN')
    _add_employee(client, 'Ola Wrona', 'OW')
    logout(client)
    login(client, station, 'Stan1234!')
    assert client.get('/produkcja/skan').status_code == 200

    # bez plakietki nie można wyszukać zlecenia
    r = client.post('/produkcja/skan/zlecenie', json={'o': 'ZO-T1'}, headers={'X-CSRF-Token': _csrf(client)})
    assert r.status_code == 401

    assert _scan_emp(client, 'zly-token').status_code == 404
    assert _scan_emp(client, _emp(app, 'JK')[1]).get_json()['employee']['initials'] == 'JK'

    r = client.post('/produkcja/skan/zlecenie', json={'o': 'zo-t1', 'p': 'kontrola zo api'},
                    headers={'X-CSRF-Token': _csrf(client)})
    order = r.get_json()['orders'][0]
    assert order['sheet'] == 'PSH' and {s['label'] for s in order['stages']} == {'SPAW.', 'KJ'}
    rid = order['row_id']
    kj = _stage_id(app, 'PSH', 'KJ')
    spaw = _stage_id(app, 'PSH', 'SPAW.')

    assert _entry(client, rid, kj).get_json()['value'] == 'JK'           # pusta komórka
    again = _entry(client, rid, kj).get_json()                            # ten sam pracownik
    assert again['status'] == 'already' and again['value'] == 'JK'

    _scan_emp(client, _emp(app, 'AN')[1])
    assert _entry(client, rid, kj).get_json()['value'] == 'JK/AN'         # druga osoba

    _scan_emp(client, _emp(app, 'OW')[1])
    third = _entry(client, rid, kj).get_json()                            # trzecia wymaga potwierdzenia
    assert third['status'] == 'confirm'
    assert _entry(client, rid, kj, confirm=True).get_json()['value'] == 'JK/AN/OW'

    # komórka z samym "x" zostaje zastąpiona; wpis w komórkę z inicjałami "MK" dopisuje drugą osobę
    assert _entry(client, rid, spaw).get_json()['value'] == 'MK/OW'

    with app.app_context():
        row = db.session.get(ProdRow, rid)
        assert json.loads(row.data)[10] == 'JK/AN/OW'
        assert sorted(json.loads(row.changed)) == [9, 10]
        assert ProdEntry.query.count() == 4   # powtórka tej samej osoby nie dubluje historii
        assert ProdEntry.query.first().user_id is not None


def test_unknown_order_and_logout(app, client, station):
    _admin(client)
    _import(client)
    _add_employee(client, 'Jan Kowalski', 'JK')
    logout(client)
    login(client, station, 'Stan1234!')
    _scan_emp(client, _emp(app, 'JK')[1])
    r = client.post('/produkcja/skan/zlecenie', json={'o': 'BRAK-ZO'}, headers={'X-CSRF-Token': _csrf(client)})
    assert r.status_code == 404
    client.post('/produkcja/skan/wyloguj', json={}, headers={'X-CSRF-Token': _csrf(client)})
    r = client.post('/produkcja/skan/zlecenie', json={'o': 'ZO-T1'}, headers={'X-CSRF-Token': _csrf(client)})
    assert r.status_code == 401


def test_assignments_enforced_when_enabled(app, client, station):
    _admin(client)
    _import(client)
    with app.app_context():
        dept = ProductionDepartment(name='Spawalnia testowa', order=99)
        other = ProductionDepartment(name='Malarnia testowa', order=100)
        db.session.add_all([dept, other])
        db.session.commit()
        dept_id, other_id = dept.id, other.id
    _add_employee(client, 'Jan Spawacz', 'JS', departments=[dept_id])
    _add_employee(client, 'Ola Malarka', 'OM', departments=[other_id])
    spaw = _stage_id(app, 'PSH', 'SPAW.')
    with app.app_context():
        db.session.get(ProdStageColumn, spaw).department_id = dept_id
        db.session.commit()
    rid = _row_id(app, 'PSH', 'ZO-T2')

    token = _csrf(client)
    client.post('/produkcja/pracownicy/ustawienia', data={'enforce_assignments': '1', '_csrf_token': token})
    logout(client)
    login(client, station, 'Stan1234!')

    _scan_emp(client, _emp(app, 'OM')[1])
    r = _entry(client, rid, spaw)
    assert r.status_code == 403 and r.get_json()['status'] == 'forbidden'
    _scan_emp(client, _emp(app, 'JS')[1])
    assert _entry(client, rid, spaw).get_json()['value'] == 'JS'

    # po wyłączeniu ograniczenia każdy może wpisać się wszędzie
    logout(client)
    _admin(client)
    client.post('/produkcja/pracownicy/ustawienia', data={'_csrf_token': _csrf(client)})
    logout(client)
    login(client, station, 'Stan1234!')
    _scan_emp(client, _emp(app, 'OM')[1])
    assert _entry(client, rid, spaw).get_json()['value'] == 'JS/OM'

    with app.app_context():
        ProdEmployeeDept.query.delete()
        ProductionDepartment.query.filter(ProductionDepartment.id.in_([dept_id, other_id])).delete(
            synchronize_session=False)
        db.session.commit()


def test_stage_config_page_and_reimport_keeps_config(app, client):
    _admin(client)
    _import(client)
    sid = _sheet_id(app, 'PSH')
    token = _csrf(client)
    # zostawiamy tylko KJ z własną nazwą
    kj_index = 10
    client.post(f'/produkcja/sheet/{sid}/etapy', data={
        'stage': [str(kj_index)], f'label_{kj_index}': 'Kontrola', f'dept_{kj_index}': '',
        '_csrf_token': token})
    with app.app_context():
        assert [(s.label, s.col_index) for s in ProdStageColumn.query.filter_by(sheet_id=sid)] == [('Kontrola', 10)]
    _import(client)
    with app.app_context():
        new_sid = _sheet_id(app, 'PSH')
        assert [(s.label, s.col_index) for s in ProdStageColumn.query.filter_by(sheet_id=new_sid)] == [('Kontrola', 10)]


def test_scan_page_supports_hardware_scanner_input(app, client, station):
    """Skaner pracujący jak klawiatura: pole z autofocusem, kamera tylko opcjonalnie."""
    login(client, station, 'Stan1234!')
    html = client.get('/produkcja/skan').get_data(as_text=True)
    assert 'id="st-scan"' in html and 'autofocus' in html
    assert 'id="st-cam-toggle"' in html
    assert 'id="qr-reader-wrap" hidden' in html        # kamera domyślnie ukryta


def test_existing_barcode_badges_identify_employee(app, client, station):
    """Plakietki, które już istnieją (kod kreskowy "JR 128"), rozpoznawane po kodzie, bez tokenu."""
    _admin(client)
    client.post('/produkcja/pracownicy/nowy', data={
        'name': 'Jan Rogoziński', 'badge_code': 'jr 128', 'initials': 'JRo', '_csrf_token': _csrf(client)})
    with app.app_context():
        emp = ProdEmployee.query.filter_by(name='Jan Rogoziński').first()
        assert emp.badge_code == 'JR128' and emp.initials == 'JRo'    # inicjały wpisane w systemie
    # inicjały są wymagane (nie podstawiamy numeru z kodu)
    resp = client.post('/produkcja/pracownicy/nowy', data={
        'name': 'Bez Inicjałów', 'badge_code': 'JR129', 'initials': '', '_csrf_token': _csrf(client)},
        follow_redirects=True)
    assert 'inicjały są wymagane' in resp.get_data(as_text=True)
    # ten sam kod u drugiej osoby jest odrzucany
    resp = client.post('/produkcja/pracownicy/nowy', data={
        'name': 'Ktoś Inny', 'badge_code': 'JR128', 'initials': 'KI', '_csrf_token': _csrf(client)},
        follow_redirects=True)
    assert 'ma już Jan Rogoziński' in resp.get_data(as_text=True)
    logout(client)
    login(client, station, 'Stan1234!')
    for scanned in ('JR128', 'jr128', ' JR 128 '):
        r = client.post('/produkcja/skan/pracownik', json={'code': scanned},
                        headers={'X-CSRF-Token': _csrf(client)})
        assert r.status_code == 200 and r.get_json()['employee']['initials'] == 'JRo'
    r = client.post('/produkcja/skan/pracownik', json={'code': 'JR99'}, headers={'X-CSRF-Token': _csrf(client)})
    assert r.status_code == 404


def test_bulk_add_employees_with_existing_badges(app, client):
    _admin(client)
    token = _csrf(client)
    lines = 'JR 128;Jan Rogoziński;JRo\nJR 129;Janusz Rybak;JaR\nJR 130\tMarek Kowal\tMK\nJR 131;Bez Inicjałów\n'
    resp = client.post('/produkcja/pracownicy/import', data={'lines': lines, '_csrf_token': token})
    body = resp.get_data(as_text=True)
    assert 'Dodano: 3' in body and 'Linia 4' in body
    with app.app_context():
        got = {e.badge_code: e.initials for e in ProdEmployee.query.all()}
        assert got == {'JR128': 'JRo', 'JR129': 'JaR', 'JR130': 'MK'}
    resp = client.post('/produkcja/pracownicy/import',
                       data={'lines': 'jr128;Duplikat;DU', '_csrf_token': _csrf(client)})
    assert 'Dodano: 0' in resp.get_data(as_text=True) and 'kod już istnieje' in resp.get_data(as_text=True)


def test_migration_adds_badge_code_to_existing_table(app):
    """Na produkcji tabela prod_employees już istnieje bez kolumny badge_code."""
    from sqlalchemy import create_engine, inspect, text
    import app as app_module
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE prod_employees (id INTEGER PRIMARY KEY, name VARCHAR(128), '
                          'initials VARCHAR(16), token VARCHAR(48))'))
    class FakeDb:   # _migrate_schema używa tylko db.engine
        pass
    FakeDb.engine = engine
    real = app_module.db
    app_module.db = FakeDb
    try:
        app_module._migrate_schema()
    finally:
        app_module.db = real
    assert 'badge_code' in [c['name'] for c in inspect(engine).get_columns('prod_employees')]


def test_delete_employee_keeps_history_and_disables_badge(app, client, station):
    _admin(client)
    _import(client)
    client.post('/produkcja/pracownicy/nowy', data={
        'name': 'Jan Rogoziński', 'badge_code': 'JR 128', 'initials': 'JRo', '_csrf_token': _csrf(client)})
    eid, _ = _emp(app, 'JRo')
    logout(client)
    login(client, station, 'Stan1234!')
    _scan_emp(client, 'JR128')
    rid = _row_id(app, 'PSH', 'ZO-T1')
    assert _entry(client, rid, _stage_id(app, 'PSH', 'KJ')).get_json()['value'] == 'JRo'
    logout(client)

    _admin(client)
    resp = client.post(f'/produkcja/pracownicy/{eid}/usun', data={'_csrf_token': _csrf(client)},
                       follow_redirects=True)
    assert 'Usunięto pracownika: Jan Rogoziński' in resp.get_data(as_text=True)
    with app.app_context():
        assert ProdEmployee.query.count() == 0
        entry = ProdEntry.query.first()
        assert entry.employee_id is None and entry.employee_name == 'Jan Rogoziński'
        assert json.loads(db.session.get(ProdRow, rid).data)[10] == 'JRo'   # komórka bez zmian
    assert 'Jan Rogoziński (usunięty)' in client.get('/produkcja/historia').get_data(as_text=True)
    logout(client)

    login(client, station, 'Stan1234!')   # plakietka usuniętego pracownika już nie działa
    r = client.post('/produkcja/skan/pracownik', json={'code': 'JR128'}, headers={'X-CSRF-Token': _csrf(client)})
    assert r.status_code == 404
