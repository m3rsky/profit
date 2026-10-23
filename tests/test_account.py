"""Ustawienia konta (/konto) i ograniczenie zarządzania kontami do super admina."""
from app import app as flask_app, db, User, _mark_superadmin
from tests.test_app import login, _csrf


def _post(client, **data):
    data['_csrf_token'] = _csrf(client)
    return client.post('/konto', data=data, follow_redirects=True)


class TestAccountPage:
    def test_requires_login(self, client):
        resp = client.get('/konto')
        assert resp.status_code in (302, 401)

    def test_page_shows_own_data(self, client):
        login(client, 'oper', 'Oper1234!')
        page = client.get('/konto').get_data(as_text=True)
        assert 'oper@test.pl' in page and 'Kontroler' in page

    def test_change_password(self, client, app):
        login(client, 'oper', 'Oper1234!')
        resp = _post(client, action='password', current_password='Oper1234!',
                     new_password='Nowe12345!', repeat_password='Nowe12345!')
        assert 'Hasło zostało zmienione' in resp.get_data(as_text=True)
        with app.app_context():
            u = User.query.filter_by(username='oper').first()
            assert u.check_password('Nowe12345!')
            u.set_password('Oper1234!')
            db.session.commit()

    def test_wrong_current_password(self, client):
        login(client, 'oper', 'Oper1234!')
        resp = _post(client, action='password', current_password='zle',
                     new_password='Nowe12345!', repeat_password='Nowe12345!')
        assert 'Obecne hasło jest nieprawidłowe' in resp.get_data(as_text=True)

    def test_password_validation(self, client):
        login(client, 'oper', 'Oper1234!')
        short = _post(client, action='password', current_password='Oper1234!',
                      new_password='krotkie', repeat_password='krotkie')
        assert '8 znaków' in short.get_data(as_text=True)
        mismatch = _post(client, action='password', current_password='Oper1234!',
                         new_password='Nowe12345!', repeat_password='Inne12345!')
        assert 'nie są takie same' in mismatch.get_data(as_text=True)

    def test_change_email_and_conflict(self, client, app):
        login(client, 'oper', 'Oper1234!')
        taken = _post(client, action='email', email='admin@test.pl')
        assert 'już istnieje' in taken.get_data(as_text=True)
        bad = _post(client, action='email', email='nie-email')
        assert 'Nieprawidłowy format' in bad.get_data(as_text=True)
        ok = _post(client, action='email', email='oper2@test.pl')
        assert 'zapisany' in ok.get_data(as_text=True)
        _post(client, action='email', email='oper@test.pl')


class TestSuperadmin:
    def test_only_superadmin_sees_users_menu(self, client):
        login(client, 'admin', 'Admin1234!')
        assert 'Użytkownicy' not in client.get('/admin/stats').get_data(as_text=True)
        client.get('/logout')
        login(client, 'super', 'Super1234!')
        assert 'Użytkownicy' in client.get('/admin/stats').get_data(as_text=True)
        assert client.get('/admin/users').status_code == 200

    def test_superadmin_cannot_be_deleted_or_demoted(self, client, app):
        login(client, 'super', 'Super1234!')
        with app.app_context():
            sid = User.query.filter_by(username='super').first().id
        resp = client.post(f'/admin/users/{sid}/edit',
                           data={'email': 'super@test.pl', 'role': 'kontroler',
                                 '_csrf_token': _csrf(client)},
                           follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert User.query.get(sid).role == 'administrator'

    def test_mark_superadmin_by_email(self, app):
        with app.app_context():
            u = User(username='owner', email='palczowski@gmail.com', role='kontroler')
            u.set_password('Owner1234!')
            db.session.add(u)
            db.session.commit()
            _mark_superadmin()
            u = User.query.filter_by(username='owner').first()
            assert u.is_superadmin and u.role == 'administrator'
            db.session.delete(u)
            db.session.commit()


class TestThemeAndMenuOrder:
    def _theme(self, app, username):
        with app.app_context():
            return User.query.filter_by(username=username).first().theme

    def test_theme_saved_on_account_and_applied(self, client, app):
        login(client, 'oper', 'Oper1234!')
        resp = _post(client, action='theme', theme='dark')
        assert 'Wygląd zapisany' in resp.get_data(as_text=True)
        assert self._theme(app, 'oper') == 'dark'
        page = client.get('/konto').get_data(as_text=True)
        assert 'data-user-theme="dark"' in page
        _post(client, action='theme', theme='light')
        assert self._theme(app, 'oper') == 'light'

    def test_theme_invalid_rejected(self, client, app):
        login(client, 'oper', 'Oper1234!')
        resp = _post(client, action='theme', theme='neon')
        assert 'Nieprawidłowy motyw' in resp.get_data(as_text=True)

    def test_theme_endpoint_for_toggle(self, client, app):
        login(client, 'oper', 'Oper1234!')
        h = {'X-CSRF-Token': _csrf(client), 'Content-Type': 'application/json'}
        assert client.post('/konto/motyw', json={'theme': 'dark'}, headers=h).get_json() == {'ok': True}
        assert self._theme(app, 'oper') == 'dark'
        assert client.post('/konto/motyw', json={'theme': 'bad'}, headers=h).status_code == 400
        client.post('/konto/motyw', json={'theme': 'light'}, headers=h)

    def test_theme_is_per_account(self, client, app):
        login(client, 'admin', 'Admin1234!')
        _post(client, action='theme', theme='dark')
        client.get('/logout')
        login(client, 'oper', 'Oper1234!')
        assert 'data-user-theme="dark"' not in client.get('/konto').get_data(as_text=True)
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        _post(client, action='theme', theme='light')

    def test_menu_order_link_only_for_admin_and_kontroler(self, client):
        login(client, 'admin', 'Admin1234!')
        page = client.get('/konto').get_data(as_text=True)
        assert 'edytuj-menu=1' in page
        client.get('/logout')
        login(client, 'oper', 'Oper1234!')
        assert 'edytuj-menu=1' in client.get('/konto').get_data(as_text=True)  # oper ma rolę kontroler

    def test_menu_order_available_for_every_role(self, client, app):
        with app.app_context():
            u = User(username='planista', email='planista@test.pl', role='technolog')
            u.set_password('Plan12345!')
            db.session.add(u)
            db.session.commit()
        login(client, 'planista', 'Plan12345!')
        assert 'edytuj-menu=1' in client.get('/konto').get_data(as_text=True)
        with app.app_context():
            db.session.delete(User.query.filter_by(username='planista').first())
            db.session.commit()
