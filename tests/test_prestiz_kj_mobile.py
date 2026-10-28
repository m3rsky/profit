"""Testy modułu KJ Prestiż, etap 3: aplikacja KJ (wybór zlecenia, materiały, punkty w sekcjach, ocena, zdjęcia,
zakończenie, przerwanie, historia), ramka telefonu i etykieta QR."""
import io
import json

import pytest
from PIL import Image

from app import app as flask_app, db
from models import User
from prestiz_kj import demo_data, service, orders as ord_, inspections as insp_
from prestiz_kj.models import (PkjOrder, PkjOrderOperation, PkjOrderMaterial, PkjInspection, PkjInspectionMaterial,
                               PkjResult, PkjPhoto, PkjEvent, PkjCheckPoint)
from test_app import login, _csrf


@pytest.fixture(autouse=True)
def _clean(app, tmp_path):
    flask_app.config['PKJ_UPLOAD_FOLDER'] = str(tmp_path / 'pkj')
    with app.app_context():
        service.reset_all()
        demo_data.seed_configuration()
        demo_data.seed_orders()
    yield
    with app.app_context():
        service.reset_all()


def _admin(client):
    login(client, 'admin', 'Admin1234!')
    return client


def _user():
    return User.query.filter_by(username='admin').one()


def _order(number='ZP-DEMO-004'):
    return PkjOrder.query.filter_by(number=number).one()


def _giecie(order):
    return PkjOrderOperation.query.filter_by(order_id=order.id, operation_name='Gięcie').one()


def _start(order=None, names=None):
    """Rozpoczyna kontrolę Gięcia (domyślnie ZP-DEMO-004, wszystkie materiały)."""
    order = order or _order()
    oop = _giecie(order)
    pending = insp_.pending_materials(oop)
    ids = [m.id for m in pending if names is None or m.material_name in names]
    insp = insp_.start_inspection(oop, ids, _user())
    db.session.commit()
    return insp


def _results(insp, section=None):
    q = PkjResult.query.filter_by(inspection_id=insp.id)
    if section:
        q = q.filter_by(section=section)
    return q.order_by(PkjResult.position).all()


def _answer_all(insp, outcome='OK'):
    for r in _results(insp):
        if r.kind == 'value':
            insp_.record_result(r, value=(r.min_value + r.max_value) / 2, value_given=True)
        else:
            insp_.record_result(r, outcome)
    db.session.commit()


def _png():
    buf = io.BytesIO()
    Image.new('RGB', (6, 6), 'blue').save(buf, 'PNG')
    buf.seek(0)
    return buf


# ── Wejście, ramka, etykieta ──────────────────────────────────────────────────

class TestEntry:
    def test_start_lists_orders_waiting_for_control(self, client):
        _admin(client)
        html = client.get('/kj-prestiz/m/').get_data(as_text=True)
        assert 'ZP-DEMO-004' in html and 'ZP-DEMO-002' not in html
        assert 'RW wstrzymane: 3' in html and 'Skanuj QR zlecenia' in html

    def test_exact_number_opens_order_like_a_scan(self, client, app):
        _admin(client)
        with app.app_context():
            oid = _order('ZP-DEMO-001').id
        resp = client.get('/kj-prestiz/m/?q=zp-demo-001')
        assert resp.status_code == 302 and resp.headers['Location'].endswith(f'/kj-prestiz/m/zlecenie/{oid}')

    def test_search_without_match_warns(self, client):
        _admin(client)
        assert 'Nie znaleziono' in client.get('/kj-prestiz/m/?q=nieistnieje').get_data(as_text=True)
        assert 'ZP-DEMO-003' in client.get('/kj-prestiz/m/?q=beta').get_data(as_text=True)

    def test_requires_module_and_controller(self, client):
        login(client, 'oper', 'Oper1234!')
        for url in ('/kj-prestiz/m/', '/kj-prestiz/aplikacja', '/kj-prestiz/m/historia/1'):
            assert client.get(url).status_code == 403

    def test_phone_frame_page(self, client):
        _admin(client)
        html = client.get('/kj-prestiz/aplikacja').get_data(as_text=True)
        assert '<iframe' in html and '/kj-prestiz/m/' in html and 'allow="camera"' in html

    def test_label_pdf_with_scannable_payload(self, client, app):
        _admin(client)
        with app.app_context():
            o = _order('ZP-DEMO-001')
            oid = o.id
            from prestiz_kj.pdf import qr_payload
            data = json.loads(qr_payload(o))
        assert data == {'p': 'Szafa PSH 600x800x300', 'c': 'Klient demo', 'o': 'ZP-DEMO-001', 'q': 1}
        resp = client.get(f'/kj-prestiz/zlecenia/{oid}/etykieta.pdf')
        assert resp.status_code == 200 and resp.data.startswith(b'%PDF') and resp.mimetype == 'application/pdf'


