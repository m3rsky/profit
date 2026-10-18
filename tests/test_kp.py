"""Testy etapu 2: szablony produktów i zlecenia ze skanu kodu QR (moduł Kontrola produkcji).

Szablon zawiera tylko elementy i trasy. Ilość elementu na sztukę produktu podaje się przy
tworzeniu zlecenia (krok 2 skanu), bo zależy od zlecenia."""
import pytest

from app import app as flask_app, db
from models import (User, ProductionStage, ProductTemplate, ProductComponent,
                    ProductComponentStage, WorkOrder, WorkOrderComponent,
                    WorkOrderComponentStage)
from test_app import login, _csrf
from kp import service

KP = '/kontrolaprodukcji'
US = '/ustawienia-produkcji'


def post(client, url, **data):
    data['_csrf_token'] = _csrf(client)
    return client.post(url, data=data, follow_redirects=True)


def scan(client, **payload):
    """Krok 1: odczyt kodu QR zlecenia."""
    return client.post(KP + '/z-qr', json=payload, headers={'X-CSRF-Token': _csrf(client)})


def create(client, per_unit, **payload):
    """Krok 2: utworzenie zlecenia. per_unit: {nazwa elementu: ilość na sztukę}."""
    return client.post(KP + '/utworz', json={**payload, 'per_unit': per_unit},
                       headers={'X-CSRF-Token': _csrf(client)})


def new_order(client, per_unit=None, **payload):
    """Skan i utworzenie zlecenia w dwóch krokach, zwraca odpowiedź kroku 2."""
    step1 = scan(client, **payload)
    body = step1.get_json()
    assert step1.status_code == 200, body
    if body.get('existing'):
        return step1
    names = per_unit or {'Drzwi': 2, 'Korpus': 1}
    by_id = {str(c['id']): names.get(c['name'], 0) for c in body['components']}
    return client.post(KP + '/utworz', json={**payload, 'per_unit': by_id},
                       headers={'X-CSRF-Token': _csrf(client)})


@pytest.fixture(autouse=True)
def _clean(app):
    def wipe():
        with app.app_context():
            for m in (WorkOrderComponentStage, WorkOrderComponent, WorkOrder,
                      ProductComponentStage, ProductComponent, ProductTemplate):
                m.query.delete()
            ProductionStage.query.filter(ProductionStage.name.like('T2 %')).delete(
                synchronize_session=False)
            db.session.commit()
    wipe()
    yield
    wipe()


@pytest.fixture
def stages(app):
    """T2 Wycinanie (1), T2 Gięcie (2) na poziomie elementu, T2 Składanie (3) na poziomie produktu."""
    with app.app_context():
        rows = [ProductionStage(name='T2 Wycinanie', order=901, level='element'),
                ProductionStage(name='T2 Gięcie', order=902, level='element'),
                ProductionStage(name='T2 Składanie', order=903, level='produkt')]
        db.session.add_all(rows)
        db.session.commit()
        return [r.id for r in rows]


@pytest.fixture
def role_users(app):
    users = {'operator': 'kp_operator', 'order': 'kp_order', 'monter': 'kp_monter',
             'konstruktor': 'kp_konstruktor'}
    with app.app_context():
        for role, name in users.items():
            if not User.query.filter_by(username=name).first():
                u = User(username=name, email=f'{name}@test.pl', role=role)
                u.set_password('Rola1234!')
                db.session.add(u)
        db.session.commit()
    return users


def make_template(app, name='PSH', elements=None, active=True, stage_ids=()):
    """elements: lista nazw elementów; każdy dostaje etapy z stage_ids."""
    elements = ['Drzwi', 'Korpus'] if elements is None else elements
    with app.app_context():
        tpl = ProductTemplate(name=name, is_active=active)
        db.session.add(tpl)
        db.session.flush()
        for i, cname in enumerate(elements, 1):
            c = ProductComponent(template_id=tpl.id, name=cname, position=i)
            db.session.add(c)
            db.session.flush()
            for sid in stage_ids:
                db.session.add(ProductComponentStage(component_id=c.id, stage_id=sid))
        db.session.commit()
        return tpl.id


