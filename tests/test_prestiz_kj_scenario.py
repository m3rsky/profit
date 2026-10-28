"""Testy modułu KJ Prestiż, etap 7: strona Scenariusz (stan kroków z danych), pytania do Streamsoft, nawigacja modułu,
przywracanie scenariusza, filtr rodzaju zlecenia i przycisk "Pozostałe punkty: OK"."""
import re

import pytest

from app import app as flask_app, db
from models import User
from prestiz_kj import demo_data, inspections as insp_, orders as ord_, repairs, service
from prestiz_kj.models import (PkjEmployee, PkjOperation, PkjOrder, PkjOrderMaterial, PkjOrderOperation, PkjReason,
                               PkjResult)
from prestiz_kj.scenario import scenario_state
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
        demo_data.seed_orders()


@pytest.fixture
def admin(client):
    login(client, 'admin', 'Admin1234!')
    return client


def _post(client, url, data=None):
    data = dict(data or {})
    data['_csrf_token'] = _csrf(client)
    return client.post(url, data=data, follow_redirects=True)


def _states():
    return [s['state'] for s in scenario_state()['steps']]


def _zp():
    return PkjOrder.query.filter_by(number='ZP-DEMO-001').one()


def _spawanie():
    return PkjOrderOperation.query.filter_by(order_id=_zp().id, operation_name='Spawanie').one()


def _user():
    return User.query.filter_by(username='admin').one()


# ── Stan scenariusza liczony z danych ─────────────────────────────────────────

class TestScenarioState:
    def test_before_data_nothing_is_ready(self, app):
        with app.app_context():
            state = scenario_state()
            assert state['order'] is None and len(state['steps']) == 7
            assert [s['state'] for s in state['steps']] == ['now'] + ['todo'] * 6
            assert all(s['links'] != [] for s in state['steps'] if s['key'] in ('konfiguracja', 'kontrola', 'raporty'))

    def test_full_walkthrough(self, app, seeded):
        with app.test_request_context():
            assert _states() == ['done', 'done', 'now', 'todo', 'todo', 'todo', 'todo']
            ord_.report_operation(_spawanie(), PkjEmployee.query.filter_by(name='Cezary Lis').one(), actor='t')
            db.session.commit()
            state = scenario_state()
            assert [s['state'] for s in state['steps']] == ['done'] * 3 + ['now'] + ['todo'] * 3
            assert 'Cezary Lis' in state['steps'][2]['detail'] and 'wstrzymane' in state['steps'][2]['detail']

            oop = _spawanie()
            insp = insp_.start_inspection(oop, [m.id for m in insp_.pending_materials(oop) if m.material_name != 'Płyta montażowa'],
                                          _user())
            insp_.mark_rest_ok(insp)
            spoina = PkjResult.query.filter_by(inspection_id=insp.id, title='Spoina narożna kompletna').one()
            insp_.record_result(spoina, 'NOK', notes='Spoina niepełna')
            from prestiz_kj.models import PkjPhoto
            db.session.add(PkjPhoto(result_id=spoina.id, filename='x.jpg'))
            db.session.flush()
            insp_.finish_inspection(insp, 't')
            db.session.commit()
            state = scenario_state()
            assert [s['state'] for s in state['steps']][:5] == ['done'] * 3 + ['done', 'now']
            assert 'NOK' in state['steps'][3]['detail'] and 'Drzwi: NOK' in state['steps'][3]['detail']

            drzwi = PkjOrderMaterial.query.filter_by(order_id=_zp().id, material_name='Drzwi',
                                                     order_operation_id=oop.id).one()
            szl = PkjOperation.query.filter_by(name='Szlifowanie').one()
            reason = PkjReason.query.filter_by(name='Spoina niepełna').one()
            ret = repairs.create_return(drzwi, reason.id, 'Dospawać', 'poprawka', oop.id, [szl.id], 't')
            db.session.commit()
            state = scenario_state()
            assert state['steps'][4]['state'] == 'done' and 'ZN-ZP-DEMO-001/1' in state['steps'][4]['detail']
            assert state['steps'][5]['state'] == 'now' and state['all_done'] is False

            zn = db.session.get(PkjOrder, ret.repair_order_id)
            for zop in ord_.operations_of(zn.id):
                ord_.report_operation(zop, PkjEmployee.query.filter_by(department=zop.operation_name).first(), actor='t')
            recheck = insp_.start_inspection(oop, [drzwi.id], _user())
            insp_.record_result(PkjResult.query.filter_by(inspection_id=recheck.id).one(), 'OK')
            insp_.finish_inspection(recheck, 't')
            db.session.commit()
            state = scenario_state()
            assert [s['state'] for s in state['steps']] == ['done'] * 7 and state['all_done'] is True
            assert state['steps'][5]['detail'] == 'Drzwi: zgodny'

    def test_reset_returns_to_step_three(self, app, seeded):
        with app.test_request_context():
            ord_.report_operation(_spawanie(), PkjEmployee.query.filter_by(name='Cezary Lis').one(), actor='t')
            db.session.commit()
            assert _states()[2] == 'done'
            demo_data.reset_scenario()
            assert _states() == ['done', 'done', 'now', 'todo', 'todo', 'todo', 'todo']


