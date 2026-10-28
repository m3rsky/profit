"""Testy modułu poglądowego KJ Prestiż, etap 1: rejestracja, uprawnienia, izolacja od innych modułów,
konfiguracja (operacje, materiały, grupy, punkty, przyczyny, pracownicy, ustawienia) oraz dane demo."""
import io
import os

import pytest
from PIL import Image

from app import app as flask_app, db, NAV_DEFAULT
from models import (MODULES, DEFAULT_PERMISSIONS, ControlPoint, ControlRecord, DepartmentEmployee,
                    ProductionDepartment, QARReport, User)
from prestiz_kj.models import (PkjOperation, PkjMaterial, PkjMaterialGroup, PkjCheckPoint, PkjReason,
                               PkjEmployee, PkjSetting, PkjOrder, PkjInspection, PkjResult)
from prestiz_kj import demo_data, service
from test_app import login, logout, _csrf


@pytest.fixture(autouse=True)
def _clean(app, tmp_path):
    flask_app.config['PKJ_UPLOAD_FOLDER'] = str(tmp_path / 'pkj')
    with app.app_context():
        service.reset_all()
    yield
    with app.app_context():
        service.reset_all()


def _admin(client):
    login(client, 'admin', 'Admin1234!')
    return client


def _post(client, url, data=None, **kw):
    data = dict(data or {})
    data['_csrf_token'] = _csrf(client)
    return client.post(url, data=data, follow_redirects=True, **kw)


def _seed(app):
    with app.app_context():
        assert demo_data.seed_configuration()


def _png():
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(buf, 'PNG')
    buf.seek(0)
    return buf


# ── Rejestracja i uprawnienia ─────────────────────────────────────────────────

class TestAccess:
    def test_module_registered(self):
        assert MODULES['kj_prestiz'] == 'KJ Prestiż (pogląd)'
        assert 'kj_prestiz' in DEFAULT_PERMISSIONS['administrator']
        assert 'kj_prestiz' not in DEFAULT_PERMISSIONS['kontroler']
        assert 'kj_prestiz' in NAV_DEFAULT

    def test_anonymous_redirected(self, client):
        assert client.get('/kj-prestiz/').status_code in (301, 302)

    def test_controller_without_permission_gets_403(self, client):
        login(client, 'oper', 'Oper1234!')
        assert client.get('/kj-prestiz/').status_code == 403
        assert client.get('/kj-prestiz/konfiguracja/operacje').status_code == 403
        assert client.post('/kj-prestiz/demo/wczytaj', data={'_csrf_token': _csrf(client)}).status_code == 403

    def test_admin_and_superuser_allowed(self, client):
        _admin(client)
        assert client.get('/kj-prestiz/').status_code == 200
        logout(client)
        login(client, 'super', 'Super1234!')
        assert client.get('/kj-prestiz/').status_code == 200

    def test_menu_entry_for_admin_only(self, client):
        _admin(client)
        assert 'KJ Prestiż (pogląd)' in client.get('/kj-prestiz/').get_data(as_text=True)
        logout(client)
        login(client, 'oper', 'Oper1234!')
        assert 'data-nav-key="kj_prestiz"' not in client.get('/kontrola/').get_data(as_text=True)

    def test_config_pages_render(self, client, app):
        _seed(app)
        _admin(client)
        for slug in ('operacje', 'materialy', 'punkty', 'przyczyny', 'pracownicy', 'ustawienia'):
            assert client.get(f'/kj-prestiz/konfiguracja/{slug}').status_code == 200, slug
        assert client.get('/kj-prestiz/konfiguracja/nie-ma').status_code == 404

    def test_tables_exist(self, app):
        from sqlalchemy import inspect
        names = set(inspect(db.engine).get_table_names())
        assert {'pkj_operations', 'pkj_materials', 'pkj_check_points', 'pkj_orders', 'pkj_results',
                'pkj_events', 'pkj_return_operations'} <= names


# ── Dane demo i izolacja ──────────────────────────────────────────────────────