QR = {'p': 'PSH-800-600-300 IP65', 'c': 'WEG', 'o': 'ZO-KP-1/2026', 'q': 6}


# ── Szablony produktów ────────────────────────────────────────────────────────

class TestTemplates:
    def test_access(self, client, role_users):
        assert client.get(US + '/produkty').status_code in (302, 401)
        login(client, role_users['order'], 'Rola1234!')
        assert client.get(US + '/produkty').status_code == 403
        assert client.get(US + '/produkty/nowy').status_code == 403

    def test_create_unique_name_and_edit_page(self, client, app):
        login(client, 'oper', 'Oper1234!')
        res = post(client, US + '/produkty/nowy', name='PSH')
        assert 'Utworzono szablon'.encode('utf-8') in res.data
        post(client, US + '/produkty/nowy', name=' psh ')     # duplikat bez względu na wielkość liter
        post(client, US + '/produkty/nowy', name='  ')
        with app.app_context():
            assert ProductTemplate.query.count() == 1
        page = client.get(US + '/produkty/nowy?name=PSH-800')
        assert b'PSH-800' in page.data
        assert 'PSH'.encode('utf-8') in client.get(US + '/produkty').data

    def test_template_has_no_per_unit_quantity(self, client, app):
        tid = make_template(app, elements=['Drzwi'])
        login(client, 'oper', 'Oper1234!')
        page = client.get(f'{US}/produkty/{tid}')
        assert page.status_code == 200
        assert b'name="per_unit"' not in page.data
        assert not hasattr(ProductComponent, 'per_unit')

    def test_components_with_route_only_element_stages(self, client, app, stages):
        tid = make_template(app, elements=[])
        login(client, 'oper', 'Oper1234!')
        post(client, f'{US}/produkty/{tid}/element/nowy', name='Drzwi',
             stage_ids=[stages[1], stages[0], stages[2]])   # kolejność i etap produktowy do odrzucenia
        post(client, f'{US}/produkty/{tid}/element/nowy', name='')
        with app.app_context():
            comps = ProductComponent.query.filter_by(template_id=tid).all()
            assert [c.name for c in comps] == ['Drzwi']
            assert [s.name for s in comps[0].stages] == ['T2 Wycinanie', 'T2 Gięcie']   # wg kolejności etapów
        page = client.get(f'{US}/produkty/{tid}')
        assert page.status_code == 200 and b'Drzwi' in page.data

    def test_save_move_delete_component(self, client, app, stages):
        tid = make_template(app, elements=['A', 'B'], stage_ids=[stages[0]])
        with app.app_context():
            ids = [c.id for c in ProductComponent.query.filter_by(template_id=tid).order_by(ProductComponent.position)]
        login(client, 'oper', 'Oper1234!')
        post(client, f'{US}/produkty/element/{ids[0]}/zapisz', name='A2', stage_ids=[stages[1]])
        with app.app_context():
            c = db.session.get(ProductComponent, ids[0])
            assert (c.name, [s.name for s in c.stages]) == ('A2', ['T2 Gięcie'])
        post(client, f'{US}/produkty/element/{ids[1]}/przesun/up')
        with app.app_context():
            assert [c.name for c in db.session.get(ProductTemplate, tid).components] == ['B', 'A2']
        post(client, f'{US}/produkty/element/{ids[0]}/usun')
        with app.app_context():
            assert [c.name for c in db.session.get(ProductTemplate, tid).components] == ['B']

    def test_copy_and_delete_keep_orders(self, client, app, stages):
        tid = make_template(app, stage_ids=[stages[0]])
        login(client, 'oper', 'Oper1234!')
        new_order(client, **QR)
        post(client, f'{US}/produkty/{tid}/kopiuj')
        with app.app_context():
            copy = ProductTemplate.query.filter(ProductTemplate.name.like('PSH (kopia)%')).one()
            assert copy.is_active is False and len(copy.components) == 2
            assert all(c.stage_ids == {stages[0]} for c in copy.components)
        post(client, f'{US}/produkty/{tid}/usun')
        with app.app_context():
            assert db.session.get(ProductTemplate, tid) is None
            order = WorkOrder.query.one()
            assert order.template_id is None and len(order.components) == 2

    def test_stage_in_use_cannot_be_deleted(self, client, app, stages):
        make_template(app, stage_ids=[stages[0]])
        login(client, 'oper', 'Oper1234!')
        res = post(client, f'{US}/etapy/{stages[0]}/usun')
        assert 'jest użyty w trasie'.encode('utf-8') in res.data
        with app.app_context():
            assert db.session.get(ProductionStage, stages[0]) is not None


