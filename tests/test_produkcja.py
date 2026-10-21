"""Testy modulu Lista produkcyjna (import Excela, edycja, eksport, listy kontrolne)."""
import io
import json
from datetime import datetime

import pytest
from openpyxl import Workbook, load_workbook

from app import app as flask_app, db
from models import (User, Report, ReportItem, Alert, ProdFile, ProdSheet, ProdRow,
                    ProdSheetAccess, ProdEmployee, ProdEmployeeDept, ProdStageColumn,
                    ProdEntry, ProdSetting)
from test_app import login, logout, _csrf

PRODUCT = 'Kontrola ZO API'   # pasuje do szablonu z conftest (dopasowanie po tokenach)


def _make_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = 'PSH'
    ws.append(['ZAM', 'ROZP.', 'CZAS REALIZACJI', 'TERMIN REALIZACJI', 'NAZWA TOW.', 'ILOŚĆ',
               'ODBIORCA', 'NUMER ZO', 'KOLOR', 'SPAW.', 'KJ'])
    ws.append([datetime(2026, 9, 7), datetime(2026, 9, 8), 3, '=WORKDAY(A2,C2)', PRODUCT, 2,
               'ELEKTRO TEST', 'ZO-T1', 'RAL 7035', 'MK', None])
    ws.append([datetime(2026, 9, 8), datetime(2026, 9, 9), 5, '=WORKDAY(A3,C3)', 'PSH 862', 1,
               'KLIENT DRUGI', 'ZO-T2', 'RAL 7035', None, None])
    ws2 = wb.create_sheet('SP')
    ws2.append(['zam', 'roz', 'czas realiz.', 'termin realizacji', 'NAZWA TOW.', 'ILOŚĆ', 'Z',
                'ODBIORCA', 'NUMER ZO'])
    ws2.append([datetime(2026, 9, 7), None, 2, None, 'SP 400X750', 1, None, 'SP KLIENT', 'ZO-S1'])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@pytest.fixture(autouse=True)
def prod_folder(app, tmp_path):
    app.config['PRODUKCJA_FOLDER'] = str(tmp_path / 'produkcja')
    yield
    with app.app_context():
        for rep in Report.query.filter(Report.qr_key.like('%|ZO-T%')).all():
            ReportItem.query.filter_by(report_id=rep.id).delete()
            db.session.delete(rep)
        ProdEntry.query.delete()
        ProdEmployeeDept.query.delete()
        ProdEmployee.query.delete()
        ProdStageColumn.query.delete()
        ProdSetting.query.delete()
        ProdRow.query.delete()
        ProdSheetAccess.query.delete()
        ProdSheet.query.delete()
        ProdFile.query.delete()
        User.query.filter(User.username.in_(['planista', 'stanowisko'])).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture
def planner(app):
    with app.app_context():
        u = User(username='planista', email='planista@test.pl', role='planowanie')
        u.set_password('Plan1234!')
        db.session.add(u)
        db.session.commit()
    return 'planista'


def _import(client, buf=None):
    token = _csrf(client)
    return client.post('/produkcja/import', data={
        'file': (buf or _make_xlsx(), 'lista.xlsx'), '_csrf_token': token,
    }, content_type='multipart/form-data', follow_redirects=True)


def _admin(client):
    login(client, 'admin', 'Admin1234!')
    return client


def _sheet_id(app, name):
    with app.app_context():
        return ProdSheet.query.filter_by(name=name).first().id


def _row_id(app, sheet, zo):
    with app.app_context():
        return ProdRow.query.filter_by(sheet_id=_sheet_id(app, sheet), zo=zo).first().id


def test_import_reads_all_sheets_and_formulas(app, client):
    _admin(client)
    resp = _import(client)
    assert 'Wczytano 2 arkuszy' in resp.get_data(as_text=True)
    with app.app_context():
        psh = ProdSheet.query.filter_by(name='PSH').first()
        assert psh.rows.count() == 2
        assert psh.mapping['zo'] == 7 and psh.mapping['product'] == 4
        assert psh.formula_cols == [3]
        row = ProdRow.query.filter_by(sheet_id=psh.id, zo='ZO-T1').first()
        assert json.loads(row.data)[0] == '2026-09-07'
        assert json.loads(row.formulas) == {'3': '=WORKDAY(A2,C2)'}


