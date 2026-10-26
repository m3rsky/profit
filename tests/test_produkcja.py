"""Testy modulu Lista produkcyjna (import Excela, edycja, eksport, listy kontrolne)."""
import io
import json
from datetime import datetime, timedelta

import pytest
from openpyxl import Workbook, load_workbook

from app import app as flask_app, db
from models import (User, Report, ReportItem, Alert, ProdFile, ProdSheet, ProdRow,
                    ProdSheetAccess, ProdEmployee, ProdEmployeeDept, ProdStageColumn,
                    ProdEntry, ProdSetting, ProdUserView, RolePermission)
from test_app import login, logout, _csrf

PRODUCT = 'Kontrola ZO API'   # pasuje do szablonu z conftest (dopasowanie po tokenach)
TODAY = datetime.combine(datetime.now().date(), datetime.min.time())   # import bierze tylko okno dat wokół dziś


def _make_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = 'PSH'
    ws.append(['ZAM', 'ROZP.', 'CZAS REALIZACJI', 'TERMIN REALIZACJI', 'NAZWA TOW.', 'ILOŚĆ',
               'ODBIORCA', 'NUMER ZO', 'KOLOR', 'SPAW.', 'KJ'])
    ws.append([TODAY, TODAY + timedelta(days=1), 3, '=WORKDAY(A2,C2)', PRODUCT, 2,
               'ELEKTRO TEST', 'ZO-T1', 'RAL 7035', 'MK', None])
    ws.append([TODAY + timedelta(days=1), TODAY + timedelta(days=2), 5, '=WORKDAY(A3,C3)', 'PSH 862', 1,
               'KLIENT DRUGI', 'ZO-T2', 'RAL 7035', None, None])
    ws2 = wb.create_sheet('SP')
    ws2.append(['zam', 'roz', 'czas realiz.', 'termin realizacji', 'NAZWA TOW.', 'ILOŚĆ', 'Z',
                'ODBIORCA', 'NUMER ZO'])
    ws2.append([TODAY, None, 2, None, 'SP 400X750', 1, None, 'SP KLIENT', 'ZO-S1'])
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
        ProdUserView.query.delete()
        ProdRow.query.delete()
        ProdSheetAccess.query.delete()
        ProdSheet.query.delete()
        ProdFile.query.delete()
        User.query.filter(User.username.in_(['planista', 'stanowisko'])).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture
def planner(app):
    with app.app_context():
        u = User(username='planista', email='planista@test.pl', role='technolog')
        u.set_password('Plan1234!')
        db.session.add(u)
        # technolog dostaje w macierzy dostęp do Listy produkcyjnej (domyślnie ma tylko Dashboard)
        db.session.add(RolePermission(role='technolog', module='lista_produkcyjna', allowed=True))
        db.session.commit()
    yield 'planista'
    with app.app_context():
        RolePermission.query.filter_by(role='technolog').delete()
        db.session.commit()


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
        assert json.loads(row.data)[0] == TODAY.strftime('%Y-%m-%d')
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
    # zmiana czasu realizacji przelicza termin (4 dni robocze od daty zamówienia)
    r = client.post(f'/produkcja/row/{rid}/cell', json={'col': 2, 'value': '4'}, headers=h)
    from produkcja import excel_io
    expected = datetime.strptime(excel_io.add_workdays(TODAY.strftime('%Y-%m-%d'), 4), '%Y-%m-%d')
    assert r.get_json()['cells']['3'] == expected.strftime('%d.%m.%Y')
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
    ws.append([TODAY, '26-06', None, None, PRODUCT, 2, None, 'ELEKTRO TEST', 'ZO-T5', None, 'RAL 7035', 'x'])
    ws.append([TODAY, '29-06', None, None, 'PSH 862', 1, None, 'KLIENT', 'ZO-T6', None, 'RAL 9003', 'x'])
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


def test_row_date_uses_due_then_order_date():
    from produkcja import excel_io
    m = {'order_date': 0, 'due': 1}
    assert str(excel_io.row_date(['2026-10-01', '2026-10-09'], m)) == '2026-10-09'
    assert str(excel_io.row_date(['2026-10-01 08:00', None], m)) == '2026-10-01'
    assert excel_io.row_date(['23-06', None], m) is None
    assert excel_io.row_date([], {}) is None


