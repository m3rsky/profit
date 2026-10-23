"""Role i macierz uprawnień: Superuser, Administrator, Operator, Konstruktor, Kontroler, Technolog."""
import pytest

from app import app as flask_app, db, _migrate_roles, _seed_role_permissions
from models import User, RolePermission, MODULES, DEFAULT_PERMISSIONS
from test_app import login, logout, _csrf


@pytest.fixture
def users(app):
    made = {}
    with app.app_context():
        for role in ('operator', 'konstruktor', 'kontroler', 'technolog'):
            name = f'rola_{role}'
            if not User.query.filter_by(username=name).first():
                u = User(username=name, email=f'{name}@test.pl', role=role)
                u.set_password('Rola12345!')
                db.session.add(u)
            made[role] = name
        db.session.commit()
    yield made
    with app.app_context():
        RolePermission.query.delete()
        User.query.filter(User.username.like('rola_%')).delete(synchronize_session=False)
        db.session.commit()


def _visible_keys(client):
    import re
    html = client.get('/').get_data(as_text=True)
    return set(re.findall(r'data-nav-key="(\w+)"', html))


class TestDefaultAccess:
    def test_technolog_sees_only_dashboard(self, client, users):
        login(client, users['technolog'], 'Rola12345!')
        assert client.get('/').status_code == 200
        assert _visible_keys(client) == {'dashboard'}
        assert client.get('/qar/').status_code == 403
        assert client.get('/kontrola/').status_code == 403
        assert client.get('/reports').status_code == 403

    def test_operator_goes_to_first_allowed_module(self, client, users):
        login(client, users['operator'], 'Rola12345!')
        resp = client.get('/')
        assert resp.status_code == 302  # Dashboard niedostępny, przekierowanie na pierwszy dozwolony moduł
        assert resp.headers['Location'].endswith('/kontrola/')
        assert client.get('/qar/').status_code == 403
        assert client.get('/produkcja/skan').status_code == 200

    def test_konstruktor_has_qar(self, client, users):
        login(client, users['konstruktor'], 'Rola12345!')
        assert client.get('/qar/').status_code == 200
        assert client.get('/produkcja/').status_code == 403
        assert client.get('/admin/stats').status_code == 403

    def test_kontroler_defaults(self, client, users):
        login(client, users['kontroler'], 'Rola12345!')
        assert client.get('/kontrola/').status_code == 200
        assert client.get('/reports').status_code == 200
        assert client.get('/admin/audit-log').status_code == 403
        assert client.get('/admin/templates').status_code == 403

    def test_administrator_sees_everything_but_users(self, client):
        login(client, 'admin', 'Admin1234!')
        keys = _visible_keys(client)
        assert {'dashboard', 'qar', 'kontrola_etapowa', 'szablony'} <= keys
        assert client.get('/admin/users').status_code == 403
        assert client.get('/admin/users/uprawnienia').status_code == 403

    def test_no_worker_layout_for_any_role(self, client, users):
        login(client, users['kontroler'], 'Rola12345!')
        html = client.get('/kontrola/').get_data(as_text=True)
        assert 'wk-header' not in html and 'id="sidebar"' in html


class TestSameDashboard:
    """Wszystkie role mają ten sam pulpit, widżety danych zależą tylko od modułów nadanych roli."""

    def test_kontroler_gets_full_dashboard(self, client, users):
        login(client, users['kontroler'], 'Rola12345!')
        html = client.get('/').get_data(as_text=True)
        assert 'Top 10 NG' in html and 'Alerty jakości' in html and 'Ostatnie raporty' in html
        assert 'QAR wg działów' in html and 'wk-welcome' not in html

    def test_administrator_and_kontroler_see_the_same_widgets(self, client, users):
        login(client, users['kontroler'], 'Rola12345!')
        kontroler = client.get('/').get_data(as_text=True)
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        admin = client.get('/').get_data(as_text=True)
        for text in ('Top 10 NG', 'QAR wg działów', 'Kontrole, ostatnie 5 dni', 'Alerty jakości', 'Ostatnie raporty'):
            assert (text in kontroler) == (text in admin) == True, text

    def test_widgets_follow_modules(self, client, users):
        login(client, users['technolog'], 'Rola12345!')
        html = client.get('/').get_data(as_text=True)
        assert 'Witaj' in html
        assert 'Top 10 NG' not in html and 'Ostatnie raporty' not in html and 'QAR wg działów' not in html


