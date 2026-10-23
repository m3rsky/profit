"""Testy modulu Ustawienia produkcji (dzialy i pracownicy)."""
import pytest

from app import app as flask_app, db
from models import User, ProductionDepartment, DepartmentEmployee
from test_app import login, logout, _csrf


@pytest.fixture
def order_user(app):
    with app.app_context():
        if not User.query.filter_by(username='zamawiajacy1').first():
            u = User(username='zamawiajacy1', email='zamawiajacy1@test.pl', role='order')
            u.set_password('Order1234!')
            db.session.add(u)
            db.session.commit()
    return 'zamawiajacy1'


@pytest.fixture
def departments(app):
    with app.app_context():
        cutting = ProductionDepartment(name='Cięcie testowe', order=0)
        welding = ProductionDepartment(name='Spawanie testowe', order=1)
        db.session.add_all([cutting, welding])
        db.session.commit()
        ids = (cutting.id, welding.id)
    yield ids
    with app.app_context():
        DepartmentEmployee.query.delete()
        ProductionDepartment.query.filter(ProductionDepartment.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()



class TestAccess:
    def test_list_requires_login(self, client):
        resp = client.get('/ustawienia-produkcji/')
        assert resp.status_code in (302, 401)

    def test_list_forbidden_for_order_role(self, client, order_user):
        login(client, order_user, 'Order1234!')
        resp = client.get('/ustawienia-produkcji/')
        assert resp.status_code == 403

    def test_index_opens_departments_for_kontroler(self, client):
        login(client, 'oper', 'Oper1234!')
        resp = client.get('/ustawienia-produkcji/', follow_redirects=True)
        assert resp.status_code == 200
        assert 'Działy i pracownicy'.encode('utf-8') in resp.data

    def test_old_marszruta_urls_are_gone(self, client):
        login(client, 'oper', 'Oper1234!')
        assert client.get('/marszruta/').status_code == 404
        assert client.get('/spawalnia/').status_code == 404


class TestDepartmentsAdmin:
    def test_add_department(self, client, departments):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/ustawienia-produkcji/admin/departments', data={
            'action': 'add', 'name': 'Piaskowanie testowe', '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert 'Piaskowanie testowe'.encode('utf-8') in resp.data
        with flask_app.app_context():
            dept = ProductionDepartment.query.filter_by(name='Piaskowanie testowe').first()
            assert dept is not None
            db.session.delete(dept)
            db.session.commit()

    def test_toggle_department(self, client, departments):
        cutting_id, _ = departments
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/ustawienia-produkcji/admin/departments', data={
            'action': 'toggle', 'dept_id': cutting_id, '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert db.session.get(ProductionDepartment, cutting_id).is_active is False



class TestEmployeesAdmin:
    def test_add_and_toggle_employee(self, client, departments):
        cutting_id, _ = departments
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post(f'/ustawienia-produkcji/admin/departments/{cutting_id}/employees', data={
            'action': 'add', 'name': 'Jan Testowy', '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert 'Jan Testowy'.encode('utf-8') in resp.data

        with flask_app.app_context():
            emp = DepartmentEmployee.query.filter_by(name='Jan Testowy').first()
            emp_id = emp.id

        token = _csrf(client)
        resp = client.post(f'/ustawienia-produkcji/admin/departments/{cutting_id}/employees', data={
            'action': 'toggle', 'emp_id': emp_id, '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert db.session.get(DepartmentEmployee, emp_id).is_active is False


class TestLegacyMigration:
    def test_old_tables_dropped_and_spawacz_becomes_operator(self, app):
        from sqlalchemy import inspect, text
        from app import _migrate_schema
        old = ('interstage_checks', 'spawalnia_records', 'spawalnia_operators',
               'giecie_operators', 'ciecie_operators',
               'routing_card_photos', 'routing_card_stages', 'routing_cards')
        with app.app_context():
            for t in old:
                db.session.execute(text(f'CREATE TABLE {t} (id INTEGER PRIMARY KEY)'))
            u = User(username='spawacz_old', email='spawacz_old@test.pl', role='spawacz')
            u.set_password('Spawacz123!')
            db.session.add(u)
            db.session.commit()

            _migrate_schema()

            names = inspect(db.engine).get_table_names()
            assert not [t for t in old if t in names]
            assert 'production_departments' in names and 'routing_templates' in names
            db.session.expire_all()
            assert User.query.filter_by(username='spawacz_old').one().role == 'operator'
            db.session.delete(User.query.filter_by(username='spawacz_old').one())
            db.session.commit()


class TestRemovedSettingsPages:
    """Ścieżki produkcyjne, Szablony produktów i Etapy i bufory zostały wyłączone (dane w bazie zostają)."""

    def test_removed_pages_return_404(self, client):
        login(client, 'oper', 'Oper1234!')
        for url in ('/ustawienia-produkcji/admin/routing-templates',
                    '/ustawienia-produkcji/produkty',
                    '/ustawienia-produkcji/etapy'):
            assert client.get(url).status_code == 404, url

    def test_departments_page_has_no_removed_tabs(self, client):
        login(client, 'oper', 'Oper1234!')
        html = client.get('/ustawienia-produkcji/admin/departments').get_data(as_text=True)
        assert 'Ścieżki produkcyjne' not in html
        assert 'Szablony produktów' not in html
        assert 'Etapy i bufory' not in html
