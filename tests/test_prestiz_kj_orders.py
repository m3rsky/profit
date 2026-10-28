"""Testy modułu KJ Prestiż, etap 2: zlecenia demo, trasa i materiały KJ, meldunek ze wstrzymanym RW, oś czasu,
lista wg standardu, ręczne tworzenie i usuwanie, przywracanie scenariusza."""
import os

import pytest

from app import app as flask_app, db
from prestiz_kj import demo_data, service, orders as ord_
from prestiz_kj.models import (PkjOperation, PkjEmployee, PkjOrder, PkjOrderOperation, PkjOrderMaterial,
                               PkjEvent, PkjInspection, PkjResult, PkjPhoto)
from test_app import login, _csrf


@pytest.fixture(autouse=True)
def _clean(app, tmp_path):
    flask_app.config['PKJ_UPLOAD_FOLDER'] = str(tmp_path / 'pkj')
    with app.app_context():
        service.reset_all()
    yield
    with app.app_context():
        service.reset_all()


@pytest.fixture
def seeded(app):
    with app.app_context():
        demo_data.seed_configuration()
        assert demo_data.seed_orders()


def _admin(client):
    login(client, 'admin', 'Admin1234!')
    return client


def _post(client, url, data=None):
    data = dict(data or {})
    data['_csrf_token'] = _csrf(client)
    return client.post(url, data=data, follow_redirects=True)


def _order(number):
    return PkjOrder.query.filter_by(number=number).one()


def _op(order, name):
    return PkjOrderOperation.query.filter_by(order_id=order.id, operation_name=name).one()


def _emp(name='Cezary Lis'):
    return PkjEmployee.query.filter_by(name=name).one()


# ── Dane demo zleceń ──────────────────────────────────────────────────────────

class TestSeedOrders:
    def test_four_orders_and_idempotent(self, app, seeded):
        with app.app_context():
            assert PkjOrder.query.count() == 4
            assert demo_data.seed_orders() is False
            assert PkjOrder.query.count() == 4

    def test_requires_configuration(self, app):
        with app.app_context():
            assert demo_data.seed_orders() is False

    def test_scenario_order_state(self, app, seeded):
        with app.app_context():
            o = _order('ZP-DEMO-001')
            assert o.is_demo_scenario and o.status == 'w_produkcji'
            assert o.qr_key == 'Szafa PSH 600x800x300|Klient demo|ZP-DEMO-001'
            assert [(x.operation_name, x.status) for x in ord_.operations_of(o.id)] == [
                ('Wycinanie', 'zakonczona'), ('Gięcie', 'zakonczona'), ('Spawanie', 'oczekuje'),
                ('Szlifowanie', 'oczekuje'), ('Malowanie', 'oczekuje'), ('Składanie', 'oczekuje')]
            giecie = _op(o, 'Gięcie')
            mats = PkjOrderMaterial.query.filter_by(order_operation_id=giecie.id).all()
            assert len(mats) == 3 and all(m.rw_status == 'wydane' and m.element_status == 'zgodny' for m in mats)

    def test_order_waiting_for_control_holds_rw(self, app, seeded):
        with app.app_context():
            o = _order('ZP-DEMO-004')
            assert o.status == 'do_kontroli'
            held = PkjOrderMaterial.query.filter_by(order_id=o.id, rw_status='wstrzymane').all()
            assert {m.material_name for m in held} == {'Korpus', 'Drzwi', 'Płyta montażowa'}


# ── Tworzenie zlecenia ────────────────────────────────────────────────────────

