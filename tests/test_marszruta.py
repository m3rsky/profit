"""Testy modulu Marszruta produkcji (karty marszrutowe skanowane QR)."""
import pytest

from app import app as flask_app, db
from models import (User, ProductionDepartment, DepartmentEmployee,
                    RoutingTemplate, RoutingTemplateStage, RoutingCard, RoutingCardStage)
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
        RoutingCardStage.query.delete()
        RoutingCard.query.delete()
        RoutingTemplateStage.query.delete()
        RoutingTemplate.query.delete()
        DepartmentEmployee.query.delete()
        ProductionDepartment.query.filter(ProductionDepartment.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture
def routing_template(app, departments):
    cutting_id, welding_id = departments
    with app.app_context():
        tmpl = RoutingTemplate(name='MRS-100', is_active=True)
        db.session.add(tmpl)
        db.session.flush()
        db.session.add(RoutingTemplateStage(template_id=tmpl.id, department_id=cutting_id, order=0))
        db.session.add(RoutingTemplateStage(template_id=tmpl.id, department_id=welding_id, order=1))
        db.session.commit()
        return tmpl.id


class TestAccess:
    def test_list_requires_login(self, client):
        resp = client.get('/marszruta/')
        assert resp.status_code in (302, 401)

    def test_list_forbidden_for_order_role(self, client, order_user):
        login(client, order_user, 'Order1234!')
        resp = client.get('/marszruta/')
        assert resp.status_code == 403

    def test_list_loads_for_kontroler(self, client):
        login(client, 'oper', 'Oper1234!')
        resp = client.get('/marszruta/')
        assert resp.status_code == 200
        assert 'Marszruta produkcji'.encode('utf-8') in resp.data


class TestDepartmentsAdmin:
    def test_add_department(self, client, departments):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/marszruta/admin/departments', data={
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
        resp = client.post('/marszruta/admin/departments', data={
            'action': 'toggle', 'dept_id': cutting_id, '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert db.session.get(ProductionDepartment, cutting_id).is_active is False

    def test_delete_department_blocked_when_used_in_template(self, client, routing_template, departments):
        cutting_id, _ = departments
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/marszruta/admin/departments', data={
            'action': 'delete', 'dept_id': cutting_id, '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert db.session.get(ProductionDepartment, cutting_id) is not None


class TestEmployeesAdmin:
    def test_add_and_toggle_employee(self, client, departments):
        cutting_id, _ = departments
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post(f'/marszruta/admin/departments/{cutting_id}/employees', data={
            'action': 'add', 'name': 'Jan Testowy', '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert 'Jan Testowy'.encode('utf-8') in resp.data

        with flask_app.app_context():
            emp = DepartmentEmployee.query.filter_by(name='Jan Testowy').first()
            emp_id = emp.id

        token = _csrf(client)
        resp = client.post(f'/marszruta/admin/departments/{cutting_id}/employees', data={
            'action': 'toggle', 'emp_id': emp_id, '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert db.session.get(DepartmentEmployee, emp_id).is_active is False


class TestRoutingTemplateCrud:
    def test_create_routing_template(self, client, departments):
        cutting_id, welding_id = departments
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/marszruta/admin/routing-templates/new', data={
            'name': 'MRS-200', 'department_ids': [str(cutting_id), str(welding_id)],
            '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            tmpl = RoutingTemplate.query.filter_by(name='MRS-200').first()
            assert tmpl is not None
            assert tmpl.stages.count() == 2

    def test_edit_routing_template_updates_departments(self, client, routing_template, departments):
        cutting_id, welding_id = departments
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post(f'/marszruta/admin/routing-templates/{routing_template}/edit', data={
            'name': 'MRS-100', 'is_active': 'on',
            'department_ids': [str(cutting_id)], '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            tmpl = db.session.get(RoutingTemplate, routing_template)
            assert tmpl.stages.count() == 1


class TestQrScan:
    def test_from_qr_creates_card_with_matching_template(self, client, routing_template):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/marszruta/from-qr', json={
            'p': 'MRS-100 obudowa', 'q': 5, 'c': 'Klient X', 'o': 'ZO-9001',
        }, headers={'X-CSRF-Token': token})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        assert 'ZO-9001' not in data.get('error', '')
        with flask_app.app_context():
            card = RoutingCard.query.filter_by(identifier='ZO-9001').first()
            assert card is not None
            assert card.product_name == 'MRS-100 obudowa'
            assert card.stages.count() == 2

    def test_from_qr_missing_product_returns_400(self, client, routing_template):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/marszruta/from-qr', json={'q': 1}, headers={'X-CSRF-Token': token})
        assert resp.status_code == 400

    def test_from_qr_no_matching_template_returns_404(self, client, routing_template):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/marszruta/from-qr', json={
            'p': 'ZUPELNIE INNY PRODUKT XYZ', 'q': 1, 'o': 'ZO-9002',
        }, headers={'X-CSRF-Token': token})
        assert resp.status_code == 404

    def test_from_qr_rescan_same_identifier_reuses_card(self, client, routing_template):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        client.post('/marszruta/from-qr', json={
            'p': 'MRS-100 obudowa', 'q': 3, 'o': 'ZO-9003',
        }, headers={'X-CSRF-Token': token})
        with flask_app.app_context():
            first_id = RoutingCard.query.filter_by(identifier='ZO-9003').first().id

        token = _csrf(client)
        resp = client.post('/marszruta/from-qr', json={
            'p': 'MRS-100 obudowa', 'q': 3, 'o': 'ZO-9003',
        }, headers={'X-CSRF-Token': token})
        data = resp.get_json()
        assert data['ok'] is True
        with flask_app.app_context():
            assert RoutingCard.query.filter_by(identifier='ZO-9003').count() == 1
            assert str(first_id) in data['redirect']


class TestStageEdit:
    def test_edit_stage_saves_result_and_employee(self, client, routing_template, departments):
        cutting_id, _ = departments
        login(client, 'oper', 'Oper1234!')

        with flask_app.app_context():
            emp = DepartmentEmployee(department_id=cutting_id, name='Adam Testowy')
            db.session.add(emp)
            tmpl = db.session.get(RoutingTemplate, routing_template)
            card = RoutingCard(identifier='ZO-9004', product_name='MRS-100 obudowa',
                               quantity=1, template_id=tmpl.id, created_by_id=User.query.filter_by(username='oper').first().id)
            db.session.add(card)
            db.session.flush()
            for stage_def in tmpl.stages:
                db.session.add(RoutingCardStage(card_id=card.id, department_id=stage_def.department_id,
                                                order=stage_def.order))
            db.session.commit()
            card_id = card.id
            emp_id = emp.id
            stage_id = card.stages.filter_by(department_id=cutting_id).first().id

        token = _csrf(client)
        resp = client.post(f'/marszruta/stage/{stage_id}/edit', data={
            'employee_id': str(emp_id), 'result': 'ng', 'notes': 'Odchyłka wymiaru',
            '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200

        with flask_app.app_context():
            stage = db.session.get(RoutingCardStage, stage_id)
            assert stage.result == 'ng'
            assert stage.employee_id == emp_id
            assert stage.notes == 'Odchyłka wymiaru'
            card = db.session.get(RoutingCard, card_id)
            assert card.has_ng is True
            assert card.is_complete is False  # druga karta (spawanie) jeszcze nieoceniona


class TestStats:
    def test_admin_stats_shows_marszruta_section(self, client, routing_template, departments):
        cutting_id, _ = departments
        login(client, 'oper', 'Oper1234!')

        with flask_app.app_context():
            emp = DepartmentEmployee(department_id=cutting_id, name='Ewa Statystyczna')
            db.session.add(emp)
            tmpl = db.session.get(RoutingTemplate, routing_template)
            card = RoutingCard(identifier='ZO-9005', product_name='MRS-100 obudowa',
                               quantity=1, template_id=tmpl.id,
                               created_by_id=User.query.filter_by(username='oper').first().id)
            db.session.add(card)
            db.session.flush()
            for stage_def in tmpl.stages:
                db.session.add(RoutingCardStage(card_id=card.id, department_id=stage_def.department_id,
                                                order=stage_def.order))
            db.session.commit()
            emp_id = emp.id
            stage_id = card.stages.filter_by(department_id=cutting_id).first().id

        token = _csrf(client)
        client.post(f'/marszruta/stage/{stage_id}/edit', data={
            'employee_id': str(emp_id), 'result': 'ng', 'notes': 'Test statystyk',
            '_csrf_token': token,
        }, follow_redirects=True)

        logout(client)
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/admin/stats')
        assert resp.status_code == 200
        assert 'Marszruta produkcji'.encode('utf-8') in resp.data
        assert 'Ewa Statystyczna'.encode('utf-8') in resp.data
        assert 'Cięcie testowe'.encode('utf-8') in resp.data


class TestDeleteCard:
    def test_delete_forbidden_for_kontroler(self, client, routing_template):
        login(client, 'oper', 'Oper1234!')
        with flask_app.app_context():
            tmpl = db.session.get(RoutingTemplate, routing_template)
            card = RoutingCard(identifier='ZO-9006', product_name='MRS-100 obudowa',
                               quantity=1, template_id=tmpl.id,
                               created_by_id=User.query.filter_by(username='oper').first().id)
            db.session.add(card)
            db.session.commit()
            card_id = card.id

        token = _csrf(client)
        resp = client.post(f'/marszruta/{card_id}/delete', data={'_csrf_token': token})
        assert resp.status_code == 403
        with flask_app.app_context():
            assert db.session.get(RoutingCard, card_id) is not None

    def test_delete_allowed_for_admin_cascades_stages(self, client, routing_template, departments):
        cutting_id, welding_id = departments
        login(client, 'oper', 'Oper1234!')
        with flask_app.app_context():
            tmpl = db.session.get(RoutingTemplate, routing_template)
            card = RoutingCard(identifier='ZO-9007', product_name='MRS-100 obudowa',
                               quantity=1, template_id=tmpl.id,
                               created_by_id=User.query.filter_by(username='oper').first().id)
            db.session.add(card)
            db.session.flush()
            for stage_def in tmpl.stages:
                db.session.add(RoutingCardStage(card_id=card.id, department_id=stage_def.department_id,
                                                order=stage_def.order))
            db.session.commit()
            card_id = card.id

        logout(client)
        login(client, 'admin', 'Admin1234!')
        token = _csrf(client)
        resp = client.post(f'/marszruta/{card_id}/delete', data={'_csrf_token': token}, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert db.session.get(RoutingCard, card_id) is None
            assert RoutingCardStage.query.filter_by(card_id=card_id).count() == 0