def test_non_planning_user_forbidden(app, client):
    login(client, 'oper', 'Oper1234!')
    assert client.get('/produkcja/').status_code == 403


def test_planner_sees_only_granted_sheets(app, client, planner):
    _admin(client)
    _import(client)
    psh_id, sp_id = _sheet_id(app, 'PSH'), _sheet_id(app, 'SP')
    with app.app_context():
        uid = User.query.filter_by(username='planista').first().id
    token = _csrf(client)
    client.post('/produkcja/access', data={'access': [f'{uid}:{psh_id}'], '_csrf_token': token})
    logout(client)
    login(client, planner, 'Plan1234!')
    body = client.get('/produkcja/').get_data(as_text=True)
    assert 'PSH' in body and '>SP<' not in body
    assert client.get(f'/produkcja/sheet/{psh_id}').status_code == 200
    assert client.get(f'/produkcja/sheet/{sp_id}').status_code == 403


def test_cell_edit_and_formula_protection(app, client):
    _admin(client)
    _import(client)
    rid = _row_id(app, 'PSH', 'ZO-T1')
    token = _csrf(client)
    h = {'X-CSRF-Token': token}
    r = client.post(f'/produkcja/row/{rid}/cell', json={'col': 9, 'value': 'AB'}, headers=h)
    assert r.get_json()['ok']
    r = client.post(f'/produkcja/row/{rid}/cell', json={'col': 3, 'value': '2026-01-01'}, headers=h)
    assert r.status_code == 400                      # kolumna z formuła jest tylko do odczytu
    # zmiana czasu realizacji przelicza termin (4 dni robocze od pon. 7.09.2026 -> pt. 11.09)
    r = client.post(f'/produkcja/row/{rid}/cell', json={'col': 2, 'value': '4'}, headers=h)
    assert r.get_json()['cells']['3'] == '11.09.2026'
    with app.app_context():
        row = db.session.get(ProdRow, rid)
        assert json.loads(row.data)[9] == 'AB'
        assert sorted(json.loads(row.changed)) == [2, 9]


def test_add_and_delete_row(app, client):
    _admin(client)
    _import(client)
    sid = _sheet_id(app, 'PSH')
    token = _csrf(client)
    client.post(f'/produkcja/sheet/{sid}/new', data={
        'order_date': '2026-09-14', 'product': 'PSH 999', 'qty': '4', 'client': 'NOWY',
        'zo': 'ZO-T9', 'lead_days': '2', '_csrf_token': token})
    with app.app_context():
        new = ProdRow.query.filter_by(sheet_id=sid, zo='ZO-T9').first()
        assert new.src_row is None and new.position == 4
        assert json.loads(new.data)[3] == '2026-09-16'
        new_id = new.id
    token = _csrf(client)
    old = _row_id(app, 'PSH', 'ZO-T2')
    client.post(f'/produkcja/sheet/{sid}/delete', data={'row_ids': [new_id, old], '_csrf_token': token})
    with app.app_context():
        assert ProdRow.query.filter_by(sheet_id=sid).count() == 1
        assert db.session.get(ProdSheet, sid).deleted_rows == [3]