# ── Strony ────────────────────────────────────────────────────────────────────

class TestPages:
    def test_scenario_page(self, admin, seeded):
        html = admin.get('/kj-prestiz/scenariusz').get_data(as_text=True)
        assert html.count('pkj-step pkj-step--') == 7
        assert html.count('pkj-step--now') == 1 and html.count('pkj-step--done') == 2
        assert 'Meldunek operacji Spawanie, RW wstrzymane' in html and 'Pozostałe punkty: OK' in html
        assert 'Przywróć scenariusz pokazu' in html and 'ZP-DEMO-001' in html

    def test_every_link_on_the_page_works(self, admin, seeded):
        html = admin.get('/kj-prestiz/scenariusz').get_data(as_text=True)
        urls = set(re.findall(r'href="(/kj-prestiz/[^"]+)"', html))
        assert len(urls) >= 12
        for url in sorted(urls):
            resp = admin.get(url, follow_redirects=True)
            assert resp.status_code == 200, url

    def test_empty_state_offers_loading_demo(self, admin, app):
        html = admin.get('/kj-prestiz/scenariusz').get_data(as_text=True)
        assert 'Nie ma jeszcze danych demonstracyjnych' in html and 'Wczytaj dane demo' in html
        assert 'pkj-step ' not in html

    def test_reset_redirects_back_to_scenario_only_for_local_paths(self, admin, seeded):
        resp = admin.post('/kj-prestiz/demo/scenariusz-reset',
                          data={'next': '/kj-prestiz/scenariusz', '_csrf_token': _csrf(admin)})
        assert resp.status_code == 302 and resp.headers['Location'].endswith('/kj-prestiz/scenariusz')
        resp = admin.post('/kj-prestiz/demo/scenariusz-reset',
                          data={'next': 'https://zlosliwa.example/', '_csrf_token': _csrf(admin)})
        assert resp.status_code == 302 and resp.headers['Location'].endswith('/kj-prestiz/zlecenia')
        resp = admin.post('/kj-prestiz/demo/scenariusz-reset',
                          data={'next': '//zlosliwa.example/', '_csrf_token': _csrf(admin)})
        assert 'zlosliwa' not in resp.headers['Location']

    def test_questions_page_reflects_settings(self, admin, app):
        with app.app_context():
            demo_data.seed_configuration()
        html = admin.get('/kj-prestiz/pytania').get_data(as_text=True)
        assert 'Pytania do Streamsoft' in html and 'Kontroler tworzy zlecenie naprawcze od razu' in html
        for needle in ('A4', 'A5', 'B4', 'C2', 'D2', 'Praca bez Wi-Fi', 'Słownik pojęć'):
            assert needle in html
        _post(admin, '/kj-prestiz/konfiguracja/ustawienia/zapisz-wszystko', {'approval_mode': 'manager'})
        assert 'Zlecenie naprawcze czeka na zatwierdzenie kierownika' in admin.get(
            '/kj-prestiz/pytania').get_data(as_text=True)

    def test_access_control(self, client, seeded):
        login(client, 'oper', 'Oper1234!')
        assert client.get('/kj-prestiz/scenariusz').status_code == 403
        assert client.get('/kj-prestiz/pytania').status_code == 403

    def test_module_navigation_on_every_desktop_page(self, admin, seeded, app):
        with app.app_context():
            oid = _zp().id
        pages = {'/kj-prestiz/': 'Przegląd', '/kj-prestiz/scenariusz': 'Scenariusz', '/kj-prestiz/zlecenia': 'Zlecenia',
                 f'/kj-prestiz/zlecenia/{oid}': 'Zlecenia', '/kj-prestiz/zawroty': 'Zawroty',
                 '/kj-prestiz/raporty': 'Raporty', '/kj-prestiz/raporty/fpy': 'Raporty',
                 '/kj-prestiz/aplikacja': 'Aplikacja KJ', '/kj-prestiz/konfiguracja/punkty': 'Konfiguracja',
                 '/kj-prestiz/pytania': 'Pytania'}
        for url, label in pages.items():
            html = admin.get(url).get_data(as_text=True)
            assert 'pkj-modnav' in html, url
            active = re.findall(r'<a href="[^"]+" class="active"><i class="bi [^"]+"></i> ([^<]+)</a>',
                                html.split('pkj-modnav', 1)[1].split('</nav>', 1)[0])
            assert active == [label], (url, active)

    def test_phone_app_marks_demo(self, admin, seeded):
        assert 'm-demo' in admin.get('/kj-prestiz/m/').get_data(as_text=True)