def test_hidden_columns_are_per_user_and_kept(app, client, planner):
    _admin(client)
    _import(client)
    sid = _sheet_id(app, 'PSH')
    with app.app_context():
        uid = User.query.filter_by(username='planista').first().id
    client.post('/produkcja/access', data={'access': [f'{uid}:{sid}'], '_csrf_token': _csrf(client)})
    # administrator ukrywa kolumnę KOLOR (klik w nagłówek)
    hdr = {'X-CSRF-Token': _csrf(client)}
    assert client.post(f'/produkcja/sheet/{sid}/kolumna', json={'col': 8, 'visible': False},
                       headers=hdr).get_json()['ok']
    html = client.get(f'/produkcja/sheet/{sid}').get_data(as_text=True)
    assert 'RAL 7035' not in html and 'ELEKTRO TEST' in html
    assert 'Ukryte kolumny (1)' in html
    rid = _row_id(app, 'PSH', 'ZO-T1')
    with app.app_context():
        assert json.loads(db.session.get(ProdRow, rid).data)[8] == 'RAL 7035'   # dane zostają
    assert client.post(f'/produkcja/sheet/{sid}/kolumna', json={'col': 99, 'visible': False},
                       headers=hdr).status_code == 400
    # kolumny aplikacji (Wiersz, Listy QA) też można ukryć
    for k in ('row', 'qa'):
        client.post(f'/produkcja/sheet/{sid}/kolumna', json={'col': k, 'visible': False}, headers=hdr)
    html = client.get(f'/produkcja/sheet/{sid}').get_data(as_text=True)
    assert 'data-c="row"' not in html and 'data-c="qa"' not in html and 'Ukryte kolumny (3)' in html
    client.post(f'/produkcja/sheet/{sid}/kolumna', json={'col': 'row', 'visible': True}, headers=hdr)
    assert 'data-c="row"' in client.get(f'/produkcja/sheet/{sid}').get_data(as_text=True)
    # planista ma własny widok: nadal widzi wszystko
    logout(client)
    login(client, planner, 'Plan1234!')
    assert 'RAL 7035' in client.get(f'/produkcja/sheet/{sid}').get_data(as_text=True)
    # ukrycie administratora przeżywa ponowne wczytanie pliku, a "pokaż wszystkie" je zdejmuje
    logout(client)
    _admin(client)
    _import(client)
    sid = _sheet_id(app, 'PSH')
    assert 'RAL 7035' not in client.get(f'/produkcja/sheet/{sid}').get_data(as_text=True)
    client.post(f'/produkcja/sheet/{sid}/kolumna', json={'all': True}, headers={'X-CSRF-Token': _csrf(client)})
    assert 'RAL 7035' in client.get(f'/produkcja/sheet/{sid}').get_data(as_text=True)


def test_empty_columns_hidden_automatically(app, client):
    wb = Workbook()
    ws = wb.active
    ws.title = 'PSH'
    ws.append(['ZAM', 'CZAS REALIZACJI', 'NAZWA TOW.', 'ILOŚĆ', 'ODBIORCA', 'NUMER ZO', 'UWAGI', 'KJ', 'NOTATKA'])
    ws.append([TODAY, 3, PRODUCT, 2, 'ELEKTRO TEST', 'ZO-T1', None, None, 'jest'])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    _admin(client)
    _import(client, buf)
    sid = _sheet_id(app, 'PSH')
    html = client.get(f'/produkcja/sheet/{sid}').get_data(as_text=True)
    assert 'Puste kolumny ukryte (1)' in html
    assert '>UWAGI</th>' not in html          # pusta, ukryta automatycznie
    assert '>KJ</th>' in html                 # pusta, ale to etap (wpisuje się w aplikacji)
    assert '>NOTATKA</th>' in html            # ma wartość
    assert '>UWAGI</th>' in client.get(f'/produkcja/sheet/{sid}?puste=1').get_data(as_text=True)