class TestDemo:
    def test_load_creates_configuration_once(self, client, app):
        _admin(client)
        _post(client, '/kj-prestiz/demo/wczytaj')
        with app.app_context():
            ops = PkjOperation.query.count()
            assert ops == 6 and PkjOperation.query.filter_by(kj_required=True).count() == 4
            assert PkjMaterial.query.filter_by(kj_flag=True).count() == 3
            assert PkjMaterial.query.filter_by(name='Aparatura').one().kj_flag is False
            assert PkjMaterialGroup.query.count() == 1
            assert PkjReason.query.count() == 6 and PkjEmployee.query.count() == 9
            points = PkjCheckPoint.query.count()
            assert {p.scope for p in PkjCheckPoint.query} == {'all', 'operation', 'material', 'group'}
        resp = _post(client, '/kj-prestiz/demo/wczytaj')
        assert 'już istnieją' in resp.get_data(as_text=True)
        with app.app_context():
            assert PkjOperation.query.count() == ops and PkjCheckPoint.query.count() == points

    def test_value_points_have_ranges(self, app):
        _seed(app)
        with app.app_context():
            vals = PkjCheckPoint.query.filter_by(kind='value').all()
            assert vals and all(p.min_value is not None and p.max_value is not None and p.unit for p in vals)

    def test_reset_orders_keeps_configuration(self, client, app):
        _seed(app)
        with app.app_context():
            db.session.add(PkjOrder(number='ZP-T-1', product='Szafa'))
            db.session.commit()
        _admin(client)
        _post(client, '/kj-prestiz/demo/reset')
        with app.app_context():
            assert PkjOrder.query.count() == 0 and PkjOperation.query.count() == 6

    def test_reset_all_clears_only_pkj_tables(self, client, app):
        _seed(app)
        with app.app_context():
            dept = ProductionDepartment(name='T-KJ Dział', order=990)
            db.session.add(dept)
            db.session.flush()
            db.session.add(DepartmentEmployee(department_id=dept.id, name='T-KJ Osoba'))
            db.session.add(ControlPoint(title='T-KJ punkt'))
            db.session.commit()
            before = (User.query.count(), ControlPoint.query.count(), DepartmentEmployee.query.count(),
                      ProductionDepartment.query.count(), ControlRecord.query.count(), QARReport.query.count())
        _admin(client)
        _post(client, '/kj-prestiz/demo/reset-wszystko')
        with app.app_context():
            assert PkjOperation.query.count() == 0 and PkjCheckPoint.query.count() == 0
            assert PkjSetting.query.count() == 0
            after = (User.query.count(), ControlPoint.query.count(), DepartmentEmployee.query.count(),
                     ProductionDepartment.query.count(), ControlRecord.query.count(), QARReport.query.count())
            assert before == after
            DepartmentEmployee.query.filter_by(name='T-KJ Osoba').delete()
            ControlPoint.query.filter_by(title='T-KJ punkt').delete()
            ProductionDepartment.query.filter_by(name='T-KJ Dział').delete()
            db.session.commit()

    def test_config_changes_do_not_touch_other_modules(self, client, app):
        _seed(app)
        with app.app_context():
            before = (ControlPoint.query.count(), DepartmentEmployee.query.count(),
                      ProductionDepartment.query.count())
        _admin(client)
        _post(client, '/kj-prestiz/konfiguracja/operacje/zapisz-wszystko', {'new_name': 'Pakowanie'})
        _post(client, '/kj-prestiz/konfiguracja/pracownicy/zapisz-wszystko', {'new_name': 'Nowy Demo'})
        with app.app_context():
            assert before == (ControlPoint.query.count(), DepartmentEmployee.query.count(),
                              ProductionDepartment.query.count())


# ── Konfiguracja ──────────────────────────────────────────────────────────────

