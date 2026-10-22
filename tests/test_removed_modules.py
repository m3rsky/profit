"""Testy po usunięciu modułu Zamówienia, roli Monter/Zamawiający i Zadań QA (2026-10-21)."""
import pytest
from sqlalchemy import inspect, text

from app import app as flask_app, db, _drop_orders_and_monter
from models import User, ChecklistTemplate, Report
from test_app import login, _csrf
from conftest import API_KEY


class TestRoutesGone:
    @pytest.mark.parametrize('path', ['/orders', '/orders/new', '/orders/import-csv', '/orders/1',
                                      '/monter/pool', '/zadania-qa/'])
    def test_pages_are_gone(self, client, path):
        login(client, 'admin', 'Admin1234!')
        assert client.get(path).status_code == 404

    def test_orders_api_gone(self, client, app):
        key = API_KEY
        for method, path in (('get', '/api/v1/orders'), ('get', '/api/v1/orders/1'), ('post', '/api/v1/orders')):
            resp = getattr(client, method)(path, headers={'X-API-Key': key})
            assert resp.status_code == 404, path

    def test_checklist_api_ignores_removed_order_fields(self, client, app):
        key = API_KEY
        h = {'X-API-Key': key}
        tmpl = client.get('/api/v1/templates', headers=h).get_json()
        tmpl_id = next(t['id'] for t in tmpl if t['name'] == 'Kontrola ZO API')
        resp = client.post('/api/v1/checklists', headers=h, json={'template_id': tmpl_id, 'order_number': 'ZAM-1'})
        assert resp.status_code == 201
        body = resp.get_json()
        assert 'order_number' not in body
        detail = client.get(f'/api/v1/checklists/{body["id"]}', headers=h).get_json()
        assert 'order' not in detail


class TestUiWithoutRemovedRoles:
    def test_user_form_has_no_monter_or_order_role(self, client):
        login(client, 'super', 'Super1234!')
        page = client.get('/admin/users/new').get_data(as_text=True)
        assert 'value="kontroler"' in page and 'value="admin"' in page
        assert 'value="monter"' not in page and 'value="order"' not in page

    def test_template_form_has_no_monter_type_or_filter(self, client):
        login(client, 'admin', 'Admin1234!')
        assert 'value="monter"' not in client.get('/admin/templates/new').get_data(as_text=True)
        page = client.get('/admin/templates').get_data(as_text=True)
        assert 'type=monter' not in page and 'filter-tabs' not in page

    def test_new_checklist_has_no_order_select(self, client):
        login(client, 'admin', 'Admin1234!')
        page = client.get('/checklist/new').get_data(as_text=True)
        assert 'name="order_id"' not in page and 'Zamówienie' not in page

    def test_dashboard_renders_for_admin_and_controller(self, client):
        for user, pw in (('admin', 'Admin1234!'), ('oper', 'Oper1234!')):
            client.get('/logout')
            login(client, user, pw)
            page = client.get('/')
            assert page.status_code == 200
            assert b'Gotowe do wysy' not in page.data and b'Aktywne zam' not in page.data


class TestMigration:
    def test_users_templates_reports_and_orders_table_cleaned(self, app):
        with app.app_context():
            db.session.execute(text('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY)'))
            admin = User.query.filter_by(username='admin').one()
            users = {}
            for name, role in (('rm_monter', 'monter'), ('rm_order', 'order'), ('rm_kontroler', 'kontroler')):
                u = User(username=name, email=f'{name}@test.pl', role=role)
                u.set_password('Rola1234!')
                db.session.add(u)
                users[name] = u
            tpl = ChecklistTemplate(name='Stary szablon montera', is_active=True, template_type='monter')
            db.session.add(tpl)
            db.session.flush()
            db.session.add(Report(user_id=admin.id, template_id=tpl.id, title='Lista montażowa',
                                  report_type='monter'))
            db.session.add(Report(user_id=admin.id, title='Lista kontrolna', report_type='kontroler'))
            db.session.commit()

            _drop_orders_and_monter()

            roles = {u.username: db.session.get(User, u.id).role for u in users.values()}
            assert roles == {'rm_monter': 'operator', 'rm_order': 'operator', 'rm_kontroler': 'kontroler'}
            assert ChecklistTemplate.query.filter_by(template_type='monter').count() == 0
            assert Report.query.filter_by(report_type='monter').count() == 0
            assert Report.query.filter_by(title='Lista kontrolna').count() == 1
            assert 'orders' not in inspect(db.engine).get_table_names()

            _drop_orders_and_monter()                         # idempotentnie
            Report.query.filter_by(title='Lista kontrolna').delete()
            User.query.filter(User.username.like('rm\\_%', escape='\\')).delete(synchronize_session=False)
            db.session.commit()
