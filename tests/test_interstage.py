"""Testy modułu Kontrola międzyetapowa (dawniej Spawalnia)."""
from app import db
from models import (SpawalniaRecord, InterstageCheck, QARReport, QARCategory,
                    ProductionDepartment, DepartmentEmployee, INTERSTAGE_STAGES)
from test_app import login, logout, _csrf


def _setup(app):
    with app.app_context():
        for name in ('Gięcie', 'Spawanie'):
            if not QARCategory.query.filter_by(name=name).first():
                db.session.add(QARCategory(name=name))
        dept = ProductionDepartment.query.filter_by(name='Gięcie').first()
        if not dept:
            dept = ProductionDepartment(name='Gięcie', order=99)
            db.session.add(dept)
            db.session.flush()
        emp = DepartmentEmployee.query.filter_by(name='Adam Giętarz').first()
        if not emp:
            emp = DepartmentEmployee(department_id=dept.id, name='Adam Giętarz')
            db.session.add(emp)
        db.session.commit()
        return emp.id


def _from_qr(client, **kw):
    payload = {'p': 'Szafa X', 'c': 'Klient', 'o': 'ZO-IS-1', 'q': 2}
    payload.update(kw)
    return client.post('/spawalnia/from-qr', json=payload,
                       headers={'X-CSRF-Token': _csrf(client)})


class TestInterstage:
    def test_from_qr_creates_series_with_all_stages(self, client, app):
        login(client, 'oper', 'Oper1234!')
        resp = _from_qr(client)
        assert resp.status_code == 200 and resp.get_json()['ok']
        with app.app_context():
            recs = SpawalniaRecord.query.filter_by(zo_number='ZO-IS-1').all()
            assert len(recs) == 2
            assert all(len(r.checks) == len(INTERSTAGE_STAGES) for r in recs)
            assert recs[0].product_name == 'Szafa X'
        logout(client)

    def test_from_qr_existing_zo_opens_it(self, client):
        login(client, 'oper', 'Oper1234!')
        _from_qr(client, o='ZO-IS-2', q=1)
        again = _from_qr(client, o='ZO-IS-2', q=1)
        assert 'już istnieje' in again.get_json()['msg']
        logout(client)

    def test_from_qr_requires_order_number(self, client):
        login(client, 'oper', 'Oper1234!')
        assert _from_qr(client, o='').status_code == 400
        logout(client)

    def test_ng_creates_qar_and_ok_does_not(self, client, app):
        emp_id = _setup(app)
        login(client, 'oper', 'Oper1234!')
        rid = _from_qr(client, o='ZO-IS-3', q=1).get_json()['redirect'].split('/')[2]
        resp = client.post(f'/spawalnia/{rid}/edit', data={
            '_csrf_token': _csrf(client),
            'result_giecie': 'NG', 'employee_giecie': str(emp_id),
            'notes_giecie': 'Zły kąt gięcia',
            'result_spawanie': 'OK',
        })
        assert resp.status_code == 302
        with app.app_context():
            rec = db.session.get(SpawalniaRecord, int(rid))
            chk = rec.check_map
            assert chk['giecie'].result == 'NG' and chk['giecie'].qar_report_id
            assert chk['spawanie'].result == 'OK' and not chk['spawanie'].qar_report_id
            assert rec.has_ng
            qar = db.session.get(QARReport, chk['giecie'].qar_report_id)
            assert qar.category == 'Gięcie' and qar.zo_number == 'ZO-IS-3'
            assert qar.employee_id == emp_id and 'Zły kąt gięcia' in qar.description
            n_qar = QARReport.query.count()

        # ponowny zapis nie tworzy drugiego QAR dla tego samego NG
        client.post(f'/spawalnia/{rid}/edit', data={'result_giecie': 'NG', '_csrf_token': _csrf(client)})
        with app.app_context():
            assert QARReport.query.count() == n_qar
        logout(client)

    def test_stages_can_be_filled_in_any_order(self, client, app):
        login(client, 'oper', 'Oper1234!')
        rid = _from_qr(client, o='ZO-IS-4', q=1).get_json()['redirect'].split('/')[2]
        client.post(f'/spawalnia/{rid}/edit', data={'result_montaz': 'OK', '_csrf_token': _csrf(client)})
        with app.app_context():
            assert db.session.get(SpawalniaRecord, int(rid)).check_map['montaz'].result == 'OK'
        logout(client)

    def test_pages_render_and_exports(self, client, app):
        login(client, 'admin', 'Admin1234!')
        rid = _from_qr(client, o='ZO-IS-5', q=1).get_json()['redirect'].split('/')[2]
        client.post(f'/spawalnia/{rid}/edit', data={'result_ciecie_laser': 'OK', '_csrf_token': _csrf(client)})
        assert client.get('/spawalnia/').status_code == 200
        assert client.get(f'/spawalnia/{rid}/edit').status_code == 200
        assert client.get('/spawalnia/scan').status_code == 200
        assert client.get('/spawalnia/export/excel').status_code == 200
        assert client.get('/spawalnia/export/pdf').status_code == 200
        assert client.get('/spawalnia/admin/operators').status_code == 302
        logout(client)

    def test_delete_record_removes_checks(self, client, app):
        login(client, 'admin', 'Admin1234!')
        rid = int(_from_qr(client, o='ZO-IS-6', q=1).get_json()['redirect'].split('/')[2])
        client.post(f'/spawalnia/{rid}/delete', data={'_csrf_token': _csrf(client)})
        with app.app_context():
            assert InterstageCheck.query.filter_by(record_id=rid).count() == 0
        logout(client)