def test_export_keeps_formulas_and_applies_changes(app, client, tmp_path):
    _admin(client)
    _import(client)
    sid = _sheet_id(app, 'PSH')
    rid = _row_id(app, 'PSH', 'ZO-T1')
    token = _csrf(client)
    client.post(f'/produkcja/row/{rid}/cell', json={'col': 10, 'value': 'JK'},
                headers={'X-CSRF-Token': token})
    client.post(f'/produkcja/sheet/{sid}/new', data={
        'order_date': '2026-09-14', 'product': 'PSH 999', 'qty': '4', 'zo': 'ZO-T9',
        'lead_days': '2', '_csrf_token': token})
    client.post(f'/produkcja/sheet/{sid}/delete',
                data={'row_ids': [_row_id(app, 'PSH', 'ZO-T2')], '_csrf_token': token})
    resp = client.post('/produkcja/export', data={'_csrf_token': token})
    assert resp.status_code == 200
    out = tmp_path / 'kopia.xlsx'
    out.write_bytes(resp.data)
    ws = load_workbook(out)['PSH']
    assert ws['K2'].value == 'JK'                       # zmieniona komórka
    assert ws['D2'].value == '=WORKDAY(A2,C2)'          # formuła nietknięta
    assert ws['H3'].value is None                       # usunięty wiersz wyczyszczony
    assert ws['H4'].value == 'ZO-T9'                    # nowy wiersz na końcu
    assert ws['D4'].value == '=WORKDAY(A4,C4)'          # formuła przesunięta do nowego wiersza
    assert 'SP' in load_workbook(out).sheetnames


def test_reimport_keeps_access_and_makes_backup(app, client, planner):
    _admin(client)
    _import(client)
    psh_id = _sheet_id(app, 'PSH')
    with app.app_context():
        uid = User.query.filter_by(username='planista').first().id
    token = _csrf(client)
    client.post('/produkcja/access', data={'access': [f'{uid}:{psh_id}'], '_csrf_token': token})
    _import(client)
    with app.app_context():
        assert ProdFile.query.filter_by(kind='backup').count() == 1
        new_id = ProdSheet.query.filter_by(name='PSH').first().id
        assert ProdSheetAccess.query.filter_by(user_id=uid, sheet_id=new_id).count() == 1


def test_create_checklists_and_pairing(app, client):
    _admin(client)
    _import(client)
    sid = _sheet_id(app, 'PSH')
    rid = _row_id(app, 'PSH', 'ZO-T1')
    token = _csrf(client)
    resp = client.post(f'/produkcja/sheet/{sid}/checklists',
                       data={'row_ids': [rid], '_csrf_token': token}, follow_redirects=True)
    assert 'utworzono 2 list kontrolnych' in resp.get_data(as_text=True)
    with app.app_context():
        row = db.session.get(ProdRow, rid)
        assert row.checklists_at is not None
        reports = Report.query.filter_by(qr_key=f'{PRODUCT.upper()}|ELEKTRO TEST|ZO-T1', report_type='kontroler').all()
        assert len(reports) == 2
        assert all(r.qr_key == f'{PRODUCT.upper()}|ELEKTRO TEST|ZO-T1' for r in reports)
    # parowanie widoczne w arkuszu (0/2 zakończonych)
    assert '0/2' in client.get(f'/produkcja/sheet/{sid}').get_data(as_text=True)
    # ponowna próba nie dubluje list
    resp = client.post(f'/produkcja/sheet/{sid}/checklists',
                       data={'row_ids': [rid], '_csrf_token': token}, follow_redirects=True)
    assert 'już istnieją' in resp.get_data(as_text=True)
    with app.app_context():
        assert Report.query.filter_by(qr_key=f'{PRODUCT.upper()}|ELEKTRO TEST|ZO-T1').count() == 2


def test_checklists_without_template_do_not_create_lists(app, client):
    _admin(client)
    _import(client)
    sid = _sheet_id(app, 'PSH')
    rid = _row_id(app, 'PSH', 'ZO-T2')
    token = _csrf(client)
    resp = client.post(f'/produkcja/sheet/{sid}/checklists',
                       data={'row_ids': [rid], '_csrf_token': token}, follow_redirects=True)
    assert 'brak pasującego szablonu' in resp.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(ProdRow, rid).checklists_at is None
        assert Report.query.filter(Report.qr_key.like('%|ZO-T2')).count() == 0