class TestPermissionMatrix:
    def test_only_superuser_opens_matrix(self, client):
        login(client, 'oper', 'Oper1234!')
        assert client.get('/admin/users/uprawnienia').status_code == 403
        client.get('/logout')
        login(client, 'super', 'Super1234!')
        page = client.get('/admin/users/uprawnienia').get_data(as_text=True)
        assert 'Technolog' in page and 'Kontrola etapowa' in page

    def test_matrix_changes_visibility(self, client, users):
        login(client, 'super', 'Super1234!')
        granted = [f'{r}:{m}' for r, mods in DEFAULT_PERMISSIONS.items() for m in mods]
        granted.append('technolog:qar')
        resp = client.post('/admin/users/uprawnienia', data={'perm': granted, '_csrf_token': _csrf(client)},
                           follow_redirects=True)
        assert 'Uprawnienia ról zapisane' in resp.get_data(as_text=True)
        client.get('/logout')
        login(client, users['technolog'], 'Rola12345!')
        assert client.get('/qar/').status_code == 200
        assert _visible_keys(client) == {'dashboard', 'qar'}

    def test_unchecking_hides_module(self, client, users):
        login(client, 'super', 'Super1234!')
        granted = [f'{r}:{m}' for r, mods in DEFAULT_PERMISSIONS.items() for m in mods if (r, m) != ('kontroler', 'qar')]
        client.post('/admin/users/uprawnienia', data={'perm': granted, '_csrf_token': _csrf(client)})
        client.get('/logout')
        login(client, users['kontroler'], 'Rola12345!')
        assert client.get('/qar/').status_code == 403
        assert 'qar' not in _visible_keys(client)

    def test_superuser_always_has_everything(self, client):
        login(client, 'super', 'Super1234!')
        client.post('/admin/users/uprawnienia', data={'_csrf_token': _csrf(client)})  # nic nie zaznaczone
        for url in ('/qar/', '/kontrola/', '/reports', '/admin/stats', '/admin/users'):
            assert client.get(url).status_code == 200, url


class TestMigrationAndForm:
    def test_legacy_roles_migrated(self, app):
        with app.app_context():
            for name, role in (('stary_admin', 'admin'), ('stary_plan', 'planowanie'), ('stary_stanowisko', 'produkcja')):
                u = User(username=name, email=f'{name}@test.pl', role=role)
                u.set_password('Stary12345!')
                db.session.add(u)
            db.session.commit()
            _migrate_roles()
            _seed_role_permissions()
            got = {u.username: u.role for u in User.query.filter(User.username.like('stary_%'))}
            assert got == {'stary_admin': 'administrator', 'stary_plan': 'technolog', 'stary_stanowisko': 'operator'}
            assert RolePermission.query.count() == len(DEFAULT_PERMISSIONS) * len(MODULES)
            User.query.filter(User.username.like('stary_%')).delete(synchronize_session=False)
            RolePermission.query.delete()
            db.session.commit()

    def test_user_form_offers_only_new_roles(self, client):
        login(client, 'super', 'Super1234!')
        page = client.get('/admin/users/new').get_data(as_text=True)
        for role in ('administrator', 'operator', 'konstruktor', 'kontroler', 'technolog'):
            assert f'value="{role}"' in page
        assert 'value="admin"' not in page and 'value="planowanie"' not in page and 'value="produkcja"' not in page

    def test_invalid_role_rejected(self, client):
        login(client, 'super', 'Super1234!')
        resp = client.post('/admin/users/new', data={'username': 'zlarola', 'email': 'zlarola@test.pl',
                                                      'password': 'Validpass1!', 'role': 'admin',
                                                      '_csrf_token': _csrf(client)}, follow_redirects=True)
        assert 'poprawną rolę' in resp.get_data(as_text=True)
