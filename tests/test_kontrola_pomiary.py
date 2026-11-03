"""Testy pomiarów z miernika w punktach Kontroli etapowej: ustawienia punktu, wpis wartości, wynik z zakresu,
zakończenie kontroli, raport QAR z wartością i widoki."""
import pytest

from app import db
from models import (User, Alert, QARReport, ControlPoint, ControlRecord, ControlResult, ControlOrder,
                    ControlElement, ControlRecordElement, ProductionDepartment, DepartmentEmployee)
from test_app import login, _csrf

QR = {'p': 'Szafa PSH', 'c': 'Klient X', 'o': 'ZO/970/2026', 'q': 2}


def _wipe():
    ControlResult.query.delete()
    ControlRecordElement.query.delete()
    ControlRecord.query.update({'employee_id': None})
    ControlRecord.query.delete()
    ControlOrder.query.delete()
    QARReport.query.filter(QARReport.zo_number.like('ZO/97%')).update({'employee_id': None},
                                                                     synchronize_session=False)
    QARReport.query.filter(QARReport.zo_number.like('ZO/97%')).delete(synchronize_session=False)
    ControlPoint.query.delete()
    ControlElement.query.delete()
    Alert.query.filter(Alert.message.like('ZO ZO/97%')).delete(synchronize_session=False)
    DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T11 %')).delete(synchronize_session=False)
    ProductionDepartment.query.filter(ProductionDepartment.name.like('T11 %')).delete(synchronize_session=False)
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
        if not User.query.filter_by(username='operator11').first():
            u = User(username='operator11', email='operator11@test.pl', role='operator')
            u.set_password('Operator123!')
            db.session.add(u)
            db.session.commit()
    return 'operator11'


@pytest.fixture
def setup(app):
    """Dział T11 z punktem OK/NG (g), pomiarem z zakresem (rng 10-12 mm), tylko minimum (lo 5 V), tylko
    maksimum (hi 3 mm), bez limitów (free) oraz pomiarem elementu (el 1-2 N)."""
    with app.app_context():
        d = ProductionDepartment(name='T11 Dział', order=970)
        db.session.add(d)
        db.session.flush()
        e = ControlElement(name='T11 Drzwi', order=1)
        db.session.add(e)
        db.session.flush()
        db.session.add_all([
            ControlPoint(title='g Wygląd', order=1, department_id=d.id),
            ControlPoint(title='rng Wymiar', order=2, department_id=d.id, point_type='pomiar', unit='mm',
                         value_min=10.0, value_max=12.0),
            ControlPoint(title='lo Napięcie', order=3, department_id=d.id, point_type='pomiar', unit='V',
                         value_min=5.0),
            ControlPoint(title='hi Luz', order=4, department_id=d.id, point_type='pomiar', unit='mm',
                         value_max=3.0),
            ControlPoint(title='free Odczyt', order=5, department_id=d.id, point_type='pomiar'),
            ControlPoint(title='el Moment', order=6, element_id=e.id, point_type='pomiar', unit='N', value_min=1.0,
                         value_max=2.0)])
        emp = DepartmentEmployee(department_id=d.id, name='T11 Jan')
        db.session.add(emp)
        db.session.commit()
        return {'d': d.id, 'e': e.id}


def _scan(client, qr=QR):
    return client.post('/kontrola/from-qr', json=qr, headers={'X-CSRF-Token': _csrf(client)})


def _order(app, qr=QR):
    with app.app_context():
        for o in ControlOrder.query.filter_by(zo_number=qr['o']).all():
            if o.product_name == qr['p']:
                return o.id


def _begin(client, app, dept, elements=(), qr=QR):
    _scan(client, qr)
    oid = _order(app, qr)
    res = client.post(f'/kontrola/zlecenie/{oid}/start',
                      data={'department_id': dept, 'element_ids': [str(e) for e in elements]},
                      headers={'X-CSRF-Token': _csrf(client)})
    return int(res.headers['Location'].rsplit('/', 1)[1])


def _by_title(app, rid):
    with app.app_context():
        return {r.title.split(' ')[0]: r.id for r in db.session.get(ControlRecord, rid).results}


