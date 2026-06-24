"""
Testy automatyczne – PSH System Kontroli Jakości
Uruchomienie: pytest tests/ -v
"""
import os
import pytest
from sqlalchemy.pool import StaticPool
from app import app as flask_app, db as _db
from models import User, ChecklistTemplate, Category, Task, Report, ReportItem


@pytest.fixture(scope='session')
def app():
    flask_app.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'SQLALCHEMY_ENGINE_OPTIONS': {
            'connect_args': {'check_same_thread': False},
            'poolclass': StaticPool,
        },
        'WTF_CSRF_ENABLED': False,
        'UPLOAD_FOLDER': os.path.join(os.path.dirname(__file__), 'tmp_uploads'),
        'SECRET_KEY': 'test-secret',
    })
    flask_app.extensions.pop("sqlalchemy", None)
    _db.init_app(flask_app)
    with flask_app.app_context():
        _db.create_all()
        _seed()
    yield flask_app


def _seed():
    admin = User(username='admin', email='admin@test.pl', role='admin')
    admin.set_password('Admin1234!')
    user = User(username='oper', email='oper@test.pl', role='kontroler')
    user.set_password('Oper1234!')
    _db.session.add_all([admin, user])
    _db.session.flush()

    tmpl = ChecklistTemplate(name='Test szablon', is_active=True)
    _db.session.add(tmpl)
    _db.session.flush()
    cat = Category(template_id=tmpl.id, name='Kategoria', order=0)
    _db.session.add(cat)
    _db.session.flush()
    task = Task(category_id=cat.id, title='Zadanie testowe', order=0, is_active=True)
    _db.session.add(task)
    _db.session.commit()


@pytest.fixture
def client(app):
    return app.test_client()


def _csrf(client):
    """Fetch CSRF token — follow redirects so it works when already logged in."""
    resp = client.get('/login', follow_redirects=True)
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        return sess.get('_csrf_token', 'no-token')


def login(client, username, password):
    token = _csrf(client)
    return client.post('/login', data={
        'username': username, 'password': password, '_csrf_token': token
    }, follow_redirects=True)


def logout(client):
    return client.get('/logout', follow_redirects=True)


# ── Auth tests ─────────────────────────────────────────────────────────────────