def test_controllink_suggestions_and_product_matching():
    from produkcja import controllink as cl
    depts = {cl.plain(n): i for i, n in enumerate(
        ['Gięcie', 'Spawanie', 'Zgrzewanie', 'Czyszczenie', 'Mycie', 'Malarnia', 'Kompletacja', 'Składanie'], 1)}
    els = {cl.plain(n): n for n in ['Korpus', 'Drzwi', 'G/D', 'Boki']}
    s = lambda label: cl.suggest(label, depts, els)
    assert s('GIĘCIE OB.') == {'depts': [1], 'elements': ['Korpus', 'G/D', 'Boki'], 'final': False}
    assert s('GIĘCIE DR')['elements'] == ['Drzwi']
    assert s('SPAW. OB.') == {'depts': [2], 'elements': ['Korpus'], 'final': False}
    assert s('SPAW DRZW.')['depts'] == [2, 3] and s('DRZWI')['elements'] == ['Drzwi']
    assert s('SPAW.')['elements'] == []
    assert s('CZYSZ. OB.')['depts'] == [4] and s('MYJ.')['depts'] == [5] and s('MAL')['depts'] == [6]
    assert s('SZYK')['depts'] == [7] and s('SKŁADANIE')['depts'] == [8]
    assert s('KJ')['final'] and s('PAK.') is None and s('MASKI') is None
    assert cl.same_product('PSH 600X800X300 (P)', 'PSH-600-800-300 IP65')
    assert cl.same_product('PSH 700X1000X175/250(P)', 'PSH-700-1000-175/250 IP65')
    assert not cl.same_product('PSH 600X800X300', 'PSH-600-800-400 IP65')
    assert not cl.same_product('SPD 845X395', 'SP-D-545-345 IP30')


def test_list_shows_control_squares_and_filters(app, client):
    from models import (ProductionDepartment, ControlElement, ControlOrder, ControlRecord,
                        ControlRecordElement, ControlResult)
    _admin(client)
    _import(client)
    sid = _sheet_id(app, 'PSH')
    with app.app_context():
        uid = User.query.filter_by(username='admin').first().id
        sh = db.session.get(ProdSheet, sid)
        roles = {f'role_{i}': r for r, i in sh.mapping.items()}
        da, db_ = ProductionDepartment(name='T9 Dział A', order=901), ProductionDepartment(name='T9 Dział B', order=902)
        e1 = ControlElement(name='T9 Element')
        db.session.add_all([da, db_, e1])
        db.session.commit()
        da_id, db_id = da.id, db_.id
    try:
        client.post(f'/produkcja/sheet/{sid}/etapy', data={
            '_csrf_token': _csrf(client), 'ctl_form': '1', 'ctl_dept_9': [str(da_id), str(db_id)],
            'ctl_el_9': ['T9 Element'], 'ctl_final': '10', **roles})
        with app.app_context():
            o = ControlOrder(zo_number='ZO-T1', product_name=PRODUCT.upper() + '  ')
            db.session.add(o)
            db.session.flush()
            recs = []
            for dep, status in ((da_id, 'completed'), (db_id, 'in_progress')):
                rec = ControlRecord(zo_number='ZO-T1', user_id=uid, department_id=dep,
                                    control_order_id=o.id, status=status)
                rec.element_links.append(ControlRecordElement(name='T9 Element'))
                db.session.add(rec)
                recs.append(rec)
            db.session.commit()
            rec_b = recs[1].id
        url = f'/produkcja/sheet/{sid}'
        html = client.get(url).get_data(as_text=True)
        assert 'ctl-sq ctl-open' in html and '<b>1/2</b>' in html          # 1 z 2 działów zakończone
        with app.app_context():
            db.session.get(ControlRecord, rec_b).status = 'completed'
            db.session.commit()
        assert 'ctl-sq ctl-done' in client.get(url).get_data(as_text=True)
        with app.app_context():
            db.session.add(ControlResult(record_id=rec_b, title='x', result='NG'))
            db.session.commit()
        assert 'ctl-sq ctl-ng' in client.get(url).get_data(as_text=True)
        # filtry
        ng = client.get(url + '?typ=etapowa&kontrola=ng').get_data(as_text=True)
        assert 'ELEKTRO TEST' in ng and 'KLIENT DRUGI' not in ng
        none = client.get(url + '?typ=etapowa&kontrola=brak').get_data(as_text=True)
        assert 'KLIENT DRUGI' in none and 'ELEKTRO TEST' not in none
        # Kontrola Końcowa (KJ): listy z tej listy produkcyjnej
        rid = _row_id(app, 'PSH', 'ZO-T1')
        client.post(f'/produkcja/sheet/{sid}/checklists', data={'row_ids': [rid], '_csrf_token': _csrf(client)})
        kj = client.get(url + '?typ=koncowa&kontrola=otwarta').get_data(as_text=True)
        assert 'ELEKTRO TEST' in kj and 'KLIENT DRUGI' not in kj
        assert 'ctl-sq ctl-open' in kj and '<b>0/2</b>' in kj
    finally:
        with app.app_context():
            ControlResult.query.filter(ControlResult.title == 'x').delete()
            for rec in ControlRecord.query.filter_by(zo_number='ZO-T1').all():
                db.session.delete(rec)
            ControlOrder.query.filter_by(zo_number='ZO-T1').delete()
            ControlElement.query.filter_by(name='T9 Element').delete()
            ProductionDepartment.query.filter(ProductionDepartment.name.like('T9 %')).delete(synchronize_session=False)
            db.session.commit()


