"""Testy modułu KJ Prestiż, etap 4: zawrót, zlecenie naprawcze (ZN), decyzja poprawka albo złom, zatwierdzanie przez
kierownika, meldunek operacji naprawczych, kontrola sprawdzająca i zamknięcie, rejestr zawrotów."""
import pytest

from app import app as flask_app, db
from models import User
from prestiz_kj import demo_data, service, orders as ord_, inspections as insp_, repairs
from prestiz_kj.models import (PkjOperation, PkjEmployee, PkjOrder, PkjOrderOperation, PkjOrderMaterial,
                               PkjInspection, PkjInspectionMaterial, PkjResult, PkjPhoto, PkjEvent, PkjReason,
                               PkjReturn, PkjReturnOperation)
from test_app import login, _csrf

ZP = 'ZP-DEMO-001'


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


def _zp():
    return PkjOrder.query.filter_by(number=ZP).one()


def _op(order, name):
    return PkjOrderOperation.query.filter_by(order_id=order.id, operation_name=name).one()


def _emp(name):
    return PkjEmployee.query.filter_by(name=name).one()


def _mat(name, order=None):
    order = order or _zp()
    return PkjOrderMaterial.query.filter_by(order_id=order.id, material_name=name,
                                            order_operation_id=_op(order, 'Spawanie').id).one()


def _reason(name='Spoina niepełna'):
    return PkjReason.query.filter_by(name=name).one().id


def _answer_ok(insp):
    for r in PkjResult.query.filter_by(inspection_id=insp.id).all():
        if r.kind == 'value':
            insp_.record_result(r, value=(r.min_value + r.max_value) / 2, value_given=True)
        elif r.result is None:
            insp_.record_result(r, 'OK')


def _failed_welding_control(names=None, interrupt=False):
    """ZP-DEMO-001: meldunek Spawania, kontrola, w której Drzwi mają NOK na spoinie (ze zdjęciem)."""
    zp = _zp()
    oop = _op(zp, 'Spawanie')
    ord_.report_operation(oop, _emp('Cezary Lis'), actor='t')
    ids = [m.id for m in insp_.pending_materials(oop) if names is None or m.material_name in names]
    insp = insp_.start_inspection(oop, ids, _user())
    if interrupt:
        insp_.interrupt_inspection(insp, 't')
    else:
        _answer_ok(insp)
        spoina = PkjResult.query.filter_by(inspection_id=insp.id, title='Spoina narożna kompletna').one()
        insp_.record_result(spoina, 'NOK', notes='Spoina niepełna')
        db.session.add(PkjPhoto(result_id=spoina.id, filename='x.jpg'))
        db.session.flush()
        insp_.finish_inspection(insp, 't')
    db.session.commit()
    return insp


def _repair_plan(order_ops_names=('Spawanie',), extra=('Szlifowanie',)):
    zp = _zp()
    target = _op(zp, order_ops_names[0]).id
    extra_ids = [PkjOperation.query.filter_by(name=n).one().id for n in extra]
    return target, extra_ids


def _return_drzwi(decision='poprawka', extra=('Szlifowanie',), actor='t'):
    target, extra_ids = _repair_plan(extra=extra)
    ret = repairs.create_return(_mat('Drzwi'), _reason(), 'Dospawać narożnik', decision, target, extra_ids, actor)
    db.session.commit()
    return ret


def _report_all(zn):
    for oop in ord_.operations_of(zn.id):
        emp = PkjEmployee.query.filter_by(department=oop.operation_name).first() or _emp('Cezary Lis')
        ord_.report_operation(oop, emp, actor='t')
    db.session.commit()


# ── Zgłoszenie zawrotu i ZN ───────────────────────────────────────────────────