class TestOperations:
    def test_add_rename_flag_deactivate(self, client, app):
        _seed(app)
        _admin(client)
        with app.app_context():
            op = PkjOperation.query.filter_by(name='Wycinanie').one()
            oid = op.id
        _post(client, '/kj-prestiz/konfiguracja/operacje/zapisz-wszystko', {
            f'name_{oid}': 'Wycinanie laserowe', f'kj_{oid}': '1',   # brak active_ = nieaktywna
            'new_name': 'Pakowanie', 'new_kj': '1'})
        with app.app_context():
            op = db.session.get(PkjOperation, oid)
            assert op.name == 'Wycinanie laserowe' and op.kj_required is True and op.is_active is False
            new = PkjOperation.query.filter_by(name='Pakowanie').one()
            assert new.kj_required is True and new.order == 7

    def test_duplicate_name_rejected(self, client, app):
        _seed(app)
        _admin(client)
        resp = _post(client, '/kj-prestiz/konfiguracja/operacje/zapisz-wszystko', {'new_name': 'spawanie'})
        assert 'już istnieje' in resp.get_data(as_text=True)
        with app.app_context():
            assert PkjOperation.query.count() == 6

    def test_move_and_delete(self, client, app):
        _seed(app)
        _admin(client)
        with app.app_context():
            ops = PkjOperation.query.order_by(PkjOperation.order).all()
            first, second = ops[0].id, ops[1].id
            free = PkjOperation.query.filter_by(name='Wycinanie').one().id
        _post(client, f'/kj-prestiz/konfiguracja/operacje/{second}/przesun/up')
        with app.app_context():
            assert PkjOperation.query.order_by(PkjOperation.order).first().id == second
            assert db.session.get(PkjOperation, first).order == 2
        # Wycinanie nie ma punktów, więc da się je usunąć
        _post(client, f'/kj-prestiz/konfiguracja/operacje/{free}/usun')
        with app.app_context():
            assert db.session.get(PkjOperation, free) is None

    def test_delete_blocked_when_points_assigned(self, client, app):
        _seed(app)
        _admin(client)
        with app.app_context():
            oid = PkjOperation.query.filter_by(name='Spawanie').one().id
        resp = _post(client, f'/kj-prestiz/konfiguracja/operacje/{oid}/usun')
        assert 'punkty kontrolne' in resp.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(PkjOperation, oid) is not None

    def test_non_admin_cannot_save(self, client, app):
        _seed(app)
        login(client, 'oper', 'Oper1234!')
        assert client.post('/kj-prestiz/konfiguracja/operacje/zapisz-wszystko',
                           data={'new_name': 'X', '_csrf_token': _csrf(client)}).status_code == 403
        with app.app_context():
            assert PkjOperation.query.filter_by(name='X').count() == 0


class TestMaterials:
    def test_group_and_flag(self, client, app):
        _seed(app)
        _admin(client)
        _post(client, '/kj-prestiz/konfiguracja/materialy/zapisz-wszystko', {'new_group': 'Wnętrze'})
        with app.app_context():
            gid = PkjMaterialGroup.query.filter_by(name='Wnętrze').one().id
            aid = PkjMaterial.query.filter_by(name='Aparatura').one().id
        _post(client, '/kj-prestiz/konfiguracja/materialy/zapisz-wszystko', {
            f'name_{aid}': 'Aparatura', f'kj_{aid}': '1', f'group_{aid}': str(gid), f'active_{aid}': '1'})
        with app.app_context():
            m = db.session.get(PkjMaterial, aid)
            assert m.kj_flag is True and m.group_id == gid

    def test_group_delete_blocked_then_allowed(self, client, app):
        _seed(app)
        _admin(client)
        with app.app_context():
            used = PkjMaterialGroup.query.filter_by(name='Obudowa').one().id
        resp = _post(client, f'/kj-prestiz/konfiguracja/grupy/{used}/usun')
        assert 'ma przypisane' in resp.get_data(as_text=True)
        _post(client, '/kj-prestiz/konfiguracja/materialy/zapisz-wszystko', {'new_group': 'Pusta'})
        with app.app_context():
            empty = PkjMaterialGroup.query.filter_by(name='Pusta').one().id
        _post(client, f'/kj-prestiz/konfiguracja/grupy/{empty}/usun')
        with app.app_context():
            assert db.session.get(PkjMaterialGroup, empty) is None
            assert db.session.get(PkjMaterialGroup, used) is not None