def test_whole_file_is_loaded_and_view_filters_by_date_range(app, client):
    wb = Workbook()
    ws = wb.active
    ws.title = 'PSH'
    ws.append(['ZAM', 'CZAS REALIZACJI', 'TERMIN REALIZACJI', 'NAZWA TOW.', 'ILOŚĆ', 'ODBIORCA', 'NUMER ZO'])
    for n in (-40, -14, 0, 14, 15, 60):
        ws.append([TODAY + timedelta(days=n), 3, TODAY + timedelta(days=n), f'PROD{n}', 1, 'K', f'ZO{n}'])
    ws.append([None, None, None, 'BEZ DATY', 1, 'K', 'ZOX'])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    _admin(client)
    _import(client, buf)
    sid = _sheet_id(app, 'PSH')
    with app.app_context():
        assert ProdRow.query.filter_by(sheet_id=sid).count() == 7      # wczytany cały plik
    url = f'/produkcja/sheet/{sid}'
    html = client.get(url).get_data(as_text=True)                       # domyślnie -14 .. +14
    for n in (-14, 0, 14):
        assert f'PROD{n}<' in html
    for n in (-40, 15, 60):
        assert f'PROD{n}<' not in html
    assert 'BEZ DATY' not in html
    day = lambda n: (TODAY + timedelta(days=n)).strftime('%Y-%m-%d')
    html = client.get(f'{url}?od={day(10)}&do={day(20)}').get_data(as_text=True)
    assert 'PROD14<' in html and 'PROD15<' in html and 'PROD0<' not in html
    html = client.get(f'{url}?od=&do=').get_data(as_text=True)         # wszystkie daty
    assert all(f'PROD{n}<' in html for n in (-40, 60)) and 'BEZ DATY' in html
    html = client.get(f'{url}?od={day(20)}&do=').get_data(as_text=True)  # tylko dolna granica
    assert 'PROD60<' in html and 'PROD15<' not in html
    # edycja terminu przelicza datę wiersza
    rid = _row_id(app, 'PSH', 'ZO0')
    client.post(f'/produkcja/row/{rid}/cell', json={'col': 2, 'value': day(100)},
                headers={'X-CSRF-Token': _csrf(client)})
    assert 'PROD0<' not in client.get(url).get_data(as_text=True)


def test_reimport_works_when_source_file_is_missing_on_disk(app, client):
    import os
    _admin(client)
    _import(client)
    with app.app_context():
        src = ProdFile.query.filter(ProdFile.kind == 'import').first()
        os.remove(os.path.join(app.config['PRODUKCJA_FOLDER'], src.stored_name))
    resp = _import(client)
    assert 'Wczytano 2 arkuszy' in resp.get_data(as_text=True)         # bez błędu kopii zapasowej
    with app.app_context():
        backups = ProdFile.query.filter_by(kind='backup').all()
        assert {b.original_name for b in backups} == {'PSH_backup.csv', 'SP_backup.csv'}   # CSV z bazy
