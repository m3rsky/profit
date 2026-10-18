"""Testy etapu 3: stanowisko wpisu na bufor, stan buforów, historia ruchów i wycofanie wpisu."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import app as flask_app, db
from models import (User, ProductionDepartment, DepartmentEmployee, ProductionStage, Buffer,
                    WorkOrder, WorkOrderComponent, WorkOrderComponentStage, StockPosition, Movement)
from test_app import login, _csrf
from kp import stock

KP = '/kontrolaprodukcji'
US = '/ustawienia-produkcji'


def api(client, path, **payload):
    return client.post(KP + path, json=payload, headers={'X-CSRF-Token': _csrf(client)})


def form_post(client, url, **data):
    data['_csrf_token'] = _csrf(client)
    return client.post(url, data=data, follow_redirects=True)


def scan(client, code):
    return api(client, '/stanowisko/skan', code=code)


def badge(token):
    return 'PSH-DEP:' + token


def buffer_stage(buffer_id):
    """Etap, na który ilość ma czekać w testach: bufory fixture mają pole stage_id tylko jako wskazówkę
    testową (w aplikacji bufor jest zwykłym wózkiem bez etapu)."""
    with flask_app.app_context():
        b = db.session.get(Buffer, buffer_id)
        return b.stage_id if b else None


def enter(client, comp_id, buffer_id, quantity, stage_id='auto'):
    if stage_id == 'auto':
        stage_id = buffer_stage(buffer_id) if isinstance(buffer_id, int) else None
    return api(client, '/stanowisko/wpis', component_id=comp_id, buffer_id=buffer_id, quantity=quantity,
               stage_id=stage_id)


@pytest.fixture(autouse=True)
def _clean(app):
    def wipe():
        with app.app_context():
            Movement.query.delete()
            StockPosition.query.delete()
            WorkOrderComponentStage.query.delete()
            WorkOrderComponent.query.delete()
            WorkOrder.query.filter(WorkOrder.zo_number.like('T3-%')).delete(synchronize_session=False)
            Buffer.query.filter(Buffer.code.like('T3%')).delete(synchronize_session=False)
            ProductionStage.query.filter(ProductionStage.name.like('T3 %')).delete(synchronize_session=False)
            DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T3 %')).delete(
                synchronize_session=False)
            ProductionDepartment.query.filter(ProductionDepartment.name.like('T3 %')).delete(
                synchronize_session=False)
            db.session.commit()
    wipe()
    yield
    wipe()


@pytest.fixture
def users(app):
    spec = {'station': ('kp3_station', 'produkcja'), 'operator': ('kp3_operator', 'operator'),
            'order': ('kp3_order', 'order'), 'monter': ('kp3_monter', 'monter'),
            'konstruktor': ('kp3_konstruktor', 'konstruktor')}
    with app.app_context():
        for _, (name, role) in spec.items():
            if not User.query.filter_by(username=name).first():
                u = User(username=name, email=f'{name}@test.pl', role=role)
                u.set_password('Rola1234!')
                db.session.add(u)
        db.session.commit()
    return {k: v[0] for k, v in spec.items()}


@pytest.fixture
def world(app):
    """Etapy T3 Wycinanie (A, element), T3 Gięcie (B, element), T3 Składanie (C, produkt); bufory;
    zlecenie T3-ZO-1 (q=6) z elementami Drzwi (12, trasa A+B), Korpus (6, trasa B), Plecy (6, bez trasy);
    dwóch pracowników."""
    with app.app_context():
        dept = ProductionDepartment(name='T3 Dział', order=95)
        db.session.add(dept)
        db.session.flush()
        sa = ProductionStage(name='T3 Wycinanie', order=911, level='element', department_id=dept.id)
        sb = ProductionStage(name='T3 Gięcie', order=912, level='element', department_id=dept.id)
        sc = ProductionStage(name='T3 Składanie', order=913, level='produkt', department_id=dept.id)
        db.session.add_all([sa, sb, sc])
        db.session.flush()
        bufs = {
            'A1': Buffer(code='T3A1', stage_id=sa.id, name='Przy laserze'),
            'A2': Buffer(code='T3A2', stage_id=sa.id),
            'B1': Buffer(code='T3B1', stage_id=sb.id),
            'C1': Buffer(code='T3C1', stage_id=sc.id),
            'X': Buffer(code='T3X'),
            'OFF': Buffer(code='T3OFF', stage_id=sa.id, is_active=False),
        }
        db.session.add_all(bufs.values())
        order = WorkOrder(zo_number='T3-ZO-1', product_name='PSH-T3 IP65', client='WEG', quantity=6,
                          created_by_id=User.query.filter_by(username='admin').one().id)
        db.session.add(order)
        db.session.flush()
        comps = {}
        for i, (name, per, req, route) in enumerate([('Drzwi', 2, 12, [sa.id, sb.id]),
                                                     ('Korpus', 1, 6, [sb.id]),
                                                     ('Plecy', 1, 6, [])], 1):
            c = WorkOrderComponent(order_id=order.id, name=name, per_unit=per, required_qty=req, position=i)
            db.session.add(c)
            db.session.flush()
            for sid in route:
                db.session.add(WorkOrderComponentStage(component_id=c.id, stage_id=sid))
            comps[name] = c
        emps = {'e1': DepartmentEmployee(department_id=dept.id, name='T3 Jan Kowalski'),
                'e2': DepartmentEmployee(department_id=dept.id, name='T3 Anna Nowak')}
        db.session.add_all(emps.values())
        db.session.commit()
        return {
            'order': order.id, 'dept': dept.id,
            'stage': {'A': sa.id, 'B': sb.id, 'C': sc.id},
            'buf': {k: b.id for k, b in bufs.items()},
            'comp': {k: c.id for k, c in comps.items()},
            'emp': {k: e.id for k, e in emps.items()},
            'token': {k: e.token for k, e in emps.items()},
        }


def station_login(client, users, emp_token=None):
    login(client, users['station'], 'Rola1234!')
    if emp_token:
        assert scan(client, badge(emp_token)).status_code == 200


# ── Wpis na bufor ─────────────────────────────────────────────────────────────

class TestEntry:
    def test_enter_updates_position_movement_and_remaining(self, client, app, users, world):
        station_login(client, users, world['token']['e1'])
        res = enter(client, world['comp']['Drzwi'], world['buf']['A1'], 5)
        assert res.status_code == 200
        body = res.get_json()
        assert body['ok'] and '5 szt.' in body['msg'] and 'T3A1' in body['msg']
        drzwi = next(c for c in body['order']['components'] if c['name'] == 'Drzwi')
        assert (drzwi['required'], drzwi['entered'], drzwi['remaining']) == (12, 5, 7)
        assert drzwi['positions'] == [{'buffer_id': world['buf']['A1'], 'code': 'T3A1',
                                       'name': 'Przy laserze', 'stage': 'T3 Wycinanie',
                                       'stage_id': world['stage']['A'], 'machines': [],
                                       'quantity': 5, 'status': 'ok', 'status_label': 'gotowe'}]
        assert body['buffer']['total'] == 5 and body['recent'][0]['quantity'] == 5
        with app.app_context():
            mv = Movement.query.one()
            assert (mv.kind, mv.quantity, mv.to_buffer_id, mv.employee_id) == (
                'wpis', 5, world['buf']['A1'], world['emp']['e1'])
            assert mv.user.username == users['station']

    def test_parts_on_different_buffers_and_full_settlement(self, client, app, users, world):
        station_login(client, users, world['token']['e1'])
        c = world['comp']['Drzwi']
        assert enter(client, c, world['buf']['A1'], 4).status_code == 200
        assert enter(client, c, world['buf']['A2'], 3).status_code == 200
        assert enter(client, c, world['buf']['A1'], 5).status_code == 200      # dopisuje do pozycji
        res = enter(client, c, world['buf']['A1'], 1)
        assert res.status_code == 400 and 'w całości' in res.get_json()['error']
        with app.app_context():
            assert StockPosition.query.filter_by(component_id=c, buffer_id=world['buf']['A1']).one().quantity == 9
            assert stock.entered_qty(db.session.get(WorkOrderComponent, c)) == 12

    def test_cannot_exceed_remaining(self, client, app, users, world):
        station_login(client, users, world['token']['e1'])
        c = world['comp']['Korpus']
        enter(client, c, world['buf']['B1'], 4)
        res = enter(client, c, world['buf']['B1'], 3)
        assert res.status_code == 400 and 'jeszcze 2 szt.' in res.get_json()['error']
        assert enter(client, c, world['buf']['B1'], 2).status_code == 200

    @pytest.mark.parametrize('qty', [0, -1, 'abc', 1.5, True, None, ''])
    def test_invalid_quantities(self, client, app, users, world, qty):
        station_login(client, users, world['token']['e1'])
        res = enter(client, world['comp']['Drzwi'], world['buf']['A1'], qty)
        assert res.status_code == 400
        with app.app_context():
            assert Movement.query.count() == 0

    def test_quantity_given_as_numeric_string_is_accepted(self, client, users, world):
        station_login(client, users, world['token']['e1'])
        assert enter(client, world['comp']['Drzwi'], world['buf']['A1'], '3').status_code == 200

    def test_buffer_stage_must_be_in_element_route(self, client, app, users, world):
        station_login(client, users, world['token']['e1'])
        # Korpus ma tylko etap Gięcie: bufor Wycinania odrzucony, bufor składania (pierwszy etap
        # poziomu produktu) dozwolony, bo element może być już gotowy do złożenia
        res = enter(client, world['comp']['Korpus'], world['buf']['A1'], 1)
        assert res.status_code == 400 and 'trasie elementu' in res.get_json()['error']
        assert enter(client, world['comp']['Korpus'], world['buf']['C1'], 1).status_code == 200
        # Drzwi mogą na dowolny etap swojej trasy (Wycinanie albo Gięcie)
        assert enter(client, world['comp']['Drzwi'], world['buf']['B1'], 2).status_code == 200
        assert enter(client, world['comp']['Drzwi'], world['buf']['A1'], 2).status_code == 200

    def test_rejects_bad_buffers_orders_and_routes(self, client, app, users, world):
        station_login(client, users, world['token']['e1'])
        d = world['comp']['Drzwi']
        assert 'nieaktywny' in enter(client, d, world['buf']['OFF'], 1).get_json()['error']
        assert enter(client, d, world['buf']['X'], 1).status_code == 200      # bufor bez etapu to zwykły wózek
        assert 'nie istnieje' in enter(client, d, 999999, 1).get_json()['error']
        assert enter(client, 999999, world['buf']['A1'], 1).status_code == 404
        assert 'nie ma etapów w trasie' in enter(client, world['comp']['Plecy'], world['buf']['A1'], 1).get_json()['error']
        with app.app_context():
            db.session.get(WorkOrder, world['order']).status = 'cancelled'
            db.session.commit()
        assert 'anulowane' in enter(client, d, world['buf']['A1'], 1).get_json()['error']
        with app.app_context():
            assert Movement.query.count() == 1

    def test_inactive_stage_rejected(self, client, app, users, world):
        station_login(client, users, world['token']['e1'])
        with app.app_context():
            db.session.get(ProductionStage, world['stage']['A']).is_active = False
            db.session.commit()
        res = enter(client, world['comp']['Drzwi'], world['buf']['A1'], 1)
        assert res.status_code == 400 and 'trasie elementu' in res.get_json()['error']


# ── Pracownik (plakietka) ─────────────────────────────────────────────────────

class TestEmployee:
    def test_station_and_operator_need_badge(self, client, users, world):
        for role in ('station', 'operator'):
            client.get('/logout')
            login(client, users[role], 'Rola1234!')
            res = enter(client, world['comp']['Drzwi'], world['buf']['A1'], 1)
            assert res.status_code == 401 and res.get_json()['need_employee'], role

    def test_kontroler_and_admin_can_enter_without_badge(self, client, app, world):
        for user, pw in (('oper', 'Oper1234!'), ('admin', 'Admin1234!')):
            client.get('/logout')
            login(client, user, pw)
            assert enter(client, world['comp']['Korpus'], world['buf']['B1'], 1).status_code == 200
        with app.app_context():
            assert Movement.query.filter(Movement.employee_id.is_(None)).count() == 2

    def test_badge_scan_sets_session_and_logout_clears_it(self, client, users, world):
        login(client, users['operator'], 'Rola1234!')
        res = scan(client, badge(world['token']['e1']))
        body = res.get_json()
        assert body['type'] == 'employee' and body['employee']['name'] == 'T3 Jan Kowalski'
        assert body['employee']['department'] == 'T3 Dział'
        assert enter(client, world['comp']['Drzwi'], world['buf']['A1'], 1).status_code == 200
        api(client, '/stanowisko/wyloguj')
        assert enter(client, world['comp']['Drzwi'], world['buf']['A1'], 1).status_code == 401

    def test_unknown_and_inactive_badge(self, client, app, users, world):
        login(client, users['operator'], 'Rola1234!')
        assert scan(client, badge('nieznany')).status_code == 404
        with app.app_context():
            db.session.get(DepartmentEmployee, world['emp']['e1']).is_active = False
            db.session.commit()
        assert scan(client, badge(world['token']['e1'])).status_code == 404

    def test_session_expires_after_12_hours(self, client, users, world):
        station_login(client, users, world['token']['e1'])
        with client.session_transaction() as sess:
            sess['kp_employee'] = {**sess['kp_employee'],
                                   'at': (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()}
        assert enter(client, world['comp']['Drzwi'], world['buf']['A1'], 1).status_code == 401

    def test_deactivated_employee_session_is_dropped(self, client, app, users, world):
        station_login(client, users, world['token']['e1'])
        with app.app_context():
            db.session.get(DepartmentEmployee, world['emp']['e1']).is_active = False
            db.session.commit()
        assert enter(client, world['comp']['Drzwi'], world['buf']['A1'], 1).status_code == 401


# ── Rozpoznawanie kodów ───────────────────────────────────────────────────────

class TestScanRecognition:
    def test_buffer_code_normalized(self, client, users, world):
        station_login(client, users)
        for code in ('T3A1', 't3a1', ' t3 a1 '):
            body = scan(client, code).get_json()
            assert body['type'] == 'buffer' and body['buffer']['code'] == 'T3A1', code
        assert 'stage' not in body['buffer']
        res = scan(client, 'T3NIEMA')
        assert res.status_code == 404 and 'Nie znaleziono bufora' in res.get_json()['error']

    def test_order_qr(self, client, users, world):
        station_login(client, users)
        payload = {'p': ' psh-t3   ip65 ', 'c': 'WEG', 'o': 'T3-ZO-1?x=1', 'q': 6}
        body = scan(client, json.dumps(payload)).get_json()
        assert body['type'] == 'order' and body['order']['zo'] == 'T3-ZO-1'
        assert [c['name'] for c in body['order']['components']] == ['Drzwi', 'Korpus', 'Plecy']
        assert body['order']['components'][0]['route'] == [
            {'id': world['stage']['A'], 'name': 'T3 Wycinanie'}, {'id': world['stage']['B'], 'name': 'T3 Gięcie'}]
        res = scan(client, json.dumps({**payload, 'o': 'T3-ZO-NIE'}))
        assert res.status_code == 404 and res.get_json()['unknown_order']
        assert scan(client, '{"p": "x"').status_code == 400
        assert scan(client, json.dumps({'p': 'x'})).status_code == 400
        assert scan(client, '').status_code == 400

    def test_access_by_role(self, client, users, world):
        assert client.get(KP + '/stanowisko').status_code in (302, 401)
        for role in ('order', 'monter', 'konstruktor'):
            client.get('/logout')
            login(client, users[role], 'Rola1234!')
            assert client.get(KP + '/stanowisko').status_code == 403
            assert scan(client, 'T3A1').status_code == 403
            assert enter(client, world['comp']['Drzwi'], world['buf']['A1'], 1).status_code == 403
        for role in ('station', 'operator'):
            client.get('/logout')
            login(client, users[role], 'Rola1234!')
            assert client.get(KP + '/stanowisko').status_code == 200

    def test_station_role_lands_on_new_station(self, client, users):
        login(client, users['station'], 'Rola1234!')
        res = client.get('/')
        assert res.status_code == 302 and res.headers['Location'].endswith('/kontrolaprodukcji/stanowisko')


# ── Wycofanie wpisu ───────────────────────────────────────────────────────────

class TestRevert:
    def _enter_as(self, client, users, world, emp_key, qty=4, comp='Drzwi', buf='A1'):
        client.get('/logout')
        station_login(client, users, world['token'][emp_key])
        res = enter(client, world['comp'][comp], world['buf'][buf], qty)
        assert res.status_code == 200
        return res.get_json()['recent'][0]['id']

    def test_worker_reverts_own_entry(self, client, app, users, world):
        mid = self._enter_as(client, users, world, 'e1')
        res = client.post(f'{KP}/ruch/{mid}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        body = res.get_json()
        assert res.status_code == 200 and body['ok'] and 'Wycofano' in body['msg']
        drzwi = next(c for c in body['order']['components'] if c['name'] == 'Drzwi')
        assert (drzwi['entered'], drzwi['remaining'], drzwi['positions']) == (0, 12, [])
        with app.app_context():
            assert StockPosition.query.count() == 0                         # pusta pozycja usunięta
            orig, rev = Movement.query.order_by(Movement.id).all()
            assert orig.reverted_at is not None and (rev.kind, rev.reverts_id) == ('cofniecie', orig.id)
        again = client.post(f'{KP}/ruch/{mid}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert again.status_code == 400 and 'już wycofany' in again.get_json()['error']

    def test_worker_cannot_revert_someone_elses_entry(self, client, app, users, world):
        mid = self._enter_as(client, users, world, 'e1')
        client.get('/logout')
        station_login(client, users, world['token']['e2'])
        res = client.post(f'{KP}/ruch/{mid}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert res.status_code == 400 and 'tylko własny' in res.get_json()['error']
        with app.app_context():
            assert db.session.get(Movement, mid).reverted_at is None

    def test_worker_without_badge_cannot_revert(self, client, users, world):
        mid = self._enter_as(client, users, world, 'e1')
        api(client, '/stanowisko/wyloguj')
        res = client.post(f'{KP}/ruch/{mid}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert res.status_code == 400 and 'plakietk' in res.get_json()['error']

    @pytest.mark.parametrize('user,pw', [('oper', 'Oper1234!'), ('admin', 'Admin1234!')])
    def test_kontroler_and_admin_revert_any_entry(self, client, app, users, world, user, pw):
        mid = self._enter_as(client, users, world, 'e1')
        client.get('/logout')
        login(client, user, pw)
        res = client.post(f'{KP}/ruch/{mid}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert res.status_code == 200
        with app.app_context():
            assert StockPosition.query.count() == 0

    def test_cannot_revert_when_quantity_left_the_buffer(self, client, app, users, world):
        mid = self._enter_as(client, users, world, 'e1', qty=4)
        with app.app_context():                                             # symulacja przeniesienia 2 szt. dalej
            StockPosition.query.one().quantity = 2
            db.session.commit()
        res = client.post(f'{KP}/ruch/{mid}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert res.status_code == 400 and 'nie leży już tam' in res.get_json()['error']

    def test_revert_form_on_order_page(self, client, app, users, world):
        mid = self._enter_as(client, users, world, 'e1')
        client.get('/logout')
        login(client, 'oper', 'Oper1234!')
        page = client.get(f'{KP}/{world["order"]}').get_data(as_text=True)
        assert 'Historia ruchów' in page and f'/ruch/{mid}/cofnij' in page
        res = form_post(client, f'{KP}/ruch/{mid}/cofnij')
        assert 'Wycofano:' in res.get_data(as_text=True)
        page = client.get(f'{KP}/{world["order"]}').get_data(as_text=True)
        assert 'wycofany' in page and f'/ruch/{mid}/cofnij' not in page

    def test_only_entries_can_be_reverted(self, client, app, users, world):
        mid = self._enter_as(client, users, world, 'e1')
        client.post(f'{KP}/ruch/{mid}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        with app.app_context():
            rev_id = Movement.query.filter_by(kind='cofniecie').one().id
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        res = client.post(f'{KP}/ruch/{rev_id}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert res.status_code == 400 and 'nie można wycofać' in res.get_json()['error']


# ── Widoki buforów i zleceń ───────────────────────────────────────────────────

class TestViews:
    def _fill(self, client, users, world):
        station_login(client, users, world['token']['e1'])
        enter(client, world['comp']['Drzwi'], world['buf']['A1'], 5)
        enter(client, world['comp']['Korpus'], world['buf']['B1'], 2)
        client.get('/logout')

    def test_everyone_can_read_buffers_and_order(self, client, app, users, world):
        self._fill(client, users, world)
        assert client.get(KP + '/bufory').status_code in (302, 401)
        who = [('oper', 'Oper1234!'), ('admin', 'Admin1234!')] + [(users[k], 'Rola1234!') for k in
                                                                    ('station', 'operator', 'order', 'monter', 'konstruktor')]
        for name, pw in who:
            client.get('/logout')
            login(client, name, pw)
            lst = client.get(KP + '/bufory')
            det = client.get(f'{KP}/bufory/{world["buf"]["A1"]}')
            order = client.get(f'{KP}/{world["order"]}')
            assert lst.status_code == 200 and b'T3A1' in lst.data, name
            assert det.status_code == 200 and b'T3-ZO-1' in det.data and b'Drzwi' in det.data, name
            assert order.status_code == 200 and b'T3A1' in order.data, name

    def test_buffer_list_totals_filter_and_code_lookup(self, client, app, users, world):
        self._fill(client, users, world)
        login(client, 'oper', 'Oper1234!')
        page = client.get(KP + '/bufory').get_data(as_text=True)
        assert '5 szt.' in page and 'pusty' in page
        assert 'T3B1' in page and 'T3A1' in page
        res = client.get(KP + '/bufory?code=t3 a1')
        assert res.status_code == 302 and res.headers['Location'].endswith(f'/bufory/{world["buf"]["A1"]}')
        assert 'Nie znaleziono bufora' in client.get(KP + '/bufory?code=NIEMA', follow_redirects=True).get_data(as_text=True)

    def test_station_page_has_scan_ui(self, client, users, world):
        station_login(client, users, world['token']['e1'])
        html = client.get(KP + '/stanowisko').get_data(as_text=True)
        assert 'Wpis na bufor' in html and 'T3 Jan Kowalski' in html and '/stanowisko/skan' in html

    def test_menu_has_station_for_entry_roles(self, client, users):
        for role in ('station', 'operator'):
            client.get('/logout')
            login(client, users[role], 'Rola1234!')
            assert 'Wpis na bufor' in client.get(KP + '/bufory').get_data(as_text=True), role


# ── Zabezpieczenia spójności ──────────────────────────────────────────────────

class TestProtections:
    def _filled(self, client, users, world):
        station_login(client, users, world['token']['e1'])
        enter(client, world['comp']['Drzwi'], world['buf']['A1'], 5)
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')

    def test_order_quantity_cannot_drop_below_entered(self, client, app, users, world):
        self._filled(client, users, world)
        order = world['order']
        res = form_post(client, f'{KP}/{order}/ilosc', quantity='2')          # Drzwi: 2*2=4 < 5 wpisanych
        assert 'poniżej już wpisanych' in res.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(WorkOrder, order).quantity == 6
        form_post(client, f'{KP}/{order}/ilosc', quantity='3')                 # 2*3=6 >= 5
        form_post(client, f'{KP}/{order}/ilosc', quantity='8')
        with app.app_context():
            assert db.session.get(WorkOrder, order).quantity == 8

    def test_component_required_and_route_protected(self, client, app, users, world):
        self._filled(client, users, world)
        cid = world['comp']['Drzwi']
        res = form_post(client, f'{KP}/element/{cid}/zapisz', name='Drzwi', per_unit='2', required_qty='4',
                        stage_ids=[world['stage']['A'], world['stage']['B']])
        assert 'mniejsza niż już wpisana' in res.get_data(as_text=True)
        res = form_post(client, f'{KP}/element/{cid}/zapisz', name='Drzwi', per_unit='2', required_qty='12',
                        stage_ids=[world['stage']['B']])                       # usunięcie etapu z buforem z elementem
        assert 'Nie można usunąć z trasy etapu' in res.get_data(as_text=True)
        res = form_post(client, f'{KP}/element/{cid}/zapisz', name='Drzwi dwuskrzydłowe', per_unit='2',
                        required_qty='10', stage_ids=[world['stage']['A'], world['stage']['B']])
        with app.app_context():
            c = db.session.get(WorkOrderComponent, cid)
            assert (c.name, c.required_qty) == ('Drzwi dwuskrzydłowe', 10)

    def test_component_with_history_cannot_be_deleted(self, client, app, users, world):
        self._filled(client, users, world)
        res = form_post(client, f'{KP}/element/{world["comp"]["Drzwi"]}/usun')
        assert 'ma wpisy na bufory' in res.get_data(as_text=True)
        form_post(client, f'{KP}/element/{world["comp"]["Plecy"]}/usun')       # bez historii: można
        with app.app_context():
            assert db.session.get(WorkOrderComponent, world['comp']['Drzwi']) is not None
            assert db.session.get(WorkOrderComponent, world['comp']['Plecy']) is None

    def test_buffer_with_history_or_stock_cannot_be_deleted_or_moved(self, client, app, users, world):
        self._filled(client, users, world)
        bid = world['buf']['A1']
        res = form_post(client, f'{US}/bufory/{bid}/usun')
        assert 'ma historię' in res.get_data(as_text=True)
        form_post(client, f'{US}/bufory/{bid}/zapisz', code='T3A1', name='Nowa nazwa', is_active='1')
        form_post(client, f'{US}/bufory/{world["buf"]["A2"]}/usun')             # pusty bufor: można
        with app.app_context():
            assert db.session.get(Buffer, bid).name == 'Nowa nazwa'
            assert db.session.get(Buffer, world['buf']['A2']) is None

    def test_employee_with_movements_cannot_be_deleted(self, client, app, users, world):
        self._filled(client, users, world)
        res = form_post(client, f'{US}/admin/departments/{world["dept"]}/employees', action='delete',
                        emp_id=world['emp']['e1'])
        assert 'wpisy na bufory' in res.get_data(as_text=True)
        form_post(client, f'{US}/admin/departments/{world["dept"]}/employees', action='delete',
                  emp_id=world['emp']['e2'])
        with app.app_context():
            assert db.session.get(DepartmentEmployee, world['emp']['e1']) is not None
            assert db.session.get(DepartmentEmployee, world['emp']['e2']) is None