# ── Dopasowanie szablonu ──────────────────────────────────────────────────────

class TestMatching:
    def test_best_template_by_tokens(self, app):
        make_template(app, 'PSH')
        make_template(app, 'PSH INOX')
        make_template(app, 'RU-L')
        make_template(app, 'ZE-ZBYT', elements=[])          # bez elementów
        make_template(app, 'NIEAKTYWNY', active=False)
        with app.app_context():
            find = lambda p: (service.find_template(p).name if service.find_template(p) else None)
            assert find('PSH-800-600-300 IP65') == 'PSH'
            assert find('PSH-1200-1000-300 IP55 INOX') == 'PSH INOX'
            assert find('RU-L IP54') == 'RU-L'
            assert find('ZE-ZBYT 100') is None
            assert find('NIEAKTYWNY 1') is None
            assert find('ABC-123') is None
            assert find('') is None


# ── Skan kodu QR zlecenia ─────────────────────────────────────────────────────

class TestScan:
    def test_only_controllers_can_scan(self, client, role_users):
        assert client.get(KP + '/skan').status_code in (302, 401)
        for role in ('operator', 'order', 'monter'):
            client.get('/logout')
            login(client, role_users[role], 'Rola1234!')
            assert client.get(KP + '/skan').status_code == 403
            assert scan(client, **QR).status_code == 403
            assert create(client, {'1': 1}, **QR).status_code == 403
        client.get('/logout')
        login(client, 'oper', 'Oper1234!')
        assert client.get(KP + '/skan').status_code == 200

    def test_scan_asks_for_quantities_and_does_not_create_order(self, client, app, stages):
        make_template(app, 'PSH', elements=['Drzwi', 'Korpus'], stage_ids=[stages[0]])
        login(client, 'oper', 'Oper1234!')
        body = scan(client, **QR).get_json()
        assert body['ok'] and body['needs_quantities'] and body['template'] == 'PSH'
        assert [c['name'] for c in body['components']] == ['Drzwi', 'Korpus']
        assert body['order'] == {'p': QR['p'], 'c': 'WEG', 'o': 'ZO-KP-1/2026', 'q': 6}
        with app.app_context():
            assert WorkOrder.query.count() == 0

    def test_creates_order_with_quantities_given_at_order(self, client, app, stages):
        make_template(app, 'PSH', elements=['Drzwi', 'Korpus'], stage_ids=[stages[0], stages[1]])
        login(client, 'oper', 'Oper1234!')
        res = new_order(client, {'Drzwi': 4, 'Korpus': 1}, **QR)   # dwie pary drzwi na sztukę
        assert res.status_code == 200
        body = res.get_json()
        assert body['ok'] and body['template'] == 'PSH' and '/kontrolaprodukcji/' in body['redirect']
        with app.app_context():
            o = WorkOrder.query.one()
            assert (o.zo_number, o.product_name, o.client, o.quantity, o.status) == (
                'ZO-KP-1/2026', 'PSH-800-600-300 IP65', 'WEG', 6, 'active')
            comps = {c.name: c for c in o.components}
            assert (comps['Drzwi'].per_unit, comps['Drzwi'].required_qty) == (4, 24)
            assert (comps['Korpus'].per_unit, comps['Korpus'].required_qty) == (1, 6)
            assert [s.name for s in comps['Drzwi'].stages] == ['T2 Wycinanie', 'T2 Gięcie']

    def test_same_template_different_quantities_per_order(self, client, app):
        make_template(app, 'PSH')
        login(client, 'oper', 'Oper1234!')
        new_order(client, {'Drzwi': 1, 'Korpus': 1}, **QR)
        new_order(client, {'Drzwi': 2, 'Korpus': 1}, **{**QR, 'p': 'PSH-600-800-300 IP65', 'q': 3})
        with app.app_context():
            req = {(o.quantity, c.name): c.required_qty for o in WorkOrder.query for c in o.components}
            assert req == {(6, 'Drzwi'): 6, (6, 'Korpus'): 6, (3, 'Drzwi'): 6, (3, 'Korpus'): 3}

    def test_zero_excludes_element(self, client, app):
        make_template(app, 'PSH', elements=['Drzwi', 'Korpus', 'Plecy'])
        login(client, 'oper', 'Oper1234!')
        new_order(client, {'Drzwi': 2, 'Korpus': 1, 'Plecy': 0}, **QR)
        with app.app_context():
            assert sorted(c.name for c in WorkOrder.query.one().components) == ['Drzwi', 'Korpus']

    def test_quantities_validation(self, client, app):
        make_template(app, 'PSH')
        login(client, 'oper', 'Oper1234!')
        ids = {c['name']: str(c['id']) for c in scan(client, **QR).get_json()['components']}
        bad = [
            {},                                                      # brak ilości
            {ids['Drzwi']: 2},                                       # brak ilości dla Korpusu
            {ids['Drzwi']: '', ids['Korpus']: 1},                    # puste pole
            {ids['Drzwi']: 'abc', ids['Korpus']: 1},                 # nie liczba
            {ids['Drzwi']: -1, ids['Korpus']: 1},                    # ujemna
            {ids['Drzwi']: 10000, ids['Korpus']: 1},                 # ponad limit
            {ids['Drzwi']: 0, ids['Korpus']: 0},                     # wszystko 0
        ]
        for per_unit in bad:
            res = client.post(KP + '/utworz', json={**QR, 'per_unit': per_unit},
                              headers={'X-CSRF-Token': _csrf(client)})
            assert res.status_code == 400, per_unit
            assert res.get_json()['error']
        with app.app_context():
            assert WorkOrder.query.count() == 0

    def test_double_submit_does_not_duplicate(self, client, app):
        make_template(app, 'PSH')
        login(client, 'oper', 'Oper1234!')
        first = new_order(client, **QR).get_json()
        ids = {c['name']: str(c['id']) for c in client.post(
            KP + '/z-qr', json={**QR, 'p': 'PSH-1 IP65', 'o': 'INNE'},
            headers={'X-CSRF-Token': _csrf(client)}).get_json()['components']}
        second = client.post(KP + '/utworz', json={**QR, 'per_unit': {ids['Drzwi']: 1, ids['Korpus']: 1}},
                             headers={'X-CSRF-Token': _csrf(client)}).get_json()
        assert second['existing'] and second['redirect'] == first['redirect']
        with app.app_context():
            assert WorkOrder.query.count() == 1

    def test_rescan_opens_existing_and_other_product_creates_new(self, client, app):
        make_template(app, 'PSH')
        login(client, 'oper', 'Oper1234!')
        first = new_order(client, **QR).get_json()
        again = scan(client, **{**QR, 'p': '  psh-800-600-300   ip65 ', 'o': 'ZO-KP-1/2026'}).get_json()
        assert again['existing'] and again['redirect'] == first['redirect'] and 'już istnieje' in again['msg']
        other = new_order(client, **{**QR, 'p': 'PSH-600-800-300 IP65', 'q': 3}).get_json()
        assert other['redirect'] != first['redirect'] and not other.get('existing')
        with app.app_context():
            assert WorkOrder.query.count() == 2
            assert sorted(o.quantity for o in WorkOrder.query) == [3, 6]

    def test_no_template_does_not_create_order(self, client, app):
        make_template(app, 'PUSTY', elements=[])
        login(client, 'oper', 'Oper1234!')
        for product in ('XYZ-123 IP65', 'PUSTY 800'):
            res = scan(client, **{**QR, 'p': product})
            assert res.status_code == 404
            body = res.get_json()
            assert body['no_template'] and 'szablonu' in body['error']
            assert '/produkty/nowy?name=' in body['create_url']
            assert create(client, {'1': 1}, **{**QR, 'p': product}).status_code == 404
        with app.app_context():
            assert WorkOrder.query.count() == 0

    def test_validation_and_quantity_clamp(self, client, app):
        make_template(app, 'PSH')
        login(client, 'oper', 'Oper1234!')
        assert scan(client, p='PSH 1', q=1).status_code == 400            # brak ZO
        assert scan(client, o='ZO-X', q=1).status_code == 400             # brak produktu
        new_order(client, p='PSH 1', o='ZO-Q1', q=0)
        new_order(client, p='PSH 2', o='ZO-Q2', q='abc')
        new_order(client, p='PSH 3', o='ZO-Q3', q=100000)
        new_order(client, p='PSH 4', o='ZO-Q4?x=1', q=2)                   # doklejony query string
        with app.app_context():
            q = {o.zo_number: o.quantity for o in WorkOrder.query}
            assert q == {'ZO-Q1': 1, 'ZO-Q2': 1, 'ZO-Q3': 9999, 'ZO-Q4': 2}

    def test_cancelled_order_is_reported_not_recreated(self, client, app):
        make_template(app, 'PSH')
        login(client, 'oper', 'Oper1234!')
        first = new_order(client, **QR).get_json()
        with app.app_context():
            o = WorkOrder.query.one()
            o.status = 'cancelled'
            db.session.commit()
        again = scan(client, **QR).get_json()
        assert again['redirect'] == first['redirect'] and 'anulowane' in again['msg']
        with app.app_context():
            assert WorkOrder.query.count() == 1

    def test_scan_page_has_quantity_step(self, client):
        login(client, 'oper', 'Oper1234!')
        html = client.get(KP + '/skan').get_data(as_text=True)
        assert 'Ilość elementów na sztukę produktu' in html and '/kontrolaprodukcji/utworz' in html


