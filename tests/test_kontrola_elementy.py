"""Testy elementów kontroli w Kontroli etapowej: lista elementów, przypisanie punktów, wybór elementów po
wyborze działu, sekcje kontroli, historia, lista, filtr i raport QAR."""
import pytest

from app import db
from models import (User, Alert, QARReport, ControlPoint, ControlRecord, ControlResult, ControlOrder,
                    ControlElement, ControlRecordElement, ProductionDepartment, DepartmentEmployee)
from test_app import login, _csrf

QR = {'p': 'Szafa PSH', 'c': 'Klient X', 'o': 'ZO/950/2026', 'q': 3}


def _wipe():
    ControlResult.query.delete()
    ControlRecordElement.query.delete()
    ControlRecord.query.update({'employee_id': None})
    ControlRecord.query.delete()
    ControlOrder.query.delete()
    QARReport.query.filter(QARReport.zo_number.like('ZO/95%')).update({'employee_id': None},
                                                                     synchronize_session=False)
    QARReport.query.filter(QARReport.zo_number.like('ZO/95%')).delete(synchronize_session=False)
    ControlPoint.query.delete()
    ControlElement.query.delete()
    Alert.query.filter(Alert.message.like('ZO ZO/95%')).delete(synchronize_session=False)
    DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T10 %')).delete(synchronize_session=False)
    ProductionDepartment.query.filter(ProductionDepartment.name.like('T10 %')).delete(synchronize_session=False)
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
        if not User.query.filter_by(username='operator10').first():
            u = User(username='operator10', email='operator10@test.pl', role='operator')
            u.set_password('Operator123!')
            db.session.add(u)
            db.session.commit()
    return 'operator10'


@pytest.fixture
def setup(app):
    """Działy A (Spawalnia) i B (Montaż), elementy Drzwi i Korpus (+ nieaktywna Plyta) i punkty o różnym zasięgu:
    g ogólny, dA tylko dział A, eD element Drzwi (każdy dział), eDA Drzwi tylko w A, eKB Korpus tylko w B."""
    with app.app_context():
        a = ProductionDepartment(name='T10 Spawalnia', order=950)
        b = ProductionDepartment(name='T10 Montaż', order=951)
        db.session.add_all([a, b])
        db.session.flush()
        d = ControlElement(name='T10 Drzwi', order=1)
        k = ControlElement(name='T10 Korpus', order=2)
        p = ControlElement(name='T10 Płyta', order=3, is_active=False)
        db.session.add_all([d, k, p])
        db.session.flush()
        db.session.add_all([
            ControlPoint(title='g Wygląd', order=1),
            ControlPoint(title='dA Spoiny', order=2, department_id=a.id),
            ControlPoint(title='dB Okablowanie', order=3, department_id=b.id),
            ControlPoint(title='eD Zawiasy', order=4, element_id=d.id),
            ControlPoint(title='eDA Spoina drzwi', order=5, element_id=d.id, department_id=a.id),
            ControlPoint(title='eKB Mocowanie korpusu', order=6, element_id=k.id, department_id=b.id),
            ControlPoint(title='eD Wyłączony', order=7, element_id=d.id, is_active=False)])
        e = {'jan': DepartmentEmployee(department_id=a.id, name='T10 Jan Spawacz')}
        db.session.add_all(e.values())
        db.session.commit()
        return {'a': a.id, 'b': b.id, 'd': d.id, 'k': k.id, 'p': p.id, 'jan': e['jan'].id}


def _scan(client, qr=QR):
    return client.post('/kontrola/from-qr', json=qr, headers={'X-CSRF-Token': _csrf(client)})


def _order(app, qr=QR):
    with app.app_context():
        for o in ControlOrder.query.filter_by(zo_number=qr['o']).all():
            if o.product_name == qr['p']:
                return o.id


def _start(client, oid, dept, elements=()):
    return client.post(f'/kontrola/zlecenie/{oid}/start',
                       data={'department_id': dept, 'element_ids': [str(e) for e in elements]},
                       headers={'X-CSRF-Token': _csrf(client)})


def _begin(client, app, dept, elements=(), qr=QR):
    _scan(client, qr)
    oid = _order(app, qr)
    res = _start(client, oid, dept, elements)
    return int(res.headers['Location'].rsplit('/', 1)[1])


def _titles(app, rid):
    with app.app_context():
        rec = db.session.get(ControlRecord, rid)
        return [(r.element_name, r.title) for r in rec.results]


def _rate(client, app, rid, results, employee=None, action='complete'):
    with app.app_context():
        ids = [r.id for r in db.session.get(ControlRecord, rid).results]
    data = {'action': action}
    for res_id, val in zip(ids, results):
        data[f'result_{res_id}'] = val
    if employee is not None:
        data['employee_id'] = str(employee)
    return client.post(f'/kontrola/{rid}', data=data, headers={'X-CSRF-Token': _csrf(client)})


