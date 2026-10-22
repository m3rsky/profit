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


class TestProductionStationAccount:
    def test_station_account_has_no_account_settings(self, client, app):
        with app.app_context():
            u = User(username='stanowisko', email='stanowisko@test.pl', role='produkcja')
            u.set_password('Stan12345!')
            db.session.add(u)
            db.session.commit()
        login(client, 'stanowisko', 'Stan12345!')
        assert client.get('/konto').status_code == 403
        assert client.post('/konto', data={'action': 'password', '_csrf_token': _csrf(client)}).status_code == 403
        page = client.get('/produkcja/skan').get_data(as_text=True)
        assert '/konto' not in page
        with app.app_context():
            db.session.delete(User.query.filter_by(username='stanowisko').first())
            db.session.commit()


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
            assert User.query.get(sid).role == 'admin'

    def test_mark_superadmin_by_email(self, app):
        with app.app_context():
            u = User(username='owner', email='palczowski@gmail.com', role='kontroler')
            u.set_password('Owner1234!')
            db.session.add(u)
            db.session.commit()
            _mark_superadmin()
            u = User.query.filter_by(username='owner').first()
            assert u.is_superadmin and u.role == 'admin'
            db.session.delete(u)
            db.session.commit()