class TestPoints:
    def _field(self, **over):
        data = {'new_title': 'Test wartości', 'new_kind': 'value', 'new_min': '1,5', 'new_max': '3',
                'new_unit': 'mm', 'new_target': 'all', 'new_photo': '1'}
        data.update(over)
        return data

    def test_add_value_point_with_decimal_comma(self, client, app):
        _seed(app)
        _admin(client)
        _post(client, '/kj-prestiz/konfiguracja/punkty/zapisz-wszystko', self._field())
        with app.app_context():
            p = PkjCheckPoint.query.filter_by(title='Test wartości').one()
            assert (p.min_value, p.max_value, p.unit, p.scope) == (1.5, 3.0, 'mm', 'all')
            assert p.photo_required_on_nok is True

    def test_targets_set_scope_and_ids(self, client, app):
        _seed(app)
        _admin(client)
        with app.app_context():
            op = PkjOperation.query.filter_by(name='Spawanie').one().id
            mat = PkjMaterial.query.filter_by(name='Drzwi').one().id
            grp = PkjMaterialGroup.query.one().id
        for target, scope in ((f'op:{op}', 'operation'), (f'mat:{mat}', 'material'), (f'grp:{grp}', 'group')):
            _post(client, '/kj-prestiz/konfiguracja/punkty/zapisz-wszystko',
                  self._field(new_title=f'T {scope}', new_target=target, new_kind='ok_nok'))
        with app.app_context():
            p = PkjCheckPoint.query.filter_by(title='T operation').one()
            assert p.scope == 'operation' and p.operation_id == op and p.material_id is None
            p = PkjCheckPoint.query.filter_by(title='T material').one()
            assert p.scope == 'material' and p.material_id == mat and p.target_label == 'Materiał: Drzwi'
            assert PkjCheckPoint.query.filter_by(title='T group').one().group_id == grp
            assert PkjCheckPoint.query.filter_by(title='T group').one().min_value is None   # ok_nok bez zakresu

    def test_invalid_input_rejected(self, client, app):
        _seed(app)
        _admin(client)
        with app.app_context():
            before = PkjCheckPoint.query.count()
        for bad in (self._field(new_min='5', new_max='2'), self._field(new_min='abc'),
                    self._field(new_target='op:99999'), self._field(new_target='xyz:1')):
            _post(client, '/kj-prestiz/konfiguracja/punkty/zapisz-wszystko', bad)
        with app.app_context():
            assert PkjCheckPoint.query.count() == before

    def test_edit_existing_point(self, client, app):
        _seed(app)
        _admin(client)
        with app.app_context():
            pid = PkjCheckPoint.query.filter_by(title='Wymiar gabarytowy').one().id
        _post(client, '/kj-prestiz/konfiguracja/punkty/zapisz-wszystko', {
            f'title_{pid}': 'Wymiar gabarytowy 800', f'kind_{pid}': 'value', f'min_{pid}': '798',
            f'max_{pid}': '802', f'unit_{pid}': 'mm', f'target_{pid}': 'all', f'active_{pid}': '1'})
        with app.app_context():
            p = db.session.get(PkjCheckPoint, pid)
            assert p.title == 'Wymiar gabarytowy 800' and p.min_value == 798 and p.scope == 'all'
            assert p.photo_required_on_nok is False

    def test_filter_by_scope(self, client, app):
        _seed(app)
        _admin(client)
        html = client.get('/kj-prestiz/konfiguracja/punkty?zakres=group').get_data(as_text=True)
        assert 'Zgodność kolorów korpusu i drzwi' in html and 'Brak śladów korozji' not in html

    def test_delete_keeps_result_history(self, client, app):
        _seed(app)
        _admin(client)
        with app.app_context():
            pid = PkjCheckPoint.query.first().id
            order = PkjOrder(number='ZP-T-2')
            db.session.add(order)
            db.session.flush()
            insp = PkjInspection(order_id=order.id)
            db.session.add(insp)
            db.session.flush()
            res = PkjResult(inspection_id=insp.id, point_id=pid, title='Kopia treści punktu', result='OK')
            db.session.add(res)
            db.session.commit()
            rid = res.id
        _post(client, f'/kj-prestiz/konfiguracja/punkty/{pid}/usun')
        with app.app_context():
            assert db.session.get(PkjCheckPoint, pid) is None
            r = db.session.get(PkjResult, rid)
            assert r.title == 'Kopia treści punktu' and r.point_id is None

    def test_image_upload_validated(self, client, app):
        _seed(app)
        _admin(client)
        data = self._field(new_title='Z grafiką', new_kind='ok_nok')
        data['new_image'] = (_png(), 'obraz.png')
        _post(client, '/kj-prestiz/konfiguracja/punkty/zapisz-wszystko', data, content_type='multipart/form-data')
        with app.app_context():
            p = PkjCheckPoint.query.filter_by(title='Z grafiką').one()
            assert p.image and os.path.isfile(os.path.join(flask_app.config['PKJ_UPLOAD_FOLDER'], p.image))
            name = p.image
        assert client.get(f'/kj-prestiz/grafika/{name}').status_code == 200
        bad = self._field(new_title='Zły plik', new_kind='ok_nok')
        bad['new_image'] = (io.BytesIO(b'to nie jest obraz'), 'falszywy.png')
        resp = _post(client, '/kj-prestiz/konfiguracja/punkty/zapisz-wszystko', bad,
                     content_type='multipart/form-data')
        assert 'nie jest prawidłowym obrazem' in resp.get_data(as_text=True)
        with app.app_context():
            assert PkjCheckPoint.query.filter_by(title='Zły plik').one().image is None