class TestAuth:
    def test_login_page_loads(self, client):
        resp = client.get('/login')
        assert resp.status_code == 200
        assert b'Logowanie' in resp.data or b'Zaloguj' in resp.data

    def test_login_success(self, client):
        resp = login(client, 'admin', 'Admin1234!')
        assert resp.status_code == 200
        assert b'Wyloguj' in resp.data

    def test_login_wrong_password(self, client):
        resp = login(client, 'admin', 'wrongpass')
        assert b'Nieprawid' in resp.data

    def test_login_unknown_user(self, client):
        resp = login(client, 'nobody', 'anything')
        assert b'Nieprawid' in resp.data

    def test_dashboard_requires_login(self, client):
        logout(client)
        resp = client.get('/', follow_redirects=False)
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_logout(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = logout(client)
        assert b'Zaloguj' in resp.data


# ── CSRF tests ─────────────────────────────────────────────────────────────────

class TestCSRF:
    def test_post_without_csrf_token_blocked(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.post('/admin/templates/new',
                           data={'name': 'Hack', 'description': ''})
        assert resp.status_code == 403

    def test_post_with_wrong_csrf_token_blocked(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.post('/admin/templates/new',
                           data={'name': 'Hack', '_csrf_token': 'bad-token'})
        assert resp.status_code == 403


# ── Dashboard tests ────────────────────────────────────────────────────────────

class TestDashboard:
    def test_dashboard_loads(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/')
        assert resp.status_code == 200

    def test_dashboard_date_filter(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/?date_from=2020-01-01&date_to=2099-12-31')
        assert resp.status_code == 200


# ── Report tests ───────────────────────────────────────────────────────────────

class TestReports:
    def _create_report(self, client):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        with flask_app.app_context():
            tmpl = ChecklistTemplate.query.filter_by(name='Test szablon').first()
            tmpl_id = tmpl.id
        resp = client.post('/checklist/new',
                           data={'template_id': tmpl_id, 'title': 'Raport testowy',
                                 '_csrf_token': token},
                           follow_redirects=True)
        return resp

    def test_create_report(self, client):
        resp = self._create_report(client)
        assert resp.status_code == 200

    def test_reports_list(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/reports')
        assert resp.status_code == 200

    def test_reports_filter_by_status(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/reports?status=completed')
        assert resp.status_code == 200

    def test_reports_search(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/reports?q=testowy')
        assert resp.status_code == 200

    def test_reports_csv_export(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/reports/export.csv')
        assert resp.status_code == 200
        assert b'text/csv' in resp.headers['Content-Type'].encode()


# ── Result API tests ───────────────────────────────────────────────────────────

class TestResultAPI:
    def _get_item_id(self, client):
        login(client, 'oper', 'Oper1234!')
        with flask_app.app_context():
            item = ReportItem.query.first()
            return item.id if item else None

    def test_set_result_ok(self, client):
        item_id = self._get_item_id(client)
        if not item_id:
            pytest.skip('No report items')
        resp = client.post(f'/api/item/{item_id}/result',
                           json={'result': 'ok'},
                           headers={'X-CSRF-Token': _csrf(client),
                                    'Content-Type': 'application/json'})
        assert resp.status_code == 200
        assert resp.json['result'] == 'ok'

    def test_set_result_ng(self, client):
        item_id = self._get_item_id(client)
        if not item_id:
            pytest.skip('No report items')
        resp = client.post(f'/api/item/{item_id}/result',
                           json={'result': 'ng'},
                           headers={'X-CSRF-Token': _csrf(client),
                                    'Content-Type': 'application/json'})
        assert resp.status_code == 200
        assert resp.json['result'] == 'ng'

    def test_set_result_na(self, client):
        item_id = self._get_item_id(client)
        if not item_id:
            pytest.skip('No report items')
        resp = client.post(f'/api/item/{item_id}/result',
                           json={'result': 'na'},
                           headers={'X-CSRF-Token': _csrf(client),
                                    'Content-Type': 'application/json'})
        assert resp.status_code == 200
        assert resp.json['result'] == 'na'

    def test_set_result_invalid(self, client):
        item_id = self._get_item_id(client)
        if not item_id:
            pytest.skip('No report items')
        resp = client.post(f'/api/item/{item_id}/result',
                           json={'result': 'bad'},
                           headers={'X-CSRF-Token': _csrf(client),
                                    'Content-Type': 'application/json'})
        assert resp.status_code == 400


# ── User management tests ──────────────────────────────────────────────────────

class TestUserManagement:
    def test_create_user_short_password(self, client):
        login(client, 'admin', 'Admin1234!')
        token = _csrf(client)
        resp = client.post('/admin/users/new',
                           data={'username': 'newuser', 'email': 'new@test.pl',
                                 'password': 'short', 'role': 'user',
                                 '_csrf_token': token},
                           follow_redirects=True)
        assert b'8 znak' in resp.data

    def test_create_user_bad_email(self, client):
        login(client, 'admin', 'Admin1234!')
        token = _csrf(client)
        resp = client.post('/admin/users/new',
                           data={'username': 'newuser2', 'email': 'not-an-email',
                                 'password': 'Validpass1!', 'role': 'user',
                                 '_csrf_token': token},
                           follow_redirects=True)
        assert b'e-mail' in resp.data

    def test_create_user_duplicate_email(self, client):
        login(client, 'admin', 'Admin1234!')
        token = _csrf(client)
        resp = client.post('/admin/users/new',
                           data={'username': 'unique', 'email': 'admin@test.pl',
                                 'password': 'Validpass1!', 'role': 'user',
                                 '_csrf_token': token},
                           follow_redirects=True)
        assert b'e-mail' in resp.data


# ── Admin access tests ─────────────────────────────────────────────────────────

class TestAdminAccess:
    def test_admin_pages_forbidden_for_users(self, client):
        login(client, 'oper', 'Oper1234!')
        resp = client.get('/admin/users')
        assert resp.status_code == 403

    def test_stats_page_loads(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/admin/stats')
        assert resp.status_code == 200

    def test_audit_log_loads(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/admin/audit-log')
        assert resp.status_code == 200

    def test_templates_page_loads(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/admin/templates')
        assert resp.status_code == 200