def test_forms_use_the_csrf_field_name_the_app_expects(app, client, planner):
    """Regresja: formularze muszą wysyłać `_csrf_token` (nazwę sprawdza csrf_protect),
    a nie `csrf_token`. Testy POST podają token ręcznie, więc tego nie wychwytują."""
    import re
    _admin(client)
    _import(client)
    sid = _sheet_id(app, 'PSH')
    pages = ['/produkcja/', '/produkcja/import', '/produkcja/access', f'/produkcja/sheet/{sid}',
             f'/produkcja/sheet/{sid}/new', f'/produkcja/sheet/{sid}/etapy',
             '/produkcja/pracownicy', '/produkcja/pracownicy/nowy']
    for url in pages:
        html = client.get(url).get_data(as_text=True)
        names = re.findall(r'<input[^>]*type="hidden"[^>]*name="(\w*csrf_token)"', html)
        assert names, f'{url}: brak pola CSRF'
        assert set(names) == {'_csrf_token'}, f'{url}: zła nazwa pola CSRF {set(names)}'


def _make_headerless_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = 'PROFIT'
    # brak wiersza nagłówków, układ jak w pliku produkcyjnym
    ws.append(['23-06', '26-06', None, None, PRODUCT, 2, None, 'ELEKTRO TEST', 'ZO-T5', None, 'RAL 7035', 'x'])
    ws.append(['24-06', '29-06', None, None, 'PSH 862', 1, None, 'KLIENT', 'ZO-T6', None, 'RAL 9003', 'x'])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_headerless_sheet_gets_default_names_and_user_can_rename(app, client):
    _admin(client)
    _import(client, _make_headerless_xlsx())
    sid = _sheet_id(app, 'PROFIT')
    with app.app_context():
        sh = db.session.get(ProdSheet, sid)
        assert sh.columns[:9] == ['ZAM', 'ROZP.', 'CZAS REALIZACJI', 'TERMIN REALIZACJI',
                                  'NAZWA TOW.', 'ILOŚĆ', 'Z', 'ODBIORCA', 'NUMER ZO']
        assert sh.columns[10] == 'K'                      # dalej litery kolumn
    html = client.get(f'/produkcja/sheet/{sid}/etapy').get_data(as_text=True)
    assert 'nie ma wiersza nagłówków' in html and 'RAL 7035' in html     # przykłady wartości

    # nazwanie kolumn K (kolor) i L (etap), wskazanie roli koloru i etapu
    form = {'_csrf_token': _csrf(client)}
    with app.app_context():
        for i, c in enumerate(db.session.get(ProdSheet, sid).columns):
            form[f'name_{i}'] = c
            if i == 8:
                form['role_8'] = 'zo'
            if i == 4:
                form['role_4'] = 'product'
    form['name_10'] = 'KOLOR'
    form['role_10'] = 'color'
    form['name_11'] = 'GIĘCIE'
    form['stage'] = ['11']
    client.post(f'/produkcja/sheet/{sid}/etapy', data=form)
    with app.app_context():
        sh = db.session.get(ProdSheet, sid)
        assert sh.columns[10] == 'KOLOR' and sh.mapping == {'zo': 8, 'product': 4, 'color': 10}
        assert [(s.label, s.col_index) for s in ProdStageColumn.query.filter_by(sheet_id=sid)] == [('GIĘCIE', 11)]
        assert ProdRow.query.filter_by(sheet_id=sid, zo='ZO-T5').count() == 1

    # ponowne wczytanie zachowuje nazwy, role i etap
    _import(client, _make_headerless_xlsx())
    with app.app_context():
        sid2 = _sheet_id(app, 'PROFIT')
        sh = db.session.get(ProdSheet, sid2)
        assert sh.columns[10] == 'KOLOR' and sh.columns[11] == 'GIĘCIE'
        assert sh.mapping == {'zo': 8, 'product': 4, 'color': 10}
        assert [(s.label, s.col_index) for s in ProdStageColumn.query.filter_by(sheet_id=sid2)] == [('GIĘCIE', 11)]