class TestCreate:
    def test_kj_materials_only_on_kj_operations(self, app):
        with app.app_context():
            demo_data.seed_configuration()
            o = ord_.create_order('ZP-T-1', 'Szafa', 'Klient', 2)
            db.session.commit()
            ops = ord_.operations_of(o.id)
            assert [x.operation_name for x in ops] == ['Wycinanie', 'Gięcie', 'Spawanie', 'Szlifowanie',
                                                       'Malowanie', 'Składanie']
            mats = ord_.materials_of(o.id)
            assert len(mats) == 12 and 'Aparatura' not in {m.material_name for m in mats}
            assert all(m.quantity == 2 and m.rw_status == 'brak' for m in mats)
            assert {m.order_operation_id for m in mats} == {x.id for x in ops if x.kj_required}

    def test_custom_route_and_validation(self, app):
        with app.app_context():
            demo_data.seed_configuration()
            ids = [PkjOperation.query.filter_by(name=n).one().id for n in ('Spawanie', 'Malowanie')]
            o = ord_.create_order('ZP-T-2', 'Szafa', None, 1, ids)
            assert [x.operation_name for x in ord_.operations_of(o.id)] == ['Spawanie', 'Malowanie']
            db.session.commit()
            with pytest.raises(ValueError):
                ord_.create_order('zp-t-2', 'Inna', None, 1)          # numer bez względu na wielkość liter
            with pytest.raises(ValueError):
                ord_.create_order('', 'Szafa', None, 1)
            PkjOperation.query.update({'is_active': False})
            with pytest.raises(ValueError):
                ord_.create_order('ZP-T-3', 'Szafa', None, 1)

    def test_create_via_route(self, client, app):
        with app.app_context():
            demo_data.seed_configuration()
        _admin(client)
        resp = _post(client, '/kj-prestiz/zlecenia/nowe',
                     {'number': 'ZP-T-10', 'product': 'Szafa PSH', 'client': 'K', 'quantity': '3'})
        assert 'Dodano zlecenie ZP-T-10' in resp.get_data(as_text=True)
        resp = _post(client, '/kj-prestiz/zlecenia/nowe', {'number': 'ZP-T-10', 'product': 'Szafa'})
        assert 'już istnieje' in resp.get_data(as_text=True)
        with app.app_context():
            assert PkjOrder.query.filter_by(number='ZP-T-10').count() == 1


# ── Meldunek i RW ─────────────────────────────────────────────────────────────

class TestReport:
    def test_order_of_operations_enforced(self, app, seeded):
        with app.app_context():
            o = _order('ZP-DEMO-002')
            with pytest.raises(ValueError, match='Wycinanie'):
                ord_.report_operation(_op(o, 'Gięcie'), _emp('Adam Wrona'))

    def test_non_kj_operation_releases_rw_immediately(self, app, seeded):
        with app.app_context():
            o = _order('ZP-DEMO-002')
            ord_.report_operation(_op(o, 'Wycinanie'), _emp('Henryk Kruk'), actor='t')
            db.session.commit()
            op = _op(o, 'Wycinanie')
            assert op.status == 'zakonczona' and op.employee_name == 'Henryk Kruk' and op.reported_at
            assert PkjEvent.query.filter_by(order_id=o.id, type='rw_wydane').count() == 1
            assert _order('ZP-DEMO-002').status == 'w_produkcji'

    def test_kj_operation_holds_rw_and_blocks_next(self, app, seeded):
        with app.app_context():
            o = _order('ZP-DEMO-001')
            ord_.report_operation(_op(o, 'Spawanie'), _emp(), actor='t')
            db.session.commit()
            assert _op(o, 'Spawanie').status == 'do_kontroli'
            mats = PkjOrderMaterial.query.filter_by(order_operation_id=_op(o, 'Spawanie').id).all()
            assert len(mats) == 3 and all(m.rw_status == 'wstrzymane' and m.element_status == 'oczekuje'
                                          for m in mats)
            assert _order('ZP-DEMO-001').status == 'do_kontroli'
            assert PkjEvent.query.filter_by(order_id=o.id, type='rw_wstrzymane',
                                        ref_id=_op(o, 'Spawanie').id).count() == 1
            with pytest.raises(ValueError, match='czeka na kontrolę KJ'):
                ord_.report_operation(_op(o, 'Szlifowanie'), _emp('Irena Czajka'))

    def test_double_report_and_bad_employee(self, app, seeded):
        with app.app_context():
            o = _order('ZP-DEMO-002')
            ord_.report_operation(_op(o, 'Wycinanie'), _emp('Henryk Kruk'))
            with pytest.raises(ValueError, match='już zameldowana'):
                ord_.report_operation(_op(o, 'Wycinanie'), _emp('Henryk Kruk'))
            with pytest.raises(ValueError, match='pracownika'):
                ord_.report_operation(_op(o, 'Gięcie'), None)
            emp = _emp('Adam Wrona')
            emp.is_active = False
            with pytest.raises(ValueError, match='pracownika'):
                ord_.report_operation(_op(o, 'Gięcie'), emp)

    def test_timeline_in_order(self, app, seeded):
        with app.app_context():
            o = _order('ZP-DEMO-001')
            ord_.report_operation(_op(o, 'Spawanie'), _emp())
            db.session.commit()
            types = [e.type for e in PkjEvent.query.filter_by(order_id=o.id).order_by(PkjEvent.ts, PkjEvent.id)]
            assert types[0] == 'utworzenie' and types[-2:] == ['meldunek', 'rw_wstrzymane']

    def test_report_via_route(self, client, app, seeded):
        _admin(client)
        with app.app_context():
            o = _order('ZP-DEMO-001')
            oid, oop_id, emp_id = o.id, _op(o, 'Spawanie').id, _emp().id
        html = client.get(f'/kj-prestiz/zlecenia/{oid}').get_data(as_text=True)
        assert 'Melduj operację' in html
        resp = _post(client, f'/kj-prestiz/zlecenia/{oid}/melduj', {'oop_id': oop_id, 'employee_id': emp_id})
        assert 'RW na materiały wstrzymane' in resp.get_data(as_text=True)
        resp = _post(client, f'/kj-prestiz/zlecenia/{oid}/melduj', {'oop_id': oop_id, 'employee_id': emp_id})
        assert 'już zameldowana' in resp.get_data(as_text=True)
        resp = _post(client, f'/kj-prestiz/zlecenia/{oid}/melduj', {'oop_id': oop_id})
        assert resp.status_code == 200

    def test_report_requires_controller(self, client, app, seeded):
        login(client, 'oper', 'Oper1234!')           # kontroler bez uprawnienia do modułu
        with app.app_context():
            oid = _order('ZP-DEMO-001').id
        assert client.post(f'/kj-prestiz/zlecenia/{oid}/melduj',
                           data={'_csrf_token': _csrf(client)}).status_code == 403