class TestCreateReturn:
    def test_needs_return_after_nok_inspection(self, app):
        with app.app_context():
            insp = _failed_welding_control()
            assert [m.material_name for m in repairs.needs_return(insp.order_id)] == ['Drzwi']

    def test_repair_order_created_with_manual_operation(self, app):
        with app.app_context():
            insp = _failed_welding_control()
            ret = _return_drzwi()
            zn = db.session.get(PkjOrder, ret.repair_order_id)
            assert zn.number == 'ZN-ZP-DEMO-001/1' and zn.type == 'ZN' and zn.parent_id == _zp().id
            assert zn.source_return_id == ret.id and zn.quantity == 1 and zn.status == 'nowe'
            assert zn.qr_key == f'{zn.product}|{zn.client}|{zn.number}'
            ops = ord_.operations_of(zn.id)
            assert [(o.operation_name, o.added_manually, o.kj_required) for o in ops] == [
                ('Spawanie', False, False), ('Szlifowanie', True, False)]
            assert all(o.repair_for_return_id == ret.id for o in ops)
            assert PkjOrderMaterial.query.filter_by(order_id=zn.id).count() == 0
            assert ret.status == 'w_naprawie' and ret.decision == 'poprawka' and ret.reason_name == 'Spoina niepełna'
            assert ret.inspection_id == insp.id and ret.result_id
            assert db.session.get(PkjResult, ret.result_id).title == 'Spoina narożna kompletna'
            drzwi = _mat('Drzwi')
            assert (drzwi.element_status, drzwi.rw_status) == ('w_naprawie', 'wstrzymane')
            assert repairs.needs_return(_zp().id) == []
            assert [r.operation_name for r in PkjReturnOperation.query.filter_by(return_id=ret.id)] == [
                'Spawanie', 'Szlifowanie']
            types = {e.type for e in PkjEvent.query.filter_by(order_id=_zp().id)}
            assert {'zawrot', 'zn_utworzone'} <= types
            assert PkjEvent.query.filter_by(order_id=zn.id, type='utworzenie').count() == 1

    def test_target_only_and_second_zn_number(self, app):
        with app.app_context():
            _failed_welding_control(names={'Drzwi'})
            ret = _return_drzwi(extra=())
            zn = db.session.get(PkjOrder, ret.repair_order_id)
            assert [o.operation_name for o in ord_.operations_of(zn.id)] == ['Spawanie']

    def test_validation(self, app):
        with app.app_context():
            _failed_welding_control()
            target, extra = _repair_plan()
            drzwi, korpus = _mat('Drzwi'), _mat('Korpus')
            with pytest.raises(ValueError, match='nie wymaga'):
                repairs.create_return(korpus, _reason(), None, 'poprawka', target, extra)
            with pytest.raises(ValueError, match='przyczynę'):
                repairs.create_return(drzwi, None, None, 'poprawka', target, extra)
            with pytest.raises(ValueError, match='decyzję'):
                repairs.create_return(drzwi, _reason(), None, 'cokolwiek', target, extra)
            with pytest.raises(ValueError, match='operację'):
                repairs.create_return(drzwi, _reason(), None, 'poprawka', None, extra)
            with pytest.raises(ValueError, match='operację'):
                repairs.create_return(drzwi, _reason(), None, 'poprawka', 999999, extra)
            PkjOperation.query.filter_by(name='Szlifowanie').update({'is_active': False})
            with pytest.raises(ValueError, match='dodatkowa'):
                repairs.create_return(drzwi, _reason(), None, 'poprawka', target, extra)
            PkjOperation.query.filter_by(name='Szlifowanie').update({'is_active': True})
            db.session.rollback()
            assert PkjReturn.query.count() == 0 and PkjOrder.query.filter_by(type='ZN').count() == 0
            repairs.create_return(drzwi, _reason(), None, 'poprawka', target, extra)
            with pytest.raises(ValueError):
                repairs.create_return(drzwi, _reason(), None, 'poprawka', target, extra)

    def test_scrap_builds_route_up_to_inspected_operation(self, app):
        with app.app_context():
            service.set_setting('scrap_mode', 'with_cost')
            _failed_welding_control()
            ret = _return_drzwi('zlom', extra=())
            zn = db.session.get(PkjOrder, ret.repair_order_id)
            assert [o.operation_name for o in ord_.operations_of(zn.id)] == ['Wycinanie', 'Gięcie', 'Spawanie']
            drzwi = _mat('Drzwi')
            assert (drzwi.element_status, drzwi.rw_status) == ('zlom', 'anulowane')
            rozchod = PkjEvent.query.filter_by(order_id=_zp().id, type='rozchod').one()
            assert 'Drzwi' in rozchod.text and '350 PLN' in rozchod.text

    def test_scrap_without_cost_has_no_amount(self, app):
        with app.app_context():
            _failed_welding_control()
            _return_drzwi('zlom', extra=())
            assert 'PLN' not in PkjEvent.query.filter_by(order_id=_zp().id, type='rozchod').one().text

    def test_interrupted_inspection_also_needs_return(self, app):
        with app.app_context():
            insp = _failed_welding_control(names={'Drzwi'}, interrupt=True)
            assert [m.material_name for m in repairs.needs_return(insp.order_id)] == ['Drzwi']
            pim, rows = repairs.failed_points(_mat('Drzwi'))
            assert pim.interrupted is True and rows == []
            ret = _return_drzwi()
            assert ret.inspection_id == insp.id and ret.result_id is None