# ── Widoki (odczyt dla wszystkich) ────────────────────────────────────────────

class TestViews:
    def _order(self, client, app):
        make_template(app, 'PSH')
        login(client, 'oper', 'Oper1234!')
        new_order(client, **QR)
        client.get('/logout')
        with app.app_context():
            return WorkOrder.query.one().id

    def test_everyone_logged_in_can_read(self, client, app, role_users):
        oid = self._order(client, app)
        assert client.get(KP + '/').status_code in (302, 401)
        for username in ['oper', 'admin'] + list(role_users.values()):
            pw = 'Oper1234!' if username == 'oper' else 'Admin1234!' if username == 'admin' else 'Rola1234!'
            client.get('/logout')
            login(client, username, pw)
            lst = client.get(KP + '/')
            det = client.get(f'{KP}/{oid}')
            assert lst.status_code == 200 and b'ZO-KP-1/2026' in lst.data, username
            assert det.status_code == 200 and b'Drzwi' in det.data, username

    def test_edit_controls_by_role(self, client, app, role_users):
        oid = self._order(client, app)
        login(client, role_users['operator'], 'Rola1234!')
        page = client.get(f'{KP}/{oid}').get_data(as_text=True)
        assert 'Dodaj element' not in page and 'Anuluj zlecenie' not in page
        client.get('/logout')
        login(client, 'oper', 'Oper1234!')           # kontroler: elementy tak, anulowanie nie
        page = client.get(f'{KP}/{oid}').get_data(as_text=True)
        assert 'Dodaj element' in page and 'Edytuj element' in page
        assert 'Anuluj zlecenie' not in page and 'Zmień ilość' not in page
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        page = client.get(f'{KP}/{oid}').get_data(as_text=True)
        assert 'Anuluj zlecenie' in page and 'Zmień ilość' in page and 'Dodaj element' in page

    def test_search_and_status_filter(self, client, app):
        self._order(client, app)
        login(client, 'oper', 'Oper1234!')
        assert b'ZO-KP-1/2026' in client.get(KP + '/?q=psh-800').data
        assert b'ZO-KP-1/2026' in client.get(KP + '/?q=weg').data
        assert b'ZO-KP-1/2026' not in client.get(KP + '/?q=nie-istnieje').data
        assert b'ZO-KP-1/2026' not in client.get(KP + '/?s=cancelled').data
        assert b'ZO-KP-1/2026' in client.get(KP + '/?s=all').data

    def test_kp_alias_redirects(self, client):
        login(client, 'oper', 'Oper1234!')
        for url in ('/kp', '/kp/'):
            res = client.get(url)
            assert res.status_code == 302 and res.headers['Location'].endswith('/kontrolaprodukcji/')