# ── Lista i szczegóły ─────────────────────────────────────────────────────────

class TestListAndDetail:
    def test_list_renders_with_standard_markers(self, client, seeded):
        _admin(client)
        html = client.get('/kj-prestiz/zlecenia').get_data(as_text=True)
        for n in ('ZP-DEMO-001', 'ZP-DEMO-002', 'ZP-DEMO-003', 'ZP-DEMO-004'):
            assert n in html
        assert 'class="rs rs-' in html and 'class="badge' not in html.split('<table', 1)[1].split('</table>', 1)[0]
        assert 'Wstrzymane: 3' in html            # ZP-DEMO-004 czeka na kontrolę
        assert 'ls-input' in html                 # wspólne wyszukiwanie list

    def test_search_and_status_filter(self, client, seeded):
        _admin(client)
        html = client.get('/kj-prestiz/zlecenia?q=gamma').get_data(as_text=True)
        assert 'ZP-DEMO-004' in html and 'ZP-DEMO-002' not in html
        html = client.get('/kj-prestiz/zlecenia?s=nowe').get_data(as_text=True)
        assert 'ZP-DEMO-002' in html and 'ZP-DEMO-001' not in html
        html = client.get('/kj-prestiz/zlecenia?s=do_kontroli').get_data(as_text=True)
        assert 'ZP-DEMO-004' in html and 'ZP-DEMO-003' not in html

    def test_date_filter_and_paging(self, client, seeded):
        _admin(client)
        html = client.get('/kj-prestiz/zlecenia?od=2999-01-01').get_data(as_text=True)
        assert 'ZP-DEMO-001' not in html and 'Brak zleceń' in html
        html = client.get('/kj-prestiz/zlecenia?per_page=10&page=9&q=zzz').get_data(as_text=True)
        assert 'Brak zleceń' in html
        assert client.get('/kj-prestiz/zlecenia?od=zly&s=cokolwiek&per_page=7').status_code == 200

    def test_detail_shows_route_materials_timeline(self, client, app, seeded):
        _admin(client)
        with app.app_context():
            oid = _order('ZP-DEMO-004').id
        html = client.get(f'/kj-prestiz/zlecenia/{oid}').get_data(as_text=True)
        assert 'Trasa operacji' in html and 'Wycinanie' in html and 'Szlifowanie' in html
        assert 'RW wstrzymane' in html and 'Płyta montażowa' in html
        assert 'Utworzono zlecenie ZP-DEMO-004' in html and 'RW wstrzymane do zakończenia kontroli KJ' in html
        assert client.get('/kj-prestiz/zlecenia/99999').status_code == 404