class TestApproval:
    def test_manager_mode_waits_for_approval(self, app):
        with app.app_context():
            service.set_setting('approval_mode', 'manager')
            _failed_welding_control()
            ret = _return_drzwi()
            assert ret.status == 'do_zatwierdzenia' and ret.repair_order_id is None
            assert PkjOrder.query.filter_by(type='ZN').count() == 0
            assert _mat('Drzwi').element_status == 'zawrocony'
            assert repairs.needs_return(_zp().id) == []              # zawrót już zgłoszony
            with pytest.raises(ValueError):                           # drugi zawrót tego samego materiału
                _return_drzwi()
            repairs.approve_return(ret, 'kierownik')
            db.session.commit()
            assert ret.status == 'w_naprawie' and db.session.get(PkjOrder, ret.repair_order_id).number.startswith('ZN-')
            assert _mat('Drzwi').element_status == 'w_naprawie'
            with pytest.raises(ValueError):
                repairs.approve_return(ret, 'kierownik')

    def test_approve_via_route_requires_admin(self, client, app):
        with app.app_context():
            service.set_setting('approval_mode', 'manager')
            _failed_welding_control()
            rid = _return_drzwi().id
        login(client, 'oper', 'Oper1234!')
        assert client.post(f'/kj-prestiz/zawroty/{rid}/zatwierdz',
                           data={'_csrf_token': _csrf(client)}).status_code == 403
        client.get('/logout')
        _admin(client)
        html = client.get('/kj-prestiz/zawroty').get_data(as_text=True)
        assert 'Do zatwierdzenia' in html and 'Zatwierdź' in html
        resp = client.post(f'/kj-prestiz/zawroty/{rid}/zatwierdz', data={'_csrf_token': _csrf(client)},
                           follow_redirects=True)
        assert 'utworzono zlecenie naprawcze' in resp.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(PkjReturn, rid).status == 'w_naprawie'


# ── Naprawa, kontrola sprawdzająca, zamknięcie ────────────────────────────────

