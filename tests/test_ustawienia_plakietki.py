"""Plakietki pracowników działów (QR PSH-DEP), tokeny i przenoszenie pracowników między działami."""
import pytest

from app import app as flask_app, db, _backfill_department_tokens
from models import User, ProductionDepartment, DepartmentEmployee
from test_app import login, _csrf
from ustawienia.lookup import parse_badge, find_department_employee, BADGE_PREFIX

BASE = '/ustawienia-produkcji'


def post(client, url, **data):
    data['_csrf_token'] = _csrf(client)
    return client.post(BASE + url, data=data, follow_redirects=True)


@pytest.fixture(autouse=True)
def _clean(app):
    def wipe():
        with app.app_context():
            DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T1 %')).delete(
                synchronize_session=False)
            db.session.commit()
    wipe()
    yield
    wipe()


@pytest.fixture
def order_user(app):
    with app.app_context():
        if not User.query.filter_by(username='zam_etapy').first():
            u = User(username='zam_etapy', email='zam_etapy@test.pl', role='order')
            u.set_password('Order1234!')
            db.session.add(u)
            db.session.commit()
    return 'zam_etapy'


@pytest.fixture
def department(app):
    with app.app_context():
        d = ProductionDepartment(name='T1 Dział testowy', order=90)
        db.session.add(d)
        db.session.commit()
        dept_id = d.id
    yield dept_id
    with app.app_context():
        DepartmentEmployee.query.filter_by(department_id=dept_id).delete()
        ProductionDepartment.query.filter_by(id=dept_id).delete()
        db.session.commit()


class TestBadges:
    def _emp(self, app, dept_id, name='T1 Jan Kowalski', active=True):
        with app.app_context():
            e = DepartmentEmployee(department_id=dept_id, name=name, is_active=active)
            db.session.add(e)
            db.session.commit()
            return e.id, e.token

    def test_new_employee_gets_token(self, app, department):
        _, token = self._emp(app, department)
        assert token and len(token) == 32

    def test_backfill_fills_missing_tokens(self, app, department):
        eid, _ = self._emp(app, department)
        with app.app_context():
            DepartmentEmployee.query.filter_by(id=eid).update({'token': None})
            db.session.commit()
            _backfill_department_tokens()
            db.session.commit()
            assert db.session.get(DepartmentEmployee, eid).token

    def test_badge_scan_recognition(self, app, department):
        eid, token = self._emp(app, department)
        _, token2 = self._emp(app, department, name='T1 Anna Nowak', active=False)
        with app.app_context():
            assert parse_badge(f'{BADGE_PREFIX}{token}') == token
            assert parse_badge('PSH-EMP:abc') is None
            assert parse_badge('A001') is None
            assert find_department_employee(f'{BADGE_PREFIX}{token}').id == eid
            assert find_department_employee(f' psh-dep:{token} ').id == eid
            assert find_department_employee(f'{BADGE_PREFIX}{token2}') is None   # nieaktywny
            assert find_department_employee(f'{BADGE_PREFIX}nieznany') is None

    def test_new_token_invalidates_old_badge(self, client, app, department):
        eid, token = self._emp(app, department)
        login(client, 'oper', 'Oper1234!')
        post(client, f'/admin/employees/{eid}/token')
        with app.app_context():
            assert db.session.get(DepartmentEmployee, eid).token != token
            assert find_department_employee(f'{BADGE_PREFIX}{token}') is None

    def test_badge_pdfs(self, client, app, department, order_user):
        eid, _ = self._emp(app, department)
        self._emp(app, department, name='T1 Anna Nowak')
        login(client, 'oper', 'Oper1234!')
        one = client.get(f'{BASE}/admin/employees/{eid}/plakietka.pdf')
        assert one.status_code == 200 and one.data.startswith(b'%PDF')
        dept = client.get(f'{BASE}/admin/employees/plakietki.pdf?dept_id={department}')
        assert dept.status_code == 200 and dept.data.startswith(b'%PDF')
        assert client.get(f'{BASE}/admin/employees/plakietki.pdf?dept_id=999999').status_code == 404
        client.get('/logout')
        login(client, order_user, 'Order1234!')
        assert client.get(f'{BASE}/admin/employees/{eid}/plakietka.pdf').status_code == 403

    def test_move_employee_to_other_department(self, client, app, department):
        eid, _ = self._emp(app, department)
        with app.app_context():
            other = ProductionDepartment(name='T1 Drugi dział', order=91)
            db.session.add(other)
            db.session.commit()
            other_id = other.id
        try:
            login(client, 'oper', 'Oper1234!')
            res = post(client, f'/admin/employees/{eid}/przenies', dept_id=other_id)
            assert 'Przeniesiono'.encode('utf-8') in res.data
            with app.app_context():
                assert db.session.get(DepartmentEmployee, eid).department_id == other_id
            res = post(client, f'/admin/employees/{eid}/przenies', dept_id='')
            assert 'Wybierz dział'.encode('utf-8') in res.data
        finally:
            with app.app_context():
                DepartmentEmployee.query.filter_by(id=eid).delete()
                ProductionDepartment.query.filter_by(id=other_id).delete()
                db.session.commit()

    def test_employees_page_has_badge_actions(self, client, app, department):
        self._emp(app, department)
        login(client, 'oper', 'Oper1234!')
        res = client.get(f'{BASE}/admin/departments/{department}/employees')
        assert res.status_code == 200
        for text in ('Plakietki działu', 'Nowy kod plakietki', 'przenieś do'):
            assert text.encode('utf-8') in res.data