def _post(client, rid, data, action='save'):
    payload = {'action': action, **data}
    return client.post(f'/kontrola/{rid}', data=payload, headers={'X-CSRF-Token': _csrf(client)},
                       follow_redirects=True)


def _res(app, res_id):
    with app.app_context():
        r = db.session.get(ControlResult, res_id)
        return r.result, r.value_text


class TestPointSettings:
    def _form(self, pts, **extra):
        data = {'save_first': '1'}
        for p in pts:
            data[f'title_{p.id}'] = p.title
            data[f'department_{p.id}'] = str(p.department_id or '')
            data[f'element_{p.id}'] = str(p.element_id or '')
            data[f'type_{p.id}'] = p.point_type
            data[f'unit_{p.id}'] = p.unit or ''
            data[f'min_{p.id}'] = '' if p.value_min is None else str(p.value_min)
            data[f'max_{p.id}'] = '' if p.value_max is None else str(p.value_max)
            if p.is_active:
                data[f'active_{p.id}'] = '1'
        data.update(extra)
        return data

    def _points(self, app):
        with app.app_context():
            pts = ControlPoint.query.order_by(ControlPoint.order, ControlPoint.id).all()
            for p in pts:
                db.session.expunge(p)
            return pts

    def test_page_has_type_and_limit_fields(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        page = client.get('/kontrola/points').get_data(as_text=True)
        assert 'name="type_' in page and 'name="unit_' in page and 'name="min_' in page and 'name="max_' in page
        assert 'name="new_type"' in page and 'name="new_min"' in page and 'name="new_max"' in page
        assert 'value="10"' in page and 'value="12"' in page                    # limity istniejącego pomiaru
        assert page.count('Zapisz wszystko</button>') == 1

    def test_save_type_unit_limits_with_comma_and_clear_for_ok_ng(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        pts = self._points(app)
        data = self._form(pts)
        data[f'type_{pts[0].id}'] = 'pomiar'                                   # OK/NG -> pomiar
        data[f'unit_{pts[0].id}'] = 'kg'
        data[f'min_{pts[0].id}'] = '1,5'
        data[f'max_{pts[0].id}'] = '2.25'
        data[f'type_{pts[1].id}'] = 'ok_ng'                                    # pomiar -> OK/NG czyści limity
        res = client.post('/kontrola/points/zapisz-wszystko', data=data, headers={'X-CSRF-Token': _csrf(client)},
                          follow_redirects=True)
        assert 'zapisano zmiany w punktach: 2' in res.get_data(as_text=True).lower()
        with app.app_context():
            a = db.session.get(ControlPoint, pts[0].id)
            assert (a.point_type, a.unit, a.value_min, a.value_max) == ('pomiar', 'kg', 1.5, 2.25)
            b = db.session.get(ControlPoint, pts[1].id)
            assert (b.point_type, b.unit, b.value_min, b.value_max) == ('ok_ng', None, None, None)

    def test_invalid_limits_skip_only_that_point(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        pts = self._points(app)
        data = self._form(pts)
        data[f'min_{pts[1].id}'] = '20'                                         # min > max (12)
        data[f'type_{pts[2].id}'] = 'pomiar'
        data[f'min_{pts[2].id}'] = 'abc'                                        # nie liczba
        data[f'title_{pts[0].id}'] = 'g Wygląd zmieniony'                       # poprawna zmiana obok błędnych
        res = client.post('/kontrola/points/zapisz-wszystko', data=data, headers={'X-CSRF-Token': _csrf(client)},
                          follow_redirects=True)
        text = res.get_data(as_text=True)
        assert 'minimum nie może być większe niż maksimum' in text and 'muszą być liczbami' in text
        with app.app_context():
            assert db.session.get(ControlPoint, pts[0].id).title == 'g Wygląd zmieniony'
            assert db.session.get(ControlPoint, pts[1].id).value_min == 10.0
            assert db.session.get(ControlPoint, pts[2].id).value_min == 5.0

    def test_new_measure_point_and_invalid_new_point(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        h = {'X-CSRF-Token': _csrf(client)}
        client.post('/kontrola/points/zapisz-wszystko', headers=h, data={
            'new_title': 'T11 Nowy pomiar', 'new_type': 'pomiar', 'new_unit': 'Ω', 'new_min': '0,5', 'new_max': '1'})
        res = client.post('/kontrola/points/zapisz-wszystko', headers=h, follow_redirects=True, data={
            'new_title': 'T11 Zły pomiar', 'new_type': 'pomiar', 'new_min': '5', 'new_max': '1'})
        assert 'Nie dodano' in res.get_data(as_text=True)
        with app.app_context():
            p = ControlPoint.query.filter_by(title='T11 Nowy pomiar').one()
            assert (p.point_type, p.unit, p.value_min, p.value_max) == ('pomiar', 'Ω', 0.5, 1.0)
            assert ControlPoint.query.filter_by(title='T11 Zły pomiar').count() == 0


class TestEntryAndResult:
    def test_limits_are_copied_to_result_and_history_is_frozen(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['d'])
        with app.app_context():
            r = ControlResult.query.filter_by(record_id=rid, title='rng Wymiar').one()
            assert (r.point_type, r.unit, r.value_min, r.value_max) == ('pomiar', 'mm', 10.0, 12.0)
            assert r.limits_label == '10 – 12 mm'
            pt = ControlPoint.query.filter_by(title='rng Wymiar').one()
            pt.value_max = 99.0                                            # zmiana punktu po utworzeniu kontroli
            db.session.commit()
            assert ControlResult.query.filter_by(record_id=rid, title='rng Wymiar').one().value_max == 12.0
        ids = _by_title(app, rid)
        _post(client, rid, {f'value_{ids["rng"]}': '12,5'})                # wg kopii limitów (max 12) to NG
        assert _res(app, ids['rng']) == ('NG', '12,5')

    @pytest.mark.parametrize('key,value,expected', [
        ('rng', '10', 'OK'), ('rng', '12', 'OK'), ('rng', '11,5', 'OK'), ('rng', '11.5', 'OK'),
        ('rng', '9,99', 'NG'), ('rng', '12,01', 'NG'), ('rng', ' 10 ', 'OK'),
        ('lo', '5', 'OK'), ('lo', '4,9', 'NG'), ('lo', '1000', 'OK'),
        ('hi', '3', 'OK'), ('hi', '3,1', 'NG'), ('hi', '-7', 'OK'),
        ('free', '123456', 'OK'), ('free', '-5', 'OK')])
    def test_result_from_range(self, client, app, setup, key, value, expected):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['d'])
        ids = _by_title(app, rid)
        _post(client, rid, {f'value_{ids[key]}': value})
        result, text = _res(app, ids[key])
        assert result == expected and text == value.strip().replace('.', ',')

    def test_invalid_empty_and_overridden_values(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['d'])
        ids = _by_title(app, rid)
        _post(client, rid, {f'value_{ids["rng"]}': '11'})
        assert _res(app, ids['rng']) == ('OK', '11')
        res = _post(client, rid, {f'value_{ids["rng"]}': '1x1'})              # nie liczba: stan bez zmian + komunikat
        assert 'nie jest liczbą' in res.get_data(as_text=True)
        assert _res(app, ids['rng']) == ('OK', '11')
        for bad in ('nan', 'inf', '1e999'):
            _post(client, rid, {f'value_{ids["rng"]}': bad})
            assert _res(app, ids['rng']) == ('OK', '11'), bad
        _post(client, rid, {f'value_{ids["rng"]}': '50', f'result_{ids["rng"]}': 'OK'})   # podstawiony wynik ignorowany
        assert _res(app, ids['rng']) == ('NG', '50')
        _post(client, rid, {f'result_{ids["rng"]}': 'OK'})                    # brak pola wartości: bez zmian
        assert _res(app, ids['rng']) == ('NG', '50')
        _post(client, rid, {f'value_{ids["rng"]}': '  '})                    # puste pole czyści
        assert _res(app, ids['rng']) == (None, None)

    def test_completion_requires_all_values_and_ok_ng_buttons(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['d'])
        ids = _by_title(app, rid)
        data = {f'result_{ids["g"]}': 'OK', f'value_{ids["rng"]}': '11', f'value_{ids["lo"]}': '6',
                f'value_{ids["hi"]}': '2'}                                   # brak pola free
        res = _post(client, rid, data, action='complete')
        assert 'Oceń wszystkie punkty' in res.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(ControlRecord, rid).status == 'in_progress'
        data[f'value_{ids["free"]}'] = '7'
        _post(client, rid, data, action='complete')
        with app.app_context():
            rec = db.session.get(ControlRecord, rid)
            assert rec.status == 'completed' and rec.result == 'OK' and rec.ng_count == 0

    def test_ng_creates_single_qar_with_value_and_limits(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['d'])
        ids = _by_title(app, rid)
        _post(client, rid, {f'value_{ids["rng"]}': '12,5'})
        _post(client, rid, {f'value_{ids["rng"]}': '12,7'})                  # ponowny zapis NG: bez duplikatu
        with app.app_context():
            qars = QARReport.query.filter_by(zo_number='ZO/970/2026').all()
            assert len(qars) == 1
            assert 'Wartość: 12,7 mm (zakres 10 – 12 mm)' in qars[0].description or \
                   'Wartość: 12,5 mm (zakres 10 – 12 mm)' in qars[0].description
        _post(client, rid, {f'value_{ids["rng"]}': '11'})                    # wartość wraca do zakresu
        assert _res(app, ids['rng']) == ('OK', '11')
        with app.app_context():
            assert QARReport.query.filter_by(zo_number='ZO/970/2026').count() == 1

    def test_element_section_measure_and_mixed_numbering(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['d'], [setup['e']])
        ids = _by_title(app, rid)
        _post(client, rid, {f'value_{ids["el"]}': '1,5'})
        assert _res(app, ids['el']) == ('OK', '1,5')
        page = client.get(f'/kontrola/{rid}').get_data(as_text=True)
        assert 'Element: T11 Drzwi' in page and 'zakres: 1 – 2 N' in page
        for n in range(1, 7):
            assert f'<span class="kz-lp">{n}.</span>' in page

    def test_legacy_general_control_supports_measure(self, client, app):
        with app.app_context():
            db.session.add_all([ControlPoint(title='lg Ogólny', order=1),
                                ControlPoint(title='lm Pomiar', order=2, point_type='pomiar', unit='V',
                                             value_min=1.0, value_max=2.0)])
            db.session.commit()
        login(client, 'oper', 'Oper1234!')
        assert _scan(client).get_json()['ok']
        with app.app_context():
            rec = ControlRecord.query.one()
            rid = rec.id
        ids = _by_title(app, rid)
        _post(client, rid, {f'result_{ids["lg"]}': 'OK', f'value_{ids["lm"]}': '2,5'})
        assert _res(app, ids['lm']) == ('NG', '2,5')


class TestViews:
    def test_edit_screen_shows_input_unit_range_and_live_preview(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['d'])
        page = client.get(f'/kontrola/{rid}').get_data(as_text=True)
        assert 'name="value_' in page and 'inputmode="decimal"' in page
        assert 'zakres: 10 – 12 mm' in page and 'zakres: min 5 V' in page and 'zakres: max 3 mm' in page
        assert 'zakres: bez limitów' in page and 'kz-live' in page
        assert page.count('name="result_') == 2                          # OK/NG tylko dla punktu OK/NG

    def test_readonly_and_operator_see_value(self, client, app, setup, operator_user):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['d'])
        ids = _by_title(app, rid)
        _post(client, rid, {f'result_{ids["g"]}': 'OK', f'value_{ids["rng"]}': '11,2', f'value_{ids["lo"]}': '4',
                            f'value_{ids["hi"]}': '1', f'value_{ids["free"]}': '9'}, action='complete')
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        page = client.get(f'/kontrola/{rid}').get_data(as_text=True)
        assert 'Wartość: <strong>11,2 mm</strong> (zakres 10 – 12 mm)' in page
        assert 'Wartość: <strong>4 V</strong> (zakres min 5 V)' in page
        assert 'name="value_' not in page
        assert client.post(f'/kontrola/{rid}', data={f'value_{ids["rng"]}': '100'},
                           headers={'X-CSRF-Token': _csrf(client)}).status_code == 403
        assert _res(app, ids['rng']) == ('OK', '11,2')