class TestRepairAndRecheck:
    def test_repair_operations_then_element_returns_to_control(self, app):
        with app.app_context():
            _failed_welding_control()
            ret = _return_drzwi()
            zn = db.session.get(PkjOrder, ret.repair_order_id)
            ops = ord_.operations_of(zn.id)
            with pytest.raises(ValueError, match='Spawanie'):          # kolejność operacji ZN
                ord_.report_operation(ops[1], _emp('Irena Czajka'))
            ord_.report_operation(ops[0], _emp('Dorota Kos'), actor='t')
            db.session.commit()
            assert db.session.get(PkjOrder, zn.id).status == 'w_produkcji'
            assert _mat('Drzwi').element_status == 'w_naprawie'
            assert PkjEvent.query.filter_by(order_id=zn.id, type='rw_wydane').count() == 0     # ZN nie wydaje RW
            ord_.report_operation(ops[1], _emp('Irena Czajka'), actor='t')
            db.session.commit()
            assert db.session.get(PkjOrder, zn.id).status == 'zakonczone'
            assert _mat('Drzwi').element_status == 'do_ponownej_kontroli'
            assert db.session.get(PkjReturn, ret.id).status == 'do_ponownej_kontroli'
            assert PkjEvent.query.filter_by(order_id=_zp().id, type='do_ponownej_kontroli').count() == 1

    def test_recheck_has_only_failed_points_and_closes_everything(self, app):
        with app.app_context():
            first = _failed_welding_control()
            ret = _return_drzwi()
            zn = db.session.get(PkjOrder, ret.repair_order_id)
            _report_all(zn)
            oop = _op(_zp(), 'Spawanie')
            assert [m.material_name for m in insp_.recheck_materials(oop)] == ['Drzwi']
            insp = insp_.start_inspection(oop, [_mat('Drzwi').id], _user())
            db.session.commit()
            assert insp.round == 2 and insp.previous_id == first.id
            sections = insp_.sections(insp.id)
            assert [s['name'] for s in sections] == ['Drzwi']
            (only,) = sections[0]['results']
            assert only.title == 'Spoina narożna kompletna' and only.photo_required_on_nok is True
            assert only.point_id and only.result is None
            assert _mat('Drzwi').element_status == 'w_kontroli'
            insp_.record_result(only, 'OK')
            summary = insp_.finish_inspection(insp, 't')
            db.session.commit()
            assert summary == {'ok': ['Drzwi'], 'nok': []}
            drzwi = _mat('Drzwi')
            assert (drzwi.element_status, drzwi.rw_status) == ('zgodny', 'wydane')
            assert db.session.get(PkjReturn, ret.id).status == 'zamkniety'
            assert db.session.get(PkjOrder, zn.id).status == 'zamkniete'
            assert _op(_zp(), 'Spawanie').status == 'zakonczona'
            assert db.session.get(PkjOrder, _zp().id).status == 'w_produkcji'
            assert PkjEvent.query.filter_by(order_id=zn.id, type='zn_zamkniete').count() == 1
            assert ord_.refresh_status(db.session.get(PkjOrder, zn.id)) == 'zamkniete'     # zamknięte nie wraca

    def test_recheck_nok_again_opens_new_return(self, app):
        with app.app_context():
            _failed_welding_control()
            ret = _return_drzwi()
            zn = db.session.get(PkjOrder, ret.repair_order_id)
            _report_all(zn)
            oop = _op(_zp(), 'Spawanie')
            insp = insp_.start_inspection(oop, [_mat('Drzwi').id], _user())
            (only,) = PkjResult.query.filter_by(inspection_id=insp.id).all()
            insp_.record_result(only, 'NOK')
            db.session.add(PkjPhoto(result_id=only.id, filename='y.jpg'))
            db.session.flush()
            insp_.finish_inspection(insp, 't')
            db.session.commit()
            assert db.session.get(PkjReturn, ret.id).status == 'zamkniety'
            assert db.session.get(PkjOrder, zn.id).status == 'zakonczone'      # nie zamknięte
            assert _mat('Drzwi').element_status == 'zawrocony'
            assert [m.material_name for m in repairs.needs_return(_zp().id)] == ['Drzwi']
            target, extra = _repair_plan(extra=())
            ret2 = repairs.create_return(_mat('Drzwi'), _reason(), None, 'poprawka', target, [])
            assert db.session.get(PkjOrder, ret2.repair_order_id).number == 'ZN-ZP-DEMO-001/2'
            assert len(repairs.returns_of_order(_zp().id)) == 2

    def test_scrapped_element_comes_back_as_new_one(self, app):
        with app.app_context():
            _failed_welding_control()
            ret = _return_drzwi('zlom', extra=())
            zn = db.session.get(PkjOrder, ret.repair_order_id)
            _report_all(zn)                                      # Wycinanie, Gięcie, Spawanie (naprawcze)
            drzwi = _mat('Drzwi')
            assert (drzwi.element_status, drzwi.rw_status) == ('do_ponownej_kontroli', 'wstrzymane')
            insp = insp_.start_inspection(_op(_zp(), 'Spawanie'), [drzwi.id], _user())
            assert insp.round == 2

    def test_do_not_mix_first_and_recheck_materials(self, app):
        with app.app_context():
            _failed_welding_control(names={'Drzwi'})
            ret = _return_drzwi()
            _report_all(db.session.get(PkjOrder, ret.repair_order_id))
            oop = _op(_zp(), 'Spawanie')
            with pytest.raises(ValueError, match='Nie łącz'):
                insp_.start_inspection(oop, [_mat('Drzwi').id, _mat('Korpus').id], _user())

    def test_recheck_requires_previous_failed_inspection(self, app):
        with app.app_context():
            _failed_welding_control()
            drzwi = _mat('Drzwi')
            drzwi.element_status = 'do_ponownej_kontroli'
            PkjInspectionMaterial.query.update({'verdict': 'OK'})
            db.session.commit()
            with pytest.raises(ValueError, match='Brak poprzedniej'):
                insp_.start_inspection(_op(_zp(), 'Spawanie'), [drzwi.id], _user())

    def test_deleting_order_removes_repair_orders_and_returns(self, app):
        with app.app_context():
            _failed_welding_control()
            _return_drzwi()
            assert ord_.delete_orders([_zp().id]) == 2
            assert PkjOrder.query.filter_by(type='ZN').count() == 0
            assert PkjReturn.query.count() == 0 and PkjReturnOperation.query.count() == 0