class TestReasonsEmployeesSettings:
    def test_reasons_crud(self, client, app):
        _seed(app)
        _admin(client)
        _post(client, '/kj-prestiz/konfiguracja/przyczyny/zapisz-wszystko', {'new_name': 'Brak lakieru'})
        with app.app_context():
            rid = PkjReason.query.filter_by(name='Brak lakieru').one().id
        _post(client, f'/kj-prestiz/konfiguracja/przyczyny/{rid}/usun')
        with app.app_context():
            assert db.session.get(PkjReason, rid) is None

    def test_employees_are_independent_of_profit_staff(self, client, app):
        _seed(app)
        _admin(client)
        _post(client, '/kj-prestiz/konfiguracja/pracownicy/zapisz-wszystko',
              {'new_name': 'Jan Demo', 'new_dept': 'Spawanie'})
        with app.app_context():
            assert PkjEmployee.query.filter_by(name='Jan Demo', department='Spawanie').count() == 1
            assert DepartmentEmployee.query.filter_by(name='Jan Demo').count() == 0

    def test_settings_save_and_validate(self, client, app):
        _seed(app)
        _admin(client)
        _post(client, '/kj-prestiz/konfiguracja/ustawienia/zapisz-wszystko', {
            'approval_mode': 'manager', 'scrap_mode': 'with_cost', 'repair_hour_cost': '150,5'})
        with app.app_context():
            assert service.get_setting('approval_mode') == 'manager'
            assert service.get_setting('scrap_mode') == 'with_cost'
            assert service.get_setting('repair_hour_cost') == '150.5'
        _post(client, '/kj-prestiz/konfiguracja/ustawienia/zapisz-wszystko', {
            'approval_mode': 'cokolwiek', 'repair_hour_cost': '-5'})
        with app.app_context():
            assert service.get_setting('approval_mode') == 'manager'
            assert service.get_setting('repair_hour_cost') == '150.5'