# ── Operacja i materiały ──────────────────────────────────────────────────────

class TestSelection:
    def test_order_page_offers_pending_materials(self, client, app):
        _admin(client)
        with app.app_context():
            oid = _order().id
        html = client.get(f'/kj-prestiz/m/zlecenie/{oid}').get_data(as_text=True)
        assert 'Gięcie' in html and html.count('name="material"') == 3
        assert 'Rozpocznij kontrolę' in html and 'Aparatura' not in html

    def test_order_without_pending_control(self, client, app):
        _admin(client)
        with app.app_context():
            oid = _order('ZP-DEMO-001').id
        assert 'nie czeka teraz na kontrolę' in client.get(f'/kj-prestiz/m/zlecenie/{oid}').get_data(as_text=True)

    def test_start_builds_sections_with_copied_points(self, app):
        with app.app_context():
            insp = _start()
            assert insp.round == 1 and insp.status == 'w_toku' and insp.performer_name
            sections = insp_.sections(insp.id)
            assert [s['name'] for s in sections] == ['Wspólne', 'Korpus', 'Drzwi', 'Płyta montażowa']
            assert [len(s['results']) for s in sections] == [6, 4, 5, 2]
            gabaryt = next(r for r in _results(insp, 'Wspólne') if r.title == 'Wymiar gabarytowy')
            assert (gabaryt.kind, gabaryt.unit, gabaryt.min_value, gabaryt.max_value) == ('value', 'mm', 598, 602)
            assert gabaryt.photo_required_on_nok is True
            assert all(m.element_status == 'w_kontroli' for m in PkjOrderMaterial.query.filter_by(
                order_id=_order().id, order_operation_id=_giecie(_order()).id))
            assert PkjEvent.query.filter_by(type='kontrola_start').count() == 1

    def test_group_points_only_for_group_members(self, app):
        with app.app_context():
            insp = _start()
            titles = {s['name']: [r.title for r in s['results']] for s in insp_.sections(insp.id)}
            assert 'Zgodność kolorów korpusu i drzwi' in titles['Korpus']
            assert 'Zgodność kolorów korpusu i drzwi' in titles['Drzwi']
            assert 'Zgodność kolorów korpusu i drzwi' not in titles['Płyta montażowa']

    def test_subset_of_materials(self, app):
        with app.app_context():
            insp = _start(names={'Drzwi'})
            assert [s['name'] for s in insp_.sections(insp.id)] == ['Wspólne', 'Drzwi']
            left = insp_.pending_materials(_giecie(_order()))
            assert {m.material_name for m in left} == {'Korpus', 'Płyta montażowa'}

    def test_start_validation(self, app):
        with app.app_context():
            oop = _giecie(_order())
            with pytest.raises(ValueError, match='co najmniej jeden'):
                insp_.start_inspection(oop, [], _user())
            with pytest.raises(ValueError, match='nie czeka'):
                insp_.start_inspection(_giecie(_order('ZP-DEMO-001')), [1], _user())
            insp = _start(names={'Korpus'})
            ids = [m.id for m in PkjOrderMaterial.query.filter_by(order_operation_id=oop.id,
                                                                  material_name='Korpus')]
            with pytest.raises(ValueError):                      # Korpus jest już w kontroli
                insp_.start_inspection(oop, ids, _user())
            assert insp.id

    def test_start_without_points_is_rejected(self, app):
        with app.app_context():
            PkjCheckPoint.query.update({'is_active': False})
            db.session.commit()
            oop = _giecie(_order())
            ids = [m.id for m in insp_.pending_materials(oop)]
            with pytest.raises(ValueError, match='Brak punktów'):
                insp_.start_inspection(oop, ids, _user())

    def test_start_via_route_and_resume(self, client, app):
        _admin(client)
        with app.app_context():
            oop = _giecie(_order())
            oid, oop_id = oop.order_id, oop.id
            mats = [m.id for m in insp_.pending_materials(oop)]
        token = _csrf(client)
        resp = client.post('/kj-prestiz/m/kontrola/start', data={'oop_id': oop_id, 'material': mats,
                                                                 '_csrf_token': token})
        assert resp.status_code == 302 and '/kj-prestiz/m/kontrola/' in resp.headers['Location']
        page = client.get(f'/kj-prestiz/m/zlecenie/{oid}').get_data(as_text=True)
        assert 'Kontrola w toku' in page and 'name="material"' not in page
        html = client.get(resp.headers['Location']).get_data(as_text=True)
        assert 'Wspólne' in html and 'Zakończ' in html and 'Przerwij' in html and 'data-v="NOK"' in html


