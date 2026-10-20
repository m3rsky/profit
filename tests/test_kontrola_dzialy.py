"""Testy kontroli po działach w Kontroli zamówień: lista zleceń ze skanu QR, wybór działu, punkty działu,
osoba odpowiedzialna, historia i ponowna kontrola (bez cyklu Wykonane i sprawdzającej)."""
import pytest

from app import db
from models import (User, Alert, QARReport, ControlPoint, ControlRecord, ControlResult, ControlOrder,
                    ProductionDepartment, DepartmentEmployee)
from test_app import login, _csrf

QR = {'p': 'Szafa PSH', 'c': 'Klient X', 'o': 'ZO/900/2026', 'q': 3}


def _wipe():
    ControlResult.query.delete()
    ControlRecord.query.update({'employee_id': None})
    ControlRecord.query.delete()
    ControlOrder.query.delete()
    QARReport.query.filter(QARReport.zo_number.like('ZO/90%')).update({'employee_id': None},
                                                                     synchronize_session=False)
    QARReport.query.filter(QARReport.zo_number.like('ZO/90%')).delete(synchronize_session=False)
    ControlPoint.query.delete()
    Alert.query.filter(Alert.message.like('ZO ZO/90%')).delete(synchronize_session=False)
    DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T9 %')).delete(synchronize_session=False)
    ProductionDepartment.query.filter(ProductionDepartment.name.like('T9 %')).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture(autouse=True)
def _clean(app):
    with app.app_context():
        _wipe()
    yield
    with app.app_context():
        _wipe()


@pytest.fixture
def operator_user(app):
    with app.app_context():
        if not User.query.filter_by(username='operator9').first():
            u = User(username='operator9', email='operator9@test.pl', role='operator')
            u.set_password('Operator123!')
            db.session.add(u)
            db.session.commit()
    return 'operator9'


@pytest.fixture
def setup(app):
    """Działy Spawalnia i Montaż z punktami, dział Pusty bez aktywnych punktów, jeden punkt ogólny i pracownicy."""
    with app.app_context():
        sp = ProductionDepartment(name='T9 Spawalnia', order=900)
        mo = ProductionDepartment(name='T9 Montaż', order=901)
        pu = ProductionDepartment(name='T9 Pusty', order=902)
        db.session.add_all([sp, mo, pu])
        db.session.flush()
        db.session.add_all([
            ControlPoint(title='Ogólny wygląd', order=1),
            ControlPoint(title='Spoiny', order=2, department_id=sp.id),
            ControlPoint(title='Wymiary spoiny', order=3, department_id=sp.id),
            ControlPoint(title='Okablowanie', order=4, department_id=mo.id),
            ControlPoint(title='Wyłączony', order=5, department_id=pu.id, is_active=False)])
        e = {'jan': DepartmentEmployee(department_id=sp.id, name='T9 Jan Spawacz'),
             'ola': DepartmentEmployee(department_id=mo.id, name='T9 Ola Monterka'),
             'off': DepartmentEmployee(department_id=sp.id, name='T9 Nieaktywny', is_active=False)}
        db.session.add_all(e.values())
        db.session.commit()
        return {'sp': sp.id, 'mo': mo.id, 'pu': pu.id, **{k: v.id for k, v in e.items()}}


def _scan(client, qr=QR):
    return client.post('/kontrola/from-qr', json=qr, headers={'X-CSRF-Token': _csrf(client)})


def _order(app, qr=QR):
    with app.app_context():
        for o in ControlOrder.query.filter_by(zo_number=qr['o']).all():
            if o.product_name == qr['p']:
                return o.id
    return None


def _start(client, order_id, dept):
    return client.post(f'/kontrola/zlecenie/{order_id}/start', data={'department_id': dept if dept else ''},
                       headers={'X-CSRF-Token': _csrf(client)})


def _begin(client, app, dept, qr=QR):
    """Skan zlecenia i rozpoczęcie kontroli działu. Zwraca id kontroli."""
    _scan(client, qr)
    oid = _order(app, qr)
    _start(client, oid, dept)
    with app.app_context():
        rec = (ControlRecord.query.filter_by(control_order_id=oid, department_id=dept)
               .order_by(ControlRecord.id.desc()).first())
        return rec.id