# ── Korekty zlecenia ──────────────────────────────────────────────────────────

class TestEdits:
    def _order(self, client, app, stages):
        make_template(app, 'PSH', elements=['Drzwi', 'Korpus'], stage_ids=[stages[0]])
        login(client, 'oper', 'Oper1234!')
        new_order(client, {'Drzwi': 2, 'Korpus': 1}, **QR)
        client.get('/logout')
        with app.app_context():
            o = WorkOrder.query.one()
            return o.id, [c.id for c in o.components]

    def test_kontroler_cannot_change_order_quantity_or_cancel(self, client, app, stages):
        oid, _ = self._order(client, app, stages)
        login(client, 'oper', 'Oper1234!')
        for url, data in ((f'{KP}/{oid}/ilosc', {'quantity': 9}), (f'{KP}/{oid}/anuluj', {}),
                          (f'{KP}/{oid}/przywroc', {})):
            assert post(client, url, **data).status_code == 403, url
        with app.app_context():
            assert db.session.get(WorkOrder, oid).quantity == 6

    def test_operator_cannot_edit_anything(self, client, app, stages, role_users):
        oid, cids = self._order(client, app, stages)
        login(client, role_users['operator'], 'Rola1234!')
        for url, data in ((f'{KP}/{oid}/ilosc', {'quantity': 9}), (f'{KP}/{oid}/anuluj', {}),
                          (f'{KP}/{oid}/element/nowy', {'name': 'X', 'per_unit': 1}),
                          (f'{KP}/element/{cids[0]}/zapisz', {'name': 'X', 'per_unit': 1, 'required_qty': 1}),
                          (f'{KP}/element/{cids[0]}/usun', {})):
            assert post(client, url, **data).status_code == 403, url

    def test_quantity_recalculates_required(self, client, app, stages):
        oid, _ = self._order(client, app, stages)
        login(client, 'admin', 'Admin1234!')
        post(client, f'{KP}/{oid}/ilosc', quantity='10')
        post(client, f'{KP}/{oid}/ilosc', quantity='0')            # odrzucone
        with app.app_context():
            o = db.session.get(WorkOrder, oid)
            assert o.quantity == 10
            assert {c.name: c.required_qty for c in o.components} == {'Drzwi': 20, 'Korpus': 10}

    def test_cancel_restore_and_block_edit_when_cancelled(self, client, app, stages):
        oid, cids = self._order(client, app, stages)
        login(client, 'admin', 'Admin1234!')
        post(client, f'{KP}/{oid}/anuluj')
        with app.app_context():
            o = db.session.get(WorkOrder, oid)
            assert o.status == 'cancelled' and o.cancelled_at is not None
        res = post(client, f'{KP}/{oid}/ilosc', quantity='3')
        assert 'anulowane'.encode('utf-8') in res.data
        with app.app_context():
            assert db.session.get(WorkOrder, oid).quantity == 6
        post(client, f'{KP}/{oid}/przywroc')
        with app.app_context():
            o = db.session.get(WorkOrder, oid)
            assert o.status == 'active' and o.cancelled_at is None

    @pytest.mark.parametrize('username,password', [('oper', 'Oper1234!'), ('admin', 'Admin1234!')])
    def test_kontroler_and_admin_edit_components(self, client, app, stages, username, password):
        oid, cids = self._order(client, app, stages)
        login(client, username, password)
        post(client, f'{KP}/{oid}/element/nowy', name='Plecy', per_unit='2', stage_ids=[stages[1], stages[2]])
        with app.app_context():
            c = WorkOrderComponent.query.filter_by(name='Plecy').one()
            assert c.required_qty == 12                                      # 2 na sztukę razy 6
            assert [s.name for s in c.stages] == ['T2 Gięcie']               # etap produktowy odrzucony
            new_id = c.id
        post(client, f'{KP}/element/{new_id}/zapisz', name='Plecy 2', per_unit='2', required_qty='7',
             stage_ids=[stages[0]])
        with app.app_context():
            c = db.session.get(WorkOrderComponent, new_id)
            assert (c.name, c.per_unit, c.required_qty, [s.name for s in c.stages]) == (
                'Plecy 2', 2, 7, ['T2 Wycinanie'])
        post(client, f'{KP}/element/{new_id}/zapisz', name='', per_unit='1', required_qty='1')
        post(client, f'{KP}/element/{new_id}/zapisz', name='X', per_unit='0', required_qty='1')
        with app.app_context():
            assert db.session.get(WorkOrderComponent, new_id).name == 'Plecy 2'
        for cid in [new_id] + cids:
            post(client, f'{KP}/element/{cid}/usun')
        with app.app_context():
            assert WorkOrderComponent.query.count() == 1                     # ostatniego nie da się usunąć

    def test_template_change_does_not_alter_existing_order(self, client, app, stages):
        oid, _ = self._order(client, app, stages)
        with app.app_context():
            tpl = ProductTemplate.query.one()
            tpl.components[0].name = 'Zmienione'
            db.session.commit()
            names = sorted(c.name for c in db.session.get(WorkOrder, oid).components)
            assert names == ['Drzwi', 'Korpus']

    def test_legacy_migration_drops_per_unit_column(self, app):
        """Lokalne bazy z etapu 2 miały kolumnę per_unit w szablonach, migracja ją usuwa."""
        from sqlalchemy import inspect, text
        from app import _migrate_schema
        with app.app_context():
            db.session.execute(text('ALTER TABLE product_components ADD COLUMN per_unit INTEGER DEFAULT 1'))
            db.session.commit()
            assert 'per_unit' in [c['name'] for c in inspect(db.engine).get_columns('product_components')]
            _migrate_schema()
            assert 'per_unit' not in [c['name'] for c in inspect(db.engine).get_columns('product_components')]


# ── Skrypty stron ─────────────────────────────────────────────────────────────

class TestPageScripts:
    """Powtórzona deklaracja `var` w skrypcie strony po cichu nadpisuje zmienną (tak zepsuł się
    skan zlecenia: druga `elQty` zastąpiła pierwszą). Test pilnuje głównego poziomu skryptów."""

    @pytest.mark.parametrize('url', [KP + '/skan', KP + '/stanowisko'])
    def test_no_duplicate_top_level_vars(self, client, url):
        import re
        login(client, 'admin', 'Admin1234!')
        html = client.get(url).get_data(as_text=True)
        scripts = [s for s in re.findall(r'<script>(.*?)</script>', html, re.S) if len(s) > 500]
        assert scripts
        for script in scripts:
            names = re.findall(r'^  var (\w+)\s*=', script, re.M)
            dups = sorted({n for n in names if names.count(n) > 1})
            assert not dups, f'Zdublowane zmienne w skrypcie {url}: {dups}'