# ── Lista zleceń: rodzaj ──────────────────────────────────────────────────────

class TestOrderTypeFilter:
    @pytest.fixture
    def with_zn(self, app, seeded):
        with app.app_context():
            oop = _spawanie()
            ord_.report_operation(oop, PkjEmployee.query.filter_by(name='Cezary Lis').one(), actor='t')
            insp = insp_.start_inspection(oop, [m.id for m in insp_.pending_materials(oop)], _user())
            insp_.interrupt_inspection(insp, 't')
            drzwi = PkjOrderMaterial.query.filter_by(order_id=_zp().id, material_name='Drzwi',
                                                     order_operation_id=oop.id).one()
            repairs.create_return(drzwi, PkjReason.query.first().id, None, 'poprawka', oop.id, [], 't')
            db.session.commit()

    def test_default_shows_production_orders_only(self, admin, with_zn):
        html = admin.get('/kj-prestiz/zlecenia').get_data(as_text=True)
        assert 'ZP-DEMO-001' in html and 'ZN-ZP-DEMO-001/1' not in html
        assert 'value="zp" selected' in html

    def test_repair_and_all(self, admin, with_zn):
        zn = admin.get('/kj-prestiz/zlecenia?typ=zn').get_data(as_text=True)
        assert 'ZN-ZP-DEMO-001/1' in zn and 'ZP-DEMO-002' not in zn and 'value="zn" selected' in zn
        both = admin.get('/kj-prestiz/zlecenia?typ=all').get_data(as_text=True)
        assert 'ZN-ZP-DEMO-001/1' in both and 'ZP-DEMO-002' in both

    def test_search_reaches_repair_orders_without_choosing_type(self, admin, with_zn):
        assert 'ZN-ZP-DEMO-001/1' in admin.get('/kj-prestiz/zlecenia?q=ZN-').get_data(as_text=True)
        html = admin.get('/kj-prestiz/zlecenia?q=ZN-&typ=zp').get_data(as_text=True)
        assert 'ZN-ZP-DEMO-001/1' not in html                           # wybór użytkownika ma pierwszeństwo

    def test_invalid_type_falls_back_and_paging_keeps_type(self, admin, with_zn):
        assert 'ZP-DEMO-002' in admin.get('/kj-prestiz/zlecenia?typ=cokolwiek').get_data(as_text=True)
        html = admin.get('/kj-prestiz/zlecenia?typ=all&per_page=10').get_data(as_text=True)
        assert 'typ=all' in html or 'Następna' not in html