# ── Usuwanie i scenariusz ─────────────────────────────────────────────────────

class TestDeleteAndScenario:
    def test_delete_single_removes_children(self, client, app, seeded):
        _admin(client)
        with app.app_context():
            oid = _order('ZP-DEMO-004').id
        _post(client, f'/kj-prestiz/zlecenia/{oid}/usun')
        with app.app_context():
            assert db.session.get(PkjOrder, oid) is None
            assert PkjOrderOperation.query.filter_by(order_id=oid).count() == 0
            assert PkjOrderMaterial.query.filter_by(order_id=oid).count() == 0
            assert PkjEvent.query.filter_by(order_id=oid).count() == 0
            assert PkjOrder.query.count() == 3

    def test_bulk_delete(self, client, app, seeded):
        _admin(client)
        with app.app_context():
            ids = [_order('ZP-DEMO-002').id, _order('ZP-DEMO-003').id]
        resp = _post(client, '/kj-prestiz/zlecenia/usun-zaznaczone', {'order_ids': ids})
        assert 'Usunięto zlecenia: 2' in resp.get_data(as_text=True)
        resp = _post(client, '/kj-prestiz/zlecenia/usun-zaznaczone', {})
        assert 'Nie zaznaczono' in resp.get_data(as_text=True)
        with app.app_context():
            assert PkjOrder.query.count() == 2

    def test_delete_requires_admin(self, client, app, seeded):
        login(client, 'oper', 'Oper1234!')
        with app.app_context():
            oid = _order('ZP-DEMO-002').id
        assert client.post(f'/kj-prestiz/zlecenia/{oid}/usun',
                           data={'_csrf_token': _csrf(client)}).status_code == 403
        with app.app_context():
            assert db.session.get(PkjOrder, oid) is not None

    def test_delete_cascades_to_repair_orders_inspections_and_photos(self, app, seeded):
        with app.app_context():
            parent = _order('ZP-DEMO-002')
            child = PkjOrder(number='ZN-ZP-DEMO-002/1', type='ZN', parent_id=parent.id)
            db.session.add(child)
            insp = PkjInspection(order_id=parent.id)
            db.session.add(insp)
            db.session.flush()
            res = PkjResult(inspection_id=insp.id, title='Punkt', result='NOK')
            db.session.add(res)
            db.session.flush()
            folder = service.upload_folder()
            with open(os.path.join(folder, 'zdjecie.jpg'), 'wb') as fh:
                fh.write(b'x')
            db.session.add(PkjPhoto(result_id=res.id, filename='zdjecie.jpg'))
            db.session.commit()
            assert ord_.delete_orders([parent.id]) == 2
            assert PkjOrder.query.filter(PkjOrder.number.like('ZN-%')).count() == 0
            assert PkjInspection.query.count() == 0 and PkjResult.query.count() == 0
            assert PkjPhoto.query.count() == 0
            assert not os.path.exists(os.path.join(folder, 'zdjecie.jpg'))

    def test_scenario_reset_restores_only_scenario(self, client, app, seeded):
        _admin(client)
        with app.app_context():
            o = _order('ZP-DEMO-001')
            ord_.report_operation(_op(o, 'Spawanie'), _emp())
            old_id = o.id
            db.session.commit()
            other = _order('ZP-DEMO-003').id
        resp = _post(client, '/kj-prestiz/demo/scenariusz-reset')
        assert 'Przywrócono zlecenie ZP-DEMO-001' in resp.get_data(as_text=True)
        with app.app_context():
            o = _order('ZP-DEMO-001')
            assert o.id != old_id and _op(o, 'Spawanie').status == 'oczekuje'
            assert PkjOrder.query.count() == 4 and _order('ZP-DEMO-003').id == other

    def test_demo_load_after_orders_reset_restores_orders_only(self, client, app, seeded):
        _admin(client)
        _post(client, '/kj-prestiz/demo/reset')
        with app.app_context():
            assert PkjOrder.query.count() == 0 and PkjOperation.query.count() == 6
        resp = _post(client, '/kj-prestiz/demo/wczytaj')
        assert 'zlecenia demo' in resp.get_data(as_text=True)
        with app.app_context():
            assert PkjOrder.query.filter_by(type='ZP').count() == 15 and PkjOperation.query.count() == 6   # 4 + 11 historycznych