def _rate(client, app, rid, results, employee=None, action='complete'):
    with app.app_context():
        ids = [r.id for r in db.session.get(ControlRecord, rid).results]
    data = {'action': action}
    for res_id, val in zip(ids, results):
        data[f'result_{res_id}'] = val
    if employee is not None:
        data['employee_id'] = str(employee)
    return client.post(f'/kontrola/{rid}', data=data, headers={'X-CSRF-Token': _csrf(client)})


class TestPointsDepartments:
    def test_add_save_and_filter_by_department(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        h = {'X-CSRF-Token': _csrf(client)}
        client.post('/kontrola/points/add', data={'title': 'T9 Nowy', 'department_id': str(setup['mo'])}, headers=h)
        client.post('/kontrola/points/add', data={'title': 'T9 Bez', 'department_id': ''}, headers=h)
        client.post('/kontrola/points/add', data={'title': 'T9 Zły', 'department_id': '999999'}, headers=h)
        with app.app_context():
            pts = {p.title: p.department_id for p in ControlPoint.query.filter(ControlPoint.title.like('T9 %'))}
            assert pts == {'T9 Nowy': setup['mo'], 'T9 Bez': None, 'T9 Zły': None}
            pid = ControlPoint.query.filter_by(title='T9 Bez').one().id
        client.post(f'/kontrola/points/{pid}/save', data={'title': 'T9 Bez', 'is_active': '1',
                                                          'department_id': str(setup['sp'])}, headers=h)
        with app.app_context():
            assert db.session.get(ControlPoint, pid).department_id == setup['sp']
        page = client.get(f'/kontrola/points?dzial={setup["mo"]}').get_data(as_text=True)
        assert 'Okablowanie' in page and 'T9 Nowy' in page and 'Spoiny' not in page
        only_general = client.get('/kontrola/points?dzial=-1').get_data(as_text=True)
        assert 'Ogólny wygląd' in only_general and 'Spoiny' not in only_general
        assert 'Spoiny' in client.get('/kontrola/points').get_data(as_text=True)

    def test_save_without_department_field_keeps_it(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        with app.app_context():
            pid = ControlPoint.query.filter_by(title='Spoiny').one().id
        client.post(f'/kontrola/points/{pid}/save', data={'title': 'Spoiny 2', 'is_active': '1'},
                    headers={'X-CSRF-Token': _csrf(client)})
        with app.app_context():
            assert db.session.get(ControlPoint, pid).department_id == setup['sp']


class TestScanAndOrder:
    def test_scan_without_department_points_works_as_before(self, client, app):
        with app.app_context():
            db.session.add(ControlPoint(title='Ogólny', order=1))
            db.session.commit()
        login(client, 'oper', 'Oper1234!')
        body = _scan(client).get_json()
        assert body['ok'] and '/kontrola/zlecenie' not in body['redirect']
        with app.app_context():
            assert ControlOrder.query.count() == 0
            rec = ControlRecord.query.filter_by(zo_number='ZO/900/2026').one()
            assert rec.department_id is None and rec.control_order_id is None

    def test_scan_adds_order_to_list_and_rescan_opens_it(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        body = _scan(client).get_json()
        assert body['ok'] and body['redirect'].startswith('/kontrola/') and 'zlecenie' not in body['redirect']
        assert 'Dodano do listy do skontrolowania' in body['msg']
        listing = client.get(body['redirect'], follow_redirects=True).get_data(as_text=True)
        assert 'Dodano do listy do skontrolowania' in listing and 'Do skontrolowania' in listing
        assert 'Szafa PSH' in listing and 'ZO/900/2026' in listing
        again = _scan(client).get_json()                                  # ponowny skan otwiera zlecenie
        assert '/kontrola/zlecenie/' in again['redirect'] and 'już na liście' in again['msg']
        _scan(client, {**QR, 'p': 'Inna szafa'})
        with app.app_context():
            assert ControlOrder.query.count() == 2 and ControlRecord.query.count() == 0
            o = ControlOrder.query.filter_by(product_name='Szafa PSH').one()
            assert (o.zo_number, o.client, o.quantity) == ('ZO/900/2026', 'Klient X', 3)
        page = client.get(again['redirect']).get_data(as_text=True)
        assert 'T9 Spawalnia' in page and 'T9 Montaż' in page and 'T9 Pusty' not in page
        assert 'Szafa PSH' in page and 'Brak kontroli' in page
        lst = client.get('/kontrola/').get_data(as_text=True)
        assert 'Kontrole' in lst and 'Nowa kontrola' in lst and 'ZO/900/2026' in lst

    def test_order_pages_require_controller(self, client, app, setup, operator_user):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        oid = _order(app)
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        assert client.get(f'/kontrola/zlecenie/{oid}').status_code == 403
        assert _start(client, oid, setup['sp']).status_code == 403
        assert client.post(f'/kontrola/zlecenie/{oid}/usun',
                           headers={'X-CSRF-Token': _csrf(client)}).status_code == 403


class TestDepartmentControl:
    def test_choose_department_expands_its_points_plus_general(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['sp'])
        with app.app_context():
            rec = db.session.get(ControlRecord, rid)
            assert [r.title for r in rec.results] == ['Ogólny wygląd', 'Spoiny', 'Wymiary spoiny']
            assert (rec.round, rec.quantity, rec.client, rec.status) == (1, 3, 'Klient X', 'in_progress')
            assert rec.control_order.zo_number == 'ZO/900/2026'
        mo = _begin(client, app, setup['mo'])
        with app.app_context():
            assert [r.title for r in db.session.get(ControlRecord, mo).results] == ['Ogólny wygląd', 'Okablowanie']

    def test_invalid_department_rejected_and_in_progress_reopened(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        oid = _order(app)
        for bad in ('', str(setup['pu']), '999999', 'x'):
            res = client.post(f'/kontrola/zlecenie/{oid}/start', data={'department_id': bad},
                              headers={'X-CSRF-Token': _csrf(client)}, follow_redirects=True)
            assert 'Wybierz dział kontroli' in res.get_data(as_text=True)
        with app.app_context():
            assert ControlRecord.query.count() == 0
        rid = _begin(client, app, setup['sp'])
        again = _start(client, oid, setup['sp'])
        assert again.headers['Location'].endswith(f'/kontrola/{rid}')
        with app.app_context():
            assert ControlRecord.query.count() == 1

    def test_employee_list_limited_to_department(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['sp'])
        page = client.get(f'/kontrola/{rid}').get_data(as_text=True)
        assert 'T9 Jan Spawacz' in page and 'T9 Ola Monterka' not in page and 'T9 Nieaktywny' not in page
        assert 'optgroup' not in page and 'Dział: T9 Spawalnia' in page
        _rate(client, app, rid, [], employee=setup['ola'], action='save')
        with app.app_context():
            assert db.session.get(ControlRecord, rid).employee_id is None
        _rate(client, app, rid, [], employee=setup['jan'], action='save')
        with app.app_context():
            assert db.session.get(ControlRecord, rid).employee_id == setup['jan']

    def test_complete_has_no_cycle_and_returns_to_order(self, client, app, setup, operator_user):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['sp'])
        res = _rate(client, app, rid, ['OK', 'OK', 'OK'], employee=setup['jan'])
        oid = _order(app)
        assert res.status_code == 302 and res.headers['Location'].endswith(f'/kontrola/zlecenie/{oid}')
        with app.app_context():
            rec = db.session.get(ControlRecord, rid)
            assert rec.status == 'completed' and rec.state == 'closed' and rec.followup is None
            assert rec.result == 'OK'
            assert Alert.query.filter(Alert.message.like('ZO ZO/900%')).count() == 0
        page = client.get(f'/kontrola/zlecenie/{oid}').get_data(as_text=True)
        assert 'T9 Spawalnia' in page and 'T9 Jan Spawacz' in page and 'zgodna' in page and 'aktualna' in page
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        assert client.post(f'/kontrola/{rid}/done', headers={'X-CSRF-Token': _csrf(client)}).status_code == 302
        with app.app_context():
            assert db.session.get(ControlRecord, rid).done_at is None
            assert ControlRecord.query.filter_by(round=2).count() == 0
        view = client.get(f'/kontrola/{rid}').get_data(as_text=True)
        assert 'Dział: T9 Spawalnia' in view and 'Wykonawca: T9 Jan Spawacz' in view
        assert client.post(f'/kontrola/{rid}', data={'action': 'save'},
                           headers={'X-CSRF-Token': _csrf(client)}).status_code == 403

    def test_recheck_of_same_department_keeps_history(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        first = _begin(client, app, setup['sp'])
        _rate(client, app, first, ['OK', 'NG', 'OK'], employee=setup['jan'])
        oid = _order(app)
        page = client.get(f'/kontrola/zlecenie/{oid}').get_data(as_text=True)
        assert 'Ponowna kontrola' in page and 'niezgodna (1)' in page
        res = _start(client, oid, setup['sp'])                               # ponowna kontrola
        second = int(res.headers['Location'].rsplit('/', 1)[1])
        assert second != first
        _rate(client, app, second, ['OK', 'OK', 'OK'])
        with app.app_context():
            ctrls = ControlRecord.query.filter_by(control_order_id=oid).order_by(ControlRecord.id).all()
            assert [(c.id == first, c.result) for c in ctrls] == [(True, 'NG'), (False, 'OK')]
        page = client.get(f'/kontrola/zlecenie/{oid}').get_data(as_text=True)
        assert page.count('aktualna') == 1                                  # tylko najnowsza zakończona
        assert 'niezgodna (1)' in page and 'zgodna' in page                 # starsza zostaje w historii
        other = _begin(client, app, setup['mo'])                            # inny dział
        _rate(client, app, other, ['OK', 'OK'])
        with app.app_context():
            assert ControlRecord.query.filter_by(control_order_id=oid).count() == 3

    def test_operator_cannot_open_unfinished_department_control(self, client, app, setup, operator_user):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['mo'])
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        assert client.get(f'/kontrola/{rid}').status_code == 403
        page = client.get('/kontrola/?s=all').get_data(as_text=True)
        assert 'ZO/900/2026' not in page                                    # zlecenie bez zakończonych kontroli

    def test_operator_sees_completed_controls_read_only(self, client, app, setup, operator_user):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['sp'])
        _rate(client, app, rid, ['OK', 'OK', 'OK'], employee=setup['jan'])
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        assert 'ZO/900/2026' not in client.get('/kontrola/').get_data(as_text=True)   # domyślnie Do wykonania
        page = client.get('/kontrola/?s=').get_data(as_text=True)
        assert 'ZO/900/2026' in page and 'T9 Spawalnia' in page and 'T9 Jan Spawacz' in page
        assert 'Nowa kontrola' not in page

    def test_admin_can_reopen_and_delete_order(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['mo'])
        _rate(client, app, rid, ['OK', 'OK'])
        oid = _order(app)
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        h = {'X-CSRF-Token': _csrf(client)}
        client.post(f'/kontrola/{rid}/reopen', headers=h)
        with app.app_context():
            assert db.session.get(ControlRecord, rid).status == 'in_progress'
        client.post(f'/kontrola/zlecenie/{oid}/usun', headers=h)
        with app.app_context():
            assert ControlOrder.query.count() == 0 and ControlRecord.query.count() == 0

    def test_ng_creates_qar_with_department_and_performer(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['sp'])
        _rate(client, app, rid, ['OK', 'NG', 'OK'], employee=setup['jan'])
        with app.app_context():
            qar = QARReport.query.filter_by(zo_number='ZO/900/2026').one()
            assert qar.employee_id == setup['jan']
            assert 'Dział kontroli: T9 Spawalnia' in qar.description
            assert 'Wykonawca: T9 Jan Spawacz (T9 Spawalnia)' in qar.description

    def test_list_filters_search_and_department(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        sp = _begin(client, app, setup['sp'])
        _rate(client, app, sp, ['OK', 'OK', 'OK'], employee=setup['jan'])
        other = {**QR, 'o': 'ZO/901/2026', 'p': 'Inna szafa'}
        mo = _begin(client, app, setup['mo'], other)
        _rate(client, app, mo, ['OK', 'OK'])
        both = client.get('/kontrola/').get_data(as_text=True)
        assert 'ZO/900/2026' in both and 'ZO/901/2026' in both
        by_dept = client.get(f'/kontrola/?dzial={setup["sp"]}').get_data(as_text=True)
        assert 'ZO/900/2026' in by_dept and 'ZO/901/2026' not in by_dept
        by_dept2 = client.get(f'/kontrola/?dzial={setup["mo"]}').get_data(as_text=True)
        assert 'ZO/901/2026' in by_dept2 and 'ZO/900/2026' not in by_dept2
        by_name = client.get('/kontrola/?zo=spawacz').get_data(as_text=True)
        assert 'ZO/900/2026' in by_name and 'ZO/901/2026' not in by_name
        assert 'ZO/901/2026' in client.get('/kontrola/?zo=inna').get_data(as_text=True)

    def test_legacy_general_control_keeps_cycle_alongside_orders(self, client, app, setup, operator_user):
        with app.app_context():
            legacy = ControlRecord(zo_number='ZO/902/2026', product_name='Stara', round=1,
                                   user_id=User.query.filter_by(username='oper').one().id)
            db.session.add(legacy)
            db.session.flush()
            db.session.add(ControlResult(record_id=legacy.id, position=1, title='Ogólny wygląd'))
            db.session.commit()
            lid = legacy.id
        login(client, 'oper', 'Oper1234!')
        _rate(client, app, lid, ['OK'])
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        client.post(f'/kontrola/{lid}/done', headers={'X-CSRF-Token': _csrf(client)})
        with app.app_context():
            assert db.session.get(ControlRecord, lid).state == 'recheck'
            assert ControlRecord.query.filter_by(round=2).count() == 1
        assert 'ZO/902/2026' in client.get('/kontrola/?s=all').get_data(as_text=True)


class TestCreateAndDelete:
    def test_manual_order_creation_and_duplicates(self, client, app, setup, operator_user):
        login(client, 'oper', 'Oper1234!')
        h = {'X-CSRF-Token': _csrf(client)}
        page = client.get('/kontrola/').get_data(as_text=True)
        assert 'Dodaj zlecenie ręcznie' in page
        res = client.post('/kontrola/zlecenie/nowe', data={'zo': 'ZO/903/2026', 'p': 'Szafa Reczna', 'c': 'Klient Y',
                                                          'q': '4'}, headers=h)
        assert res.status_code == 302 and '/kontrola/zlecenie/' in res.headers['Location']
        again = client.post('/kontrola/zlecenie/nowe', data={'zo': 'zo/903/2026'.upper(), 'p': 'szafa  reczna', 'q': '4'},
                            headers=h)
        assert again.headers['Location'] == res.headers['Location']
        with app.app_context():
            assert ControlOrder.query.filter_by(zo_number='ZO/903/2026').count() == 1
            o = ControlOrder.query.filter_by(zo_number='ZO/903/2026').one()
            assert (o.client, o.quantity) == ('Klient Y', 4)
        for bad in ({'zo': '', 'p': 'X'}, {'zo': 'ZO/9', 'p': ''}):
            r = client.post('/kontrola/zlecenie/nowe', data=bad, headers=h, follow_redirects=True)
            assert 'Podaj' in r.get_data(as_text=True)
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        assert client.post('/kontrola/zlecenie/nowe', data={'zo': 'ZO/904', 'p': 'X'},
                           headers={'X-CSRF-Token': _csrf(client)}).status_code == 403
        assert 'Dodaj zlecenie ręcznie' not in client.get('/kontrola/').get_data(as_text=True)

    def test_admin_deletes_single_and_bulk_orders_with_controls(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        r1 = _begin(client, app, setup['sp'])
        _rate(client, app, r1, ['OK', 'NG', 'OK'], employee=setup['jan'])
        q2 = {**QR, 'o': 'ZO/905/2026', 'p': 'Druga'}
        q3 = {**QR, 'o': 'ZO/906/2026', 'p': 'Trzecia'}
        _begin(client, app, setup['mo'], q2)
        _begin(client, app, setup['mo'], q3)
        o1, o2, o3 = _order(app), _order(app, q2), _order(app, q3)
        # kontroler nie usuwa
        h = {'X-CSRF-Token': _csrf(client)}
        assert client.post(f'/kontrola/zlecenie/{o1}/usun', headers=h).status_code == 403
        assert client.post('/kontrola/zlecenie/usun-zaznaczone', data={'order_ids': [o1]}, headers=h).status_code == 403
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        page = client.get('/kontrola/').get_data(as_text=True)
        assert 'Usuń zaznaczone' in page and f'name="order_ids" value="{o1}"' in page and 'title="Usuń zlecenie"' in page
        h = {'X-CSRF-Token': _csrf(client)}
        client.post(f'/kontrola/zlecenie/{o1}/usun', headers=h)
        with app.app_context():
            assert ControlOrder.query.count() == 2 and ControlRecord.query.filter_by(control_order_id=o1).count() == 0
            assert QARReport.query.filter_by(zo_number='ZO/900/2026').count() == 1       # QAR zostaje
        res = client.post('/kontrola/zlecenie/usun-zaznaczone', data={'order_ids': [o2, o3]}, headers=h,
                          follow_redirects=True)
        assert 'Usunięto zlecenia z listy: 2' in res.get_data(as_text=True)
        with app.app_context():
            assert ControlOrder.query.count() == 0 and ControlRecord.query.count() == 0
        empty = client.post('/kontrola/zlecenie/usun-zaznaczone', data={}, headers=h, follow_redirects=True)
        assert 'Nie zaznaczono' in empty.get_data(as_text=True)

    def test_admin_deletes_single_control_from_history(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['sp'])
        _rate(client, app, rid, ['OK', 'OK', 'OK'])
        oid = _order(app)
        h = {'X-CSRF-Token': _csrf(client)}
        assert client.post(f'/kontrola/{rid}/delete', headers=h).status_code == 403
        assert 'title="Usuń kontrolę"' not in client.get(f'/kontrola/zlecenie/{oid}').get_data(as_text=True)
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        assert 'title="Usuń kontrolę"' in client.get(f'/kontrola/zlecenie/{oid}').get_data(as_text=True)
        res = client.post(f'/kontrola/{rid}/delete', headers={'X-CSRF-Token': _csrf(client)})
        assert res.status_code == 302 and res.headers['Location'].endswith(f'/kontrola/zlecenie/{oid}')
        with app.app_context():
            assert ControlRecord.query.count() == 0 and ControlOrder.query.count() == 1


class TestListLayout:
    def _mixed(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        done = _begin(client, app, setup['sp'])
        _rate(client, app, done, ['OK', 'OK', 'OK'], employee=setup['jan'])
        _begin(client, app, setup['mo'], {**QR, 'o': 'ZO/910/2026', 'p': 'W trakcie'})
        _scan(client, {**QR, 'o': 'ZO/911/2026', 'p': 'Pusta'})
        client.get('/kontrola/')                                            # odbiera komunikaty ze skanów
        with app.app_context():
            legacy = ControlRecord(zo_number='ZO/912/2026', product_name='Stara', round=1,
                                   user_id=User.query.filter_by(username='oper').one().id)
            db.session.add(legacy)
            db.session.flush()
            db.session.add(ControlResult(record_id=legacy.id, position=1, title='Ogólny wygląd'))
            db.session.commit()
            return legacy.id

    def test_one_table_with_orders_and_legacy_and_report_style_layout(self, client, app, setup):
        self._mixed(client, app, setup)
        page = client.get('/kontrola/').get_data(as_text=True)
        for text in ('ZO/900/2026', 'ZO/910/2026', 'ZO/911/2026', 'ZO/912/2026'):
            assert text in page
        assert 'data-table tbl-cards' in page and 'filter-bar' in page and 'class="pagination"' in page
        assert 'bulk-bar' not in page                                               # zaznaczanie tylko admin
        assert 'ogólna' in page and 'Do skontrolowania' in page
        assert '<strong>Szafa PSH</strong></a>' in page and '<div class="kz-zo-small">ZO ZO/900/2026</div>' in page
        assert 'Produkt / ZO' in page
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        admin_page = client.get('/kontrola/').get_data(as_text=True)
        assert 'bulk-bar' in admin_page and 'id="select-all"' in admin_page and 'class="row-check"' in admin_page

    def test_status_department_and_date_filters(self, client, app, setup):
        self._mixed(client, app, setup)
        done = client.get('/kontrola/?s=completed').get_data(as_text=True)
        assert 'ZO/900/2026' in done and 'ZO/910/2026' not in done and 'ZO/911/2026' not in done
        prog = client.get('/kontrola/?s=in_progress').get_data(as_text=True)
        assert 'ZO/910/2026' in prog and 'ZO/900/2026' not in prog and 'ZO/912/2026' in prog
        new = client.get('/kontrola/?s=new').get_data(as_text=True)
        assert 'ZO/911/2026' in new and 'ZO/900/2026' not in new
        assert 'ZO/900/2026' in client.get('/kontrola/?date_from=2000-01-01').get_data(as_text=True)
        assert 'ZO/900/2026' not in client.get('/kontrola/?date_from=2999-01-01').get_data(as_text=True)
        assert 'ZO/900/2026' not in client.get('/kontrola/?date_to=2000-01-01').get_data(as_text=True)
        assert 'ZO/900/2026' in client.get('/kontrola/?date_from=zle&date_to=zle').get_data(as_text=True)

    def test_pagination(self, client, app, setup):
        self._mixed(client, app, setup)
        page1 = client.get('/kontrola/?per_page=10').get_data(as_text=True)
        assert '1 / 1 (4 pozycji)' in page1
        with app.app_context():
            for i in range(12):
                db.session.add(ControlOrder(zo_number=f'ZO/920/{i:02d}', product_name='Masowe', quantity=1))
            db.session.commit()
        p1 = client.get('/kontrola/?per_page=10').get_data(as_text=True)
        p2 = client.get('/kontrola/?per_page=10&page=2').get_data(as_text=True)
        assert '1 / 2 (16 pozycji)' in p1 and '2 / 2 (16 pozycji)' in p2
        assert 'Następna' in p1 and 'Poprzednia' in p2
        assert client.get('/kontrola/?per_page=10&page=99').status_code == 200        # strona poza zakresem
        assert client.get('/kontrola/?per_page=7').status_code == 200                  # niedozwolona wartość

    def test_admin_bulk_deletes_orders_and_legacy_together(self, client, app, setup):
        legacy = self._mixed(client, app, setup)
        o = _order(app)
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        res = client.post('/kontrola/zlecenie/usun-zaznaczone', data={'order_ids': [o], 'record_ids': [legacy]},
                          headers={'X-CSRF-Token': _csrf(client)}, follow_redirects=True)
        assert 'Usunięto zlecenia z listy: 2' in res.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(ControlRecord, legacy) is None
            assert ControlOrder.query.filter_by(zo_number='ZO/900/2026').count() == 0
        page = client.get('/kontrola/').get_data(as_text=True)
        assert 'data-post-url' in page                                              # usuwanie wiersza poza formularzem

    def test_operator_default_view_and_links(self, client, app, setup, operator_user):
        self._mixed(client, app, setup)
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        page = client.get('/kontrola/?s=completed').get_data(as_text=True)
        assert 'ZO/900/2026' in page and 'ZO/910/2026' not in page
        assert 'ZO/911/2026' not in page and 'Usuń' not in page and 'Dodaj zlecenie ręcznie' not in page
        opts = client.get('/kontrola/').get_data(as_text=True)
        assert 'value="in_progress"' not in opts and 'value="new"' not in opts


class TestScanButtonLabel:
    def _lookup(self, client, qr=QR):
        return client.post('/kontrola/qr-lookup', json=qr, headers={'X-CSRF-Token': _csrf(client)}).get_json()

    def test_lookup_before_and_after_first_scan_with_departments(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        assert self._lookup(client) == {'ok': True, 'mode': 'orders', 'exists': False}
        _scan(client)
        assert self._lookup(client) == {'ok': True, 'mode': 'orders', 'exists': True}
        assert self._lookup(client, {**QR, 'p': 'Inny'})['exists'] is False       # inny produkt z tym samym ZO
        assert self._lookup(client, {**QR, 'p': ' szafa   psh '})['exists'] is True
        assert self._lookup(client, {'p': 'X'})['exists'] is False                # brak ZO

    def test_lookup_in_legacy_mode_and_access(self, client, app, operator_user):
        with app.app_context():
            db.session.add(ControlPoint(title='Ogólny', order=1))
            db.session.commit()
        login(client, 'oper', 'Oper1234!')
        assert self._lookup(client) == {'ok': True, 'mode': 'legacy', 'exists': False}
        _scan(client)
        assert self._lookup(client) == {'ok': True, 'mode': 'legacy', 'exists': True}
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        assert client.post('/kontrola/qr-lookup', json=QR, headers={'X-CSRF-Token': _csrf(client)}).status_code == 403

    def test_scan_page_has_labels(self, client, app):
        login(client, 'oper', 'Oper1234!')
        page = client.get('/kontrola/scan').get_data(as_text=True)
        for text in ('Wpis zlecenia na listę', 'Rozpocznij kontrolę', 'qr-lookup'):
            assert text in page