# ── Ocena punktów ─────────────────────────────────────────────────────────────

class TestRecordResults:
    def test_ok_nok_nd_and_clear(self, app):
        with app.app_context():
            insp = _start()
            r = _results(insp)[0]
            for outcome in ('OK', 'NOK', 'ND'):
                insp_.record_result(r, outcome)
                assert r.result == outcome
            insp_.record_result(r, '')
            assert r.result is None
            with pytest.raises(ValueError):
                insp_.record_result(r, 'MOŻE')

    def test_value_sets_result_by_range(self, app):
        with app.app_context():
            insp = _start()
            r = next(x for x in _results(insp) if x.title == 'Wymiar gabarytowy')
            insp_.record_result(r, value=600.5, value_given=True)
            assert (r.result, r.out_of_range) == ('OK', False)
            insp_.record_result(r, value=603, value_given=True)
            assert (r.result, r.out_of_range) == ('NOK', True)
            insp_.record_result(r, 'OK')                         # poza zakresem nie może być OK
            assert r.result == 'NOK'
            insp_.record_result(r, value=None, value_given=True)
            assert r.out_of_range is False

    def test_record_via_json_route(self, client, app):
        _admin(client)
        with app.app_context():
            insp = _start()
            rid, iid = _results(insp)[0].id, insp.id
            vid = next(x for x in _results(insp) if x.title == 'Wymiar gabarytowy').id
        h = {'X-CSRF-Token': _csrf(client)}
        data = client.post(f'/kj-prestiz/m/kontrola/{iid}/punkt/{rid}', json={'result': 'OK'}, headers=h).get_json()
        assert data['ok'] and data['result'] == 'OK' and data['done'] == 1 and data['total'] == 17
        data = client.post(f'/kj-prestiz/m/kontrola/{iid}/punkt/{vid}', json={'value': '599,5'}, headers=h).get_json()
        assert data['result'] == 'OK' and data['out_of_range'] is False and data['done'] == 2
        data = client.post(f'/kj-prestiz/m/kontrola/{iid}/punkt/{vid}', json={'value': '650'}, headers=h).get_json()
        assert data['result'] == 'NOK' and data['out_of_range'] is True
        bad = client.post(f'/kj-prestiz/m/kontrola/{iid}/punkt/{vid}', json={'value': 'abc'}, headers=h)
        assert bad.status_code == 400 and not bad.get_json()['ok']
        assert client.post(f'/kj-prestiz/m/kontrola/{iid + 99}/punkt/{rid}', json={}, headers=h).status_code == 404
        notes = client.post(f'/kj-prestiz/m/kontrola/{iid}/punkt/{rid}', json={'notes': ' rysa '}, headers=h)
        assert notes.get_json()['result'] == 'OK'
        with app.app_context():
            assert db.session.get(PkjResult, rid).notes == 'rysa'

    def test_performer_change(self, client, app):
        _admin(client)
        with app.app_context():
            insp = _start()
            iid = insp.id
            from prestiz_kj.models import PkjEmployee
            new_id = PkjEmployee.query.filter_by(name='Dorota Kos').one().id
        h = {'X-CSRF-Token': _csrf(client)}
        assert client.post(f'/kj-prestiz/m/kontrola/{iid}/wykonawca', json={'employee_id': new_id},
                           headers=h).get_json()['ok']
        assert not client.post(f'/kj-prestiz/m/kontrola/{iid}/wykonawca', json={}, headers=h).get_json()['ok']
        with app.app_context():
            assert db.session.get(PkjInspection, iid).performer_name == 'Dorota Kos'