class TestElementsAdmin:
    def test_add_rename_toggle_move_delete(self, client, app):
        login(client, 'admin', 'Admin1234!')
        h = {'X-CSRF-Token': _csrf(client)}
        client.post('/kontrola/elementy/dodaj', data={'name': '  T10   Płyta  montażowa '}, headers=h)
        client.post('/kontrola/elementy/dodaj', data={'name': 't10 płyta montażowa'}, headers=h)       # duplikat
        client.post('/kontrola/elementy/dodaj', data={'name': '   '}, headers=h)                       # pusty
        client.post('/kontrola/elementy/dodaj', data={'name': 'T10 Drzwi'}, headers=h)
        with app.app_context():
            names = [e.name for e in ControlElement.query.order_by(ControlElement.order, ControlElement.id)]
            assert names == ['T10 Płyta montażowa', 'T10 Drzwi']
            first, second = [e.id for e in ControlElement.query.order_by(ControlElement.order, ControlElement.id)]
        client.post(f'/kontrola/elementy/{second}/przesun/up', headers=h)
        client.post(f'/kontrola/elementy/{first}/zapisz', data={'name': 'T10 Płyta', 'is_active': ''}, headers=h)
        client.post(f'/kontrola/elementy/{second}/zapisz', data={'name': 'T10 Płyta', 'is_active': '1'}, headers=h)   # duplikat nazwy
        with app.app_context():
            ordered = ControlElement.query.order_by(ControlElement.order, ControlElement.id).all()
            assert [(e.name, e.is_active) for e in ordered] == [('T10 Drzwi', True), ('T10 Płyta', False)]
        page = client.get('/kontrola/elementy').get_data(as_text=True)
        assert 'T10 Drzwi' in page and 'T10 Płyta' in page and 'punkty: 0' in page
        client.post(f'/kontrola/elementy/{first}/usun', headers=h)
        client.post(f'/kontrola/elementy/{second}/usun', headers=h)
        with app.app_context():
            assert ControlElement.query.count() == 0

    def test_access_only_admin(self, client, app, operator_user):
        for user, pw in (('oper', 'Oper1234!'), (operator_user, 'Operator123!')):
            client.get('/logout')
            login(client, user, pw)
            h = {'X-CSRF-Token': _csrf(client)}
            assert client.get('/kontrola/elementy').status_code == 403
            assert client.post('/kontrola/elementy/dodaj', data={'name': 'X'}, headers=h).status_code == 403
        with app.app_context():
            assert ControlElement.query.count() == 0

    def test_delete_blocked_when_points_use_it_but_keeps_record_names(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        h = {'X-CSRF-Token': _csrf(client)}
        client.post(f'/kontrola/elementy/{setup["d"]}/usun', headers=h)
        with app.app_context():
            assert db.session.get(ControlElement, setup['d']) is not None                # ma punkty
            ControlPoint.query.filter_by(element_id=setup['k']).delete()
            db.session.commit()
        rid = _begin(client, app, setup['b'], [setup['k']])
        client.post(f'/kontrola/elementy/{setup["k"]}/usun', headers=h)                  # brak punktów: można
        with app.app_context():
            assert db.session.get(ControlElement, setup['k']) is None
            rec = db.session.get(ControlRecord, rid)
            assert rec.element_names == ['T10 Korpus'] and rec.element_ids == set()
        assert 'T10 Korpus' in client.get(f'/kontrola/{rid}').get_data(as_text=True)


class TestPointsAndElements:
    def test_assign_element_to_points_and_filter(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        h = {'X-CSRF-Token': _csrf(client)}
        client.post('/kontrola/points/add', data={'title': 'T10 Nowy', 'element_id': str(setup['k'])}, headers=h)
        client.post('/kontrola/points/add', data={'title': 'T10 Zły', 'element_id': '999999'}, headers=h)
        with app.app_context():
            pts = {p.title: p.element_id for p in ControlPoint.query.filter(ControlPoint.title.like('T10 %'))}
            assert pts == {'T10 Nowy': setup['k'], 'T10 Zły': None}
            pid = ControlPoint.query.filter_by(title='T10 Zły').one().id
        client.post(f'/kontrola/points/{pid}/save', data={'title': 'T10 Zły', 'is_active': '1',
                                                          'element_id': str(setup['d'])}, headers=h)
        client.post(f'/kontrola/points/{pid}/save', data={'title': 'T10 Zły', 'is_active': '1'}, headers=h)   # bez pola
        with app.app_context():
            assert db.session.get(ControlPoint, pid).element_id == setup['d']
        k_only = client.get(f'/kontrola/points?element={setup["k"]}').get_data(as_text=True)
        assert 'T10 Nowy' in k_only and 'Mocowanie korpusu' in k_only and 'Wygląd' not in k_only
        none_el = client.get('/kontrola/points?element=-1').get_data(as_text=True)
        assert 'Wygląd' in none_el and 'Zawiasy' not in none_el
        assert 'Elementy' in client.get('/kontrola/points').get_data(as_text=True)


class TestStartWithElements:
    def test_scope_rules_for_points(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        # dział A bez elementów: tylko wspólne i działowe punkty bez elementu
        r1 = _begin(client, app, setup['a'])
        assert _titles(app, r1) == [(None, 'g Wygląd'), (None, 'dA Spoiny')]
        # dział A + Drzwi: sekcja Drzwi ma punkty elementu bez działu i elementu w tym dziale
        qr2 = {**QR, 'p': 'Druga'}
        r2 = _begin(client, app, setup['a'], [setup['d']], qr2)
        assert _titles(app, r2) == [(None, 'g Wygląd'), (None, 'dA Spoiny'),
                                    ('T10 Drzwi', 'eD Zawiasy'), ('T10 Drzwi', 'eDA Spoina drzwi')]
        # dział B + Drzwi i Korpus: Drzwi tylko eD (eDA tylko w A), Korpus eKB
        qr3 = {**QR, 'p': 'Trzecia'}
        r3 = _begin(client, app, setup['b'], [setup['d'], setup['k']], qr3)
        assert _titles(app, r3) == [(None, 'g Wygląd'), (None, 'dB Okablowanie'), ('T10 Drzwi', 'eD Zawiasy'),
                                    ('T10 Korpus', 'eKB Mocowanie korpusu')]
        with app.app_context():
            assert db.session.get(ControlRecord, r3).element_names == ['T10 Drzwi', 'T10 Korpus']
            assert [l.position for l in db.session.get(ControlRecord, r3).element_links] == [1, 2]

    def test_element_without_points_in_department_gives_only_shared(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['a'], [setup['k']])           # Korpus ma punkty tylko w dziale B
        assert _titles(app, rid) == [(None, 'g Wygląd'), (None, 'dA Spoiny')]
        with app.app_context():
            assert db.session.get(ControlRecord, rid).element_names == ['T10 Korpus']

    def test_invalid_inactive_and_duplicate_elements(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        oid = _order(app)
        for bad in ([setup['p']], [999999], ['x'], [setup['d'], setup['p']]):
            res = client.post(f'/kontrola/zlecenie/{oid}/start',
                              data={'department_id': setup['a'], 'element_ids': [str(e) for e in bad]},
                              headers={'X-CSRF-Token': _csrf(client)}, follow_redirects=True)
            assert 'nie istnieje albo jest nieaktywny' in res.get_data(as_text=True), bad
        with app.app_context():
            assert ControlRecord.query.count() == 0
        res = _start(client, oid, setup['a'], [setup['d'], setup['d']])
        assert res.status_code == 302
        with app.app_context():
            assert ControlRecord.query.one().element_names == ['T10 Drzwi']

    def test_same_set_opens_existing_other_set_creates_new(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        first = _begin(client, app, setup['a'], [setup['d']])
        oid = _order(app)
        again = _start(client, oid, setup['a'], [setup['d']])
        assert again.headers['Location'].endswith(f'/kontrola/{first}')
        other = _start(client, oid, setup['a'], [setup['k']])
        none_set = _start(client, oid, setup['a'])
        assert other.headers['Location'].endswith(f'/kontrola/{first}') is False
        assert none_set.headers['Location'].rsplit('/', 1)[1] not in (str(first), other.headers['Location'].rsplit('/', 1)[1])
        with app.app_context():
            assert ControlRecord.query.count() == 3

    def test_optional_selection_and_page_form(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        page = client.get(f'/kontrola/zlecenie/{_order(app)}').get_data(as_text=True)
        assert 'name="element_ids"' in page and 'T10 Drzwi' in page and 'T10 Korpus' in page
        assert 'T10 Płyta' not in page                                    # nieaktywny nie jest oferowany
        assert 'Elementy (opcjonalnie)' in page and 'Rozpocznij kontrolę' in page
        rid = _begin(client, app, setup['a'])                              # bez elementów też można
        assert rid


class TestControlScreenAndHistory:
    def test_sections_numbering_and_completion(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['a'], [setup['d']])
        page = client.get(f'/kontrola/{rid}').get_data(as_text=True)
        assert 'Wspólne' in page and 'Element: T10 Drzwi' in page and 'Elementy: <strong>T10 Drzwi</strong>' in page
        assert page.index('g Wygląd') < page.index('Element: T10 Drzwi') < page.index('eD Zawiasy')
        for n in ('1.', '2.', '3.', '4.'):
            assert f'<span class="kz-lp">{n}</span>' in page
        _rate(client, app, rid, ['OK', 'OK', 'OK'], action='save')
        res = _rate(client, app, rid, ['OK', 'OK', 'OK', 'OK'], employee=setup['jan'])
        oid = _order(app)
        assert res.headers['Location'].endswith(f'/kontrola/zlecenie/{oid}')
        with app.app_context():
            assert db.session.get(ControlRecord, rid).result == 'OK'

    def test_no_section_headings_without_elements(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['a'])
        page = client.get(f'/kontrola/{rid}').get_data(as_text=True)
        assert 'class="kz-group"' not in page and 'Elementy:' not in page

    def test_ng_in_element_section_creates_qar_with_element(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['a'], [setup['d']])
        _rate(client, app, rid, ['OK', 'OK', 'NG', 'OK'], employee=setup['jan'])
        with app.app_context():
            qar = QARReport.query.filter_by(zo_number='ZO/950/2026').one()
            assert 'Element: T10 Drzwi' in qar.description and '(T10 Drzwi)' in qar.title
            assert 'eD Zawiasy' in qar.title

    def test_rename_keeps_history_name_and_recheck_prefills(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['a'], [setup['d'], setup['k']])
        _rate(client, app, rid, ['OK'] * 4)
        oid = _order(app)
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        client.post(f'/kontrola/elementy/{setup["d"]}/zapisz', data={'name': 'T10 Drzwi nowe', 'is_active': '1'},
                    headers={'X-CSRF-Token': _csrf(client)})
        client.get('/logout')
        login(client, 'oper', 'Oper1234!')
        page = client.get(f'/kontrola/zlecenie/{oid}').get_data(as_text=True)
        assert 'T10 Drzwi, T10 Korpus' in page                                # stara nazwa w historii
        assert f'name="element_ids" value="{setup["d"]}"' in page             # ponowna kontrola z tym samym zestawem
        again = _start(client, oid, setup['a'], [setup['d'], setup['k']])
        second = int(again.headers['Location'].rsplit('/', 1)[1])
        assert second != rid
        with app.app_context():
            assert ControlRecord.query.filter_by(control_order_id=oid).count() == 2


class TestListWithElements:
    def test_badges_filter_and_search(self, client, app, setup):
        login(client, 'oper', 'Oper1234!')
        r1 = _begin(client, app, setup['a'], [setup['d']])
        _rate(client, app, r1, ['OK'] * 4)
        other = {**QR, 'o': 'ZO/951/2026', 'p': 'Inna szafa'}
        r2 = _begin(client, app, setup['b'], [setup['k']], other)
        _rate(client, app, r2, ['OK'] * 3)
        page = client.get('/kontrola/').get_data(as_text=True)
        assert 'T10 Spawalnia (T10 Drzwi)' in page and 'T10 Montaż (T10 Korpus)' in page
        assert 'Wszystkie elementy' in page
        by_el = client.get(f'/kontrola/?element={setup["d"]}').get_data(as_text=True)
        assert 'ZO/950/2026' in by_el and 'ZO/951/2026' not in by_el
        by_name = client.get('/kontrola/?zo=korpus').get_data(as_text=True)
        assert 'ZO/951/2026' in by_name and 'ZO/950/2026' not in by_name
        both = client.get(f'/kontrola/?element={setup["k"]}&dzial={setup["b"]}').get_data(as_text=True)
        assert 'ZO/951/2026' in both and 'ZO/950/2026' not in both

    def test_operator_sees_elements_read_only(self, client, app, setup, operator_user):
        login(client, 'oper', 'Oper1234!')
        rid = _begin(client, app, setup['a'], [setup['d']])
        _rate(client, app, rid, ['OK'] * 4)
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        assert 'T10 Drzwi' in client.get('/kontrola/?s=').get_data(as_text=True)
        assert 'Element: T10 Drzwi' in client.get(f'/kontrola/{rid}').get_data(as_text=True)


class TestLegacyUnaffected:
    def test_general_control_ignores_element_points(self, client, app):
        with app.app_context():
            el = ControlElement(name='T10 Drzwi', order=1)
            db.session.add(el)
            db.session.flush()
            db.session.add_all([ControlPoint(title='Ogólny', order=1),
                                ControlPoint(title='Punkt elementu', order=2, element_id=el.id)])
            db.session.commit()
        login(client, 'oper', 'Oper1234!')
        assert _scan(client).get_json()['ok']
        with app.app_context():
            rec = ControlRecord.query.one()
            assert [r.title for r in rec.results] == ['Ogólny'] and rec.department_id is None
