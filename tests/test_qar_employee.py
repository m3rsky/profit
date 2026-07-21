"""Testy powiązania QAR z pracownikiem (osoba odpowiedzialna) i statystyk per osoba."""
import pytest

from app import app as flask_app, db
from models import (User, QARReport, ProductionDepartment, DepartmentEmployee,
                    RoutingCard, RoutingCardStage)
from test_app import login, logout, _csrf


@pytest.fixture
def qar_department(app):
    with app.app_context():
        dept = ProductionDepartment(name='Spawalnia QAR', order=0)
        db.session.add(dept)
        db.session.flush()
        emp = DepartmentEmployee(department_id=dept.id, name='Piotr Spawacz')
        db.session.add(emp)
        db.session.commit()
        ids = (dept.id, emp.id)
    yield ids
    with app.app_context():
        QARReport.query.filter(QARReport.employee_id.isnot(None)).delete(synchronize_session=False)
        RoutingCardStage.query.delete()
        RoutingCard.query.delete()
        DepartmentEmployee.query.filter_by(id=ids[1]).delete()
        ProductionDepartment.query.filter_by(id=ids[0]).delete()
        db.session.commit()


class TestQarEmployeeAssignment:
    def test_new_report_saves_employee(self, client, qar_department):
        _, emp_id = qar_department
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/qar/new', data={
            'title': 'Błąd spawu', 'description': 'Pęknięcie spoiny',
            'employee_id': str(emp_id), '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            report = QARReport.query.filter_by(title='Błąd spawu').first()
            assert report is not None
            assert report.employee_id == emp_id
            assert report.employee.name == 'Piotr Spawacz'

    def test_new_report_invalid_employee_saved_as_none(self, client, qar_department):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/qar/new', data={
            'title': 'Błąd bez osoby', 'description': 'Opis',
            'employee_id': '999999', '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            report = QARReport.query.filter_by(title='Błąd bez osoby').first()
            assert report.employee_id is None

    def test_edit_report_updates_employee(self, client, qar_department):
        _, emp_id = qar_department
        login(client, 'oper', 'Oper1234!')
        with flask_app.app_context():
            user = User.query.filter_by(username='oper').first()
            report = QARReport(number='QAR-2026-9001', title='Do edycji',
                               description='Opis', user_id=user.id)
            db.session.add(report)
            db.session.commit()
            report_id = report.id

        token = _csrf(client)
        resp = client.post(f'/qar/{report_id}/edit', data={
            'title': 'Do edycji', 'description': 'Opis', 'status': 'open',
            'employee_id': str(emp_id), '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert db.session.get(QARReport, report_id).employee_id == emp_id

    def test_detail_shows_employee(self, client, qar_department):
        _, emp_id = qar_department
        login(client, 'oper', 'Oper1234!')
        with flask_app.app_context():
            user = User.query.filter_by(username='oper').first()
            report = QARReport(number='QAR-2026-9002', title='Z osobą',
                               description='Opis', user_id=user.id, employee_id=emp_id)
            db.session.add(report)
            db.session.commit()
            report_id = report.id
        resp = client.get(f'/qar/{report_id}')
        assert resp.status_code == 200
        assert 'Piotr Spawacz'.encode('utf-8') in resp.data
        assert 'Spawalnia QAR'.encode('utf-8') in resp.data

    def test_list_filter_by_employee(self, client, qar_department):
        _, emp_id = qar_department
        login(client, 'oper', 'Oper1234!')
        with flask_app.app_context():
            user = User.query.filter_by(username='oper').first()
            db.session.add(QARReport(number='QAR-2026-9003', title='Filtrowany raport',
                                     description='Opis', user_id=user.id, employee_id=emp_id))
            db.session.add(QARReport(number='QAR-2026-9004', title='Inny raport bez osoby',
                                     description='Opis', user_id=user.id))
            db.session.commit()
        resp = client.get(f'/qar/?employee={emp_id}')
        assert resp.status_code == 200
        assert 'Filtrowany raport'.encode('utf-8') in resp.data
        assert 'Inny raport bez osoby'.encode('utf-8') not in resp.data


class TestSuggestEmployees:
    def test_suggest_returns_employees_from_routing_card(self, client, qar_department):
        dept_id, emp_id = qar_department
        login(client, 'oper', 'Oper1234!')
        with flask_app.app_context():
            user = User.query.filter_by(username='oper').first()
            card = RoutingCard(identifier='ZO-QAR-100', product_name='Produkt QAR',
                               quantity=1, created_by_id=user.id)
            db.session.add(card)
            db.session.flush()
            db.session.add(RoutingCardStage(card_id=card.id, department_id=dept_id,
                                            order=0, employee_id=emp_id))
            db.session.commit()

        resp = client.get('/qar/suggest-employees?zo=ZO-QAR-100')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['employees']) == 1
        assert data['employees'][0]['id'] == emp_id
        assert data['employees'][0]['name'] == 'Piotr Spawacz'
        assert data['employees'][0]['department'] == 'Spawalnia QAR'

    def test_suggest_unknown_zo_returns_empty(self, client):
        login(client, 'oper', 'Oper1234!')
        resp = client.get('/qar/suggest-employees?zo=ZO-NIE-ISTNIEJE')
        assert resp.status_code == 200
        assert resp.get_json()['employees'] == []


class TestQarStats:
    def test_stats_requires_privileged_role(self, client, app):
        with app.app_context():
            if not User.query.filter_by(username='zamawiajacy_qar').first():
                u = User(username='zamawiajacy_qar', email='zam_qar@test.pl', role='order')
                u.set_password('Order1234!')
                db.session.add(u)
                db.session.commit()
        login(client, 'zamawiajacy_qar', 'Order1234!')
        resp = client.get('/qar/stats')
        assert resp.status_code == 403
        logout(client)

    def test_stats_counts_reports_per_employee(self, client, qar_department):
        _, emp_id = qar_department
        login(client, 'oper', 'Oper1234!')
        with flask_app.app_context():
            user = User.query.filter_by(username='oper').first()
            db.session.add(QARReport(number='QAR-2026-9005', title='Stat 1',
                                     description='Opis', user_id=user.id,
                                     employee_id=emp_id, category='Spawanie'))
            db.session.add(QARReport(number='QAR-2026-9006', title='Stat 2',
                                     description='Opis', user_id=user.id,
                                     employee_id=emp_id, category='Spawanie', status='closed'))
            db.session.commit()

        resp = client.get('/qar/stats')
        assert resp.status_code == 200
        assert 'Piotr Spawacz'.encode('utf-8') in resp.data
        assert 'Spawalnia QAR'.encode('utf-8') in resp.data
        assert 'Spawanie (2)'.encode('utf-8') in resp.data