# ── Ekrany ────────────────────────────────────────────────────────────────────

class TestScreens:
    def test_mobile_flow_to_repair_order(self, client, app):
        _admin(client)
        with app.app_context():
            insp = _failed_welding_control()
            oid, drzwi_id = insp.order_id, _mat('Drzwi').id
            target = _op(_zp(), 'Spawanie').id
            reason, szl = _reason(), PkjOperation.query.filter_by(name='Szlifowanie').one().id
        page = client.get(f'/kj-prestiz/m/zlecenie/{oid}').get_data(as_text=True)
        assert 'Zgłoś zawrót: Drzwi' in page
        done = client.get(f'/kj-prestiz/m/kontrola/{insp.id}').get_data(as_text=True)
        assert 'Zgłoś zawrót: Drzwi' in done and 'Wymagany zawrót' in done
        form = client.get(f'/kj-prestiz/m/zawrot/nowy/{drzwi_id}').get_data(as_text=True)
        assert 'Spoina narożna kompletna' in form and 'Spoina niepełna' in form and 'Szlifowanie' in form
        assert 'value="poprawka"' in form and 'value="zlom"' in form
        token = _csrf(client)
        bad = client.post(f'/kj-prestiz/m/zawrot/nowy/{drzwi_id}', data={'decision': 'poprawka', 'target': target,
                                                                       '_csrf_token': token})
        assert bad.status_code == 200 and 'Wybierz przyczynę' in bad.get_data(as_text=True)
        ok = client.post(f'/kj-prestiz/m/zawrot/nowy/{drzwi_id}',
                         data={'decision': 'poprawka', 'target': target, 'extra': [szl], 'reason_id': reason,
                               'description': 'Dospawać narożnik', '_csrf_token': token})
        assert ok.status_code == 302
        summary = client.get(ok.headers['Location']).get_data(as_text=True)
        assert 'Utworzono ZN-ZP-DEMO-001/1' in summary and 'Spawanie' in summary and 'Szlifowanie (ręcznie)' in summary
        again = client.get(f'/kj-prestiz/m/zawrot/nowy/{drzwi_id}')
        assert again.status_code == 302                       # zawrót już zgłoszony
        order_page = client.get(f'/kj-prestiz/m/zlecenie/{oid}').get_data(as_text=True)
        assert 'Zgłoś zawrót' not in order_page and 'ZN-ZP-DEMO-001/1' in order_page

    def test_mobile_scrap_via_form(self, client, app):
        _admin(client)
        with app.app_context():
            _failed_welding_control()
            drzwi_id, reason = _mat('Drzwi').id, _reason()
        resp = client.post(f'/kj-prestiz/m/zawrot/nowy/{drzwi_id}',
                           data={'decision': 'zlom', 'reason_id': reason, '_csrf_token': _csrf(client)})
        assert resp.status_code == 302
        with app.app_context():
            ret = PkjReturn.query.one()
            assert ret.decision == 'zlom' and PkjReturnOperation.query.filter_by(return_id=ret.id).count() == 3

    def test_mobile_recheck_offered_after_repair(self, client, app):
        _admin(client)
        with app.app_context():
            _failed_welding_control()
            ret = _return_drzwi()
            _report_all(db.session.get(PkjOrder, ret.repair_order_id))
            oid = _zp().id
        html = client.get(f'/kj-prestiz/m/zlecenie/{oid}').get_data(as_text=True)
        assert 'Kontrola sprawdzająca po naprawie' in html and 'Rozpocznij kontrolę sprawdzającą' in html

    def test_registry_and_detail_pages(self, client, app):
        _admin(client)
        with app.app_context():
            _failed_welding_control()
            ret = _return_drzwi()
            rid, zn_id, zp_id = ret.id, ret.repair_order_id, _zp().id
        html = client.get('/kj-prestiz/zawroty').get_data(as_text=True)
        assert 'ZP-DEMO-001' in html and 'ZN-ZP-DEMO-001/1' in html and 'Spoina niepełna' in html
        assert 'class="rs rs-warn"' in html and 'Poprawka' in html
        assert 'ZP-DEMO-001' in client.get('/kj-prestiz/zawroty?q=drzwi').get_data(as_text=True)
        assert 'Brak zawrotów' in client.get('/kj-prestiz/zawroty?q=nic').get_data(as_text=True)
        assert 'Brak zawrotów' in client.get('/kj-prestiz/zawroty?s=zamkniety').get_data(as_text=True)
        detail = client.get(f'/kj-prestiz/zawroty/{rid}').get_data(as_text=True)
        assert 'Spoina narożna kompletna' in detail and 'Dospawać narożnik' in detail and 'x.jpg' in detail
        assert client.get('/kj-prestiz/zawroty/99999').status_code == 404
        zp_page = client.get(f'/kj-prestiz/zlecenia/{zp_id}').get_data(as_text=True)
        assert 'Zawroty' in zp_page and 'ZN-ZP-DEMO-001/1' in zp_page and 'zawrót' in zp_page.lower()
        zn_page = client.get(f'/kj-prestiz/zlecenia/{zn_id}').get_data(as_text=True)
        assert 'Zawrót: Spoina niepełna' in zn_page and 'zlecenie naprawcze' in zn_page
        assert 'Melduj operację' in zn_page
        assert 'ZN-ZP-DEMO-001/1' in client.get('/kj-prestiz/zlecenia?q=ZN-').get_data(as_text=True)

    def test_zn_report_via_desktop_route_closes_cycle(self, client, app):
        _admin(client)
        with app.app_context():
            _failed_welding_control()
            ret = _return_drzwi(extra=())
            zn = db.session.get(PkjOrder, ret.repair_order_id)
            zn_id, oop_id, emp = zn.id, ord_.operations_of(zn.id)[0].id, _emp('Cezary Lis').id
        resp = client.post(f'/kj-prestiz/zlecenia/{zn_id}/melduj',
                           data={'oop_id': oop_id, 'employee_id': emp, '_csrf_token': _csrf(client)},
                           follow_redirects=True)
        assert 'Zameldowano operację Spawanie' in resp.get_data(as_text=True)
        with app.app_context():
            assert _mat('Drzwi').element_status == 'do_ponownej_kontroli'
            assert db.session.get(PkjOrder, zn_id).status == 'zakonczone'

    def test_access_control(self, client, app):
        with app.app_context():
            _failed_welding_control()
            rid, mid = _return_drzwi().id, _mat('Drzwi').id
        login(client, 'oper', 'Oper1234!')
        for url in ('/kj-prestiz/zawroty', f'/kj-prestiz/zawroty/{rid}', f'/kj-prestiz/m/zawrot/{rid}',
                    f'/kj-prestiz/m/zawrot/nowy/{mid}'):
            assert client.get(url).status_code == 403

    def test_settings_scrap_cost(self, client, app):
        _admin(client)
        client.post('/kj-prestiz/konfiguracja/ustawienia/zapisz-wszystko',
                    data={'scrap_material_cost': '410,5', '_csrf_token': _csrf(client)})
        with app.app_context():
            assert service.get_setting('scrap_material_cost') == '410.5'
        assert 'Koszt materiału jednego zezłomowanego elementu' in client.get(
            '/kj-prestiz/konfiguracja/ustawienia').get_data(as_text=True)