# ── Zdjęcia ───────────────────────────────────────────────────────────────────

class TestPhotos:
    def test_upload_serve_delete(self, client, app):
        _admin(client)
        with app.app_context():
            insp = _start()
            rid, iid = _results(insp)[0].id, insp.id
        token = _csrf(client)
        resp = client.post(f'/kj-prestiz/m/kontrola/{iid}/punkt/{rid}/zdjecie', content_type='multipart/form-data',
                           data={'photo': (_png(), 'a.png')}, headers={'X-CSRF-Token': token}).get_json()
        assert resp['ok'] and resp['url'] and resp['delete_url']
        assert client.get(resp['url']).status_code == 200
        bad = client.post(f'/kj-prestiz/m/kontrola/{iid}/punkt/{rid}/zdjecie', content_type='multipart/form-data',
                          data={'photo': (io.BytesIO(b'to nie obraz'), 'a.png')}, headers={'X-CSRF-Token': token})
        assert bad.status_code == 400
        assert client.post(resp['delete_url'], json={}, headers={'X-CSRF-Token': token}).get_json()['ok']
        with app.app_context():
            assert PkjPhoto.query.count() == 0


# ── Zakończenie, przerwanie, historia ─────────────────────────────────────────

class TestFinish:
    def test_unanswered_points_block_finish(self, app):
        with app.app_context():
            insp = _start()
            with pytest.raises(ValueError, match='Pozostało: 17'):
                insp_.finish_inspection(insp, 't')

    def test_all_ok_releases_rw_and_closes_operation(self, app):
        with app.app_context():
            order = _order()
            insp = _start()
            _answer_all(insp)
            summary = insp_.finish_inspection(insp, 'admin')
            db.session.commit()
            assert summary['nok'] == [] and len(summary['ok']) == 3
            assert insp.status == 'zakonczona' and insp.result == 'OK' and insp.finished_at
            mats = PkjOrderMaterial.query.filter_by(order_operation_id=_giecie(order).id).all()
            assert all(m.rw_status == 'wydane' and m.element_status == 'zgodny' for m in mats)
            assert _giecie(order).status == 'zakonczona'
            assert db.session.get(PkjOrder, order.id).status == 'w_produkcji'
            types = [e.type for e in PkjEvent.query.filter_by(order_id=order.id).order_by(PkjEvent.id)]
            assert 'kontrola' in types and types.count('rw_wydane') >= 1 and 'operacja_zakonczona' in types
            with pytest.raises(ValueError):
                insp_.finish_inspection(insp, 'admin')

    def test_nok_needs_required_photo_then_only_that_material_is_returned(self, app):
        with app.app_context():
            order = _order()
            insp = _start()
            _answer_all(insp)
            spoina = next(r for r in _results(insp, 'Drzwi') if r.title == 'Spoina narożna kompletna')
            insp_.record_result(spoina, 'NOK', notes='Spoina niepełna')
            db.session.commit()
            with pytest.raises(ValueError, match='zdjęcie'):
                insp_.finish_inspection(insp, 'admin')
            db.session.add(PkjPhoto(result_id=spoina.id, filename='x.jpg'))
            db.session.commit()
            summary = insp_.finish_inspection(insp, 'admin')
            db.session.commit()
            assert summary['nok'] == ['Drzwi'] and set(summary['ok']) == {'Korpus', 'Płyta montażowa'}
            assert insp.result == 'NOK'
            by_name = {m.material_name: m for m in PkjOrderMaterial.query.filter_by(
                order_operation_id=_giecie(order).id)}
            assert (by_name['Korpus'].element_status, by_name['Korpus'].rw_status) == ('zgodny', 'wydane')
            assert (by_name['Drzwi'].element_status, by_name['Drzwi'].rw_status) == ('zawrocony', 'wstrzymane')
            assert _giecie(order).status == 'do_kontroli'          # operacja czeka na zawrót Drzwi
            assert db.session.get(PkjOrder, order.id).status == 'do_kontroli'
            verdicts = {m.name: m.verdict for m in PkjInspectionMaterial.query.filter_by(inspection_id=insp.id)}
            assert verdicts == {'Korpus': 'OK', 'Drzwi': 'NOK', 'Płyta montażowa': 'OK'}
            assert PkjEvent.query.filter_by(order_id=order.id, type='wymagany_zawrot').count() == 1

    def test_common_point_nok_returns_all_selected_materials(self, app):
        with app.app_context():
            insp = _start(names={'Korpus', 'Drzwi'})
            _answer_all(insp)
            common = next(r for r in _results(insp, 'Wspólne') if r.title == 'Brak śladów korozji')
            insp_.record_result(common, 'NOK')
            summary = insp_.finish_inspection(insp, 'admin')
            assert summary['ok'] == [] and set(summary['nok']) == {'Korpus', 'Drzwi'}

    def test_interrupt_excludes_element(self, app):
        with app.app_context():
            order = _order()
            insp = _start(names={'Drzwi'})
            insp_.interrupt_inspection(insp, 'admin')
            db.session.commit()
            assert insp.status == 'przerwana' and insp.result == 'NOK' and insp.finished_at
            im = PkjInspectionMaterial.query.filter_by(inspection_id=insp.id).one()
            assert im.verdict == 'NOK' and im.interrupted is True
            drzwi = PkjOrderMaterial.query.filter_by(order_id=order.id, material_name='Drzwi',
                                                     order_operation_id=_giecie(order).id).one()
            assert (drzwi.element_status, drzwi.rw_status) == ('zawrocony', 'wstrzymane')
            assert PkjEvent.query.filter_by(order_id=order.id, type='kontrola_przerwana').count() == 1
            with pytest.raises(ValueError):
                insp_.interrupt_inspection(insp, 'admin')

    def test_finished_inspection_is_read_only(self, app):
        with app.app_context():
            insp = _start()
            _answer_all(insp)
            insp_.finish_inspection(insp, 'admin')
            db.session.commit()
            with pytest.raises(ValueError):
                insp_.record_result(_results(insp)[0], 'NOK')
            with pytest.raises(ValueError):
                insp_.set_performer(insp, None)

    def test_finish_flow_via_routes_and_history(self, client, app):
        _admin(client)
        with app.app_context():
            insp = _start(names={'Drzwi'})
            iid, oid = insp.id, insp.order_id
            _answer_all(insp)
        token = _csrf(client)
        resp = client.post(f'/kj-prestiz/m/kontrola/{iid}/zakoncz', data={'_csrf_token': token},
                           follow_redirects=True)
        html = resp.get_data(as_text=True)
        assert 'Kontrola zgodna' in html and 'RW na materiały wydane' in html and 'Wróć do zlecenia' in html
        assert 'data-v="OK"' not in html and 'Przerwij' not in html            # widok tylko do odczytu
        hist = client.get(f'/kj-prestiz/m/historia/{oid}').get_data(as_text=True)
        assert 'Gięcie' in hist and 'Zgodna' in hist and 'Drzwi' in hist
        desk = client.get(f'/kj-prestiz/zlecenia/{oid}').get_data(as_text=True)
        assert 'Kontrole KJ' in desk and 'Etykieta QR' in desk

    def test_nok_banner_and_interrupt_via_routes(self, client, app):
        _admin(client)
        with app.app_context():
            insp = _start(names={'Korpus'})
            iid = insp.id
        token = _csrf(client)
        html = client.post(f'/kj-prestiz/m/kontrola/{iid}/przerwij', data={'_csrf_token': token},
                           follow_redirects=True).get_data(as_text=True)
        assert 'Kontrola przerwana' in html and 'Korpus' in html
        # nieoceniona kontrola nie kończy się: komunikat zamiast błędu serwera
        with app.app_context():
            insp2 = _start(names={'Drzwi'})
            iid2 = insp2.id
        html = client.post(f'/kj-prestiz/m/kontrola/{iid2}/zakoncz', data={'_csrf_token': token},
                           follow_redirects=True).get_data(as_text=True)
        assert 'Oceń wszystkie punkty' in html

    def test_history_empty_state(self, client, app):
        _admin(client)
        with app.app_context():
            oid = _order('ZP-DEMO-002').id
        assert 'nie ma jeszcze żadnych kontrol' in client.get(f'/kj-prestiz/m/historia/{oid}').get_data(as_text=True)