# ── Pozostałe punkty: OK ──────────────────────────────────────────────────────

class TestRestOk:
    def _start(self):
        oop = PkjOrderOperation.query.filter_by(order_id=PkjOrder.query.filter_by(number='ZP-DEMO-004').one().id,
                                                operation_name='Gięcie').one()
        return insp_.start_inspection(oop, [m.id for m in insp_.pending_materials(oop)], _user())

    def test_marks_unanswered_and_keeps_existing_answers(self, app, seeded):
        with app.app_context():
            insp = self._start()
            first = PkjResult.query.filter_by(inspection_id=insp.id).order_by(PkjResult.position).first()
            insp_.record_result(first, 'NOK', notes='zostaje')
            total = PkjResult.query.filter_by(inspection_id=insp.id).count()
            assert insp_.mark_rest_ok(insp) == total - 1
            results = PkjResult.query.filter_by(inspection_id=insp.id).all()
            assert all(r.result for r in results) and first.result == 'NOK' and first.notes == 'zostaje'
            assert insp_.mark_rest_ok(insp) == 0

    def test_value_points_get_a_value_inside_the_range(self, app, seeded):
        with app.app_context():
            insp = self._start()
            insp_.mark_rest_ok(insp)
            vals = PkjResult.query.filter_by(inspection_id=insp.id, kind='value').all()
            assert vals and all(r.value is not None and r.min_value <= r.value <= r.max_value
                                and not r.out_of_range and r.result == 'OK' for r in vals)

    def test_value_point_with_single_bound_and_none(self, app, seeded):
        with app.app_context():
            insp = self._start()
            r1, r2 = PkjResult.query.filter_by(inspection_id=insp.id).limit(2).all()
            r1.kind, r1.min_value, r1.max_value = 'value', 5.0, None
            r2.kind, r2.min_value, r2.max_value = 'value', None, None
            db.session.commit()
            insp_.mark_rest_ok(insp)
            assert (r1.value, r1.result) == (5.0, 'OK') and (r2.value, r2.result) == (None, 'OK')

    def test_cannot_change_finished_inspection(self, app, seeded):
        with app.app_context():
            insp = self._start()
            insp_.mark_rest_ok(insp)
            insp_.finish_inspection(insp, 't')
            with pytest.raises(ValueError):
                insp_.mark_rest_ok(insp)

    def test_route_and_button(self, admin, app, seeded):
        with app.app_context():
            insp = self._start()
            db.session.commit()
            iid = insp.id
        page = admin.get(f'/kj-prestiz/m/kontrola/{iid}').get_data(as_text=True)
        assert 'Pozostałe punkty: OK' in page
        resp = _post(admin, f'/kj-prestiz/m/kontrola/{iid}/reszta-ok')
        html = resp.get_data(as_text=True)
        assert 'Oceniono jako OK punktów: 17' in html and '<b id="m-done">17</b>' in html
        assert 'Wszystkie punkty są już ocenione' in _post(admin, f'/kj-prestiz/m/kontrola/{iid}/reszta-ok').get_data(
            as_text=True)
        with app.app_context():
            insp_.finish_inspection(db.session.get(insp_.PkjInspection, iid), 't')
            db.session.commit()
        done = admin.get(f'/kj-prestiz/m/kontrola/{iid}').get_data(as_text=True)
        assert 'Pozostałe punkty: OK' not in done
        assert 'Kontrola jest zakończona' in _post(admin, f'/kj-prestiz/m/kontrola/{iid}/reszta-ok').get_data(
            as_text=True)

    def test_route_requires_controller_and_module(self, client, app, seeded):
        with app.app_context():
            iid = self._start().id
            db.session.commit()
        login(client, 'oper', 'Oper1234!')
        assert client.post(f'/kj-prestiz/m/kontrola/{iid}/reszta-ok',
                           data={'_csrf_token': _csrf(client)}).status_code == 403
