"""Testy modulu Zadania QA (karta wykonania zadan kontroli jakosci)."""
import pytest

from app import app as flask_app, db
from models import User, QATask
from test_app import login, _csrf


@pytest.fixture
def monter_user(app):
    with app.app_context():
        if not User.query.filter_by(username='monter1').first():
            u = User(username='monter1', email='monter1@test.pl', role='monter')
            u.set_password('Monter123!')
            db.session.add(u)
            db.session.commit()
    return 'monter1'


@pytest.fixture(autouse=True)
def _cleanup_tasks(app):
    yield
    with app.app_context():
        QATask.query.delete()
        db.session.commit()


class TestAccess:
    def test_list_requires_login(self, client):
        resp = client.get('/zadania-qa/')
        assert resp.status_code in (302, 401)

    def test_list_forbidden_for_monter(self, client, monter_user):
        login(client, monter_user, 'Monter123!')
        resp = client.get('/zadania-qa/')
        assert resp.status_code == 403

    def test_list_loads_for_kontroler(self, client):
        login(client, 'oper', 'Oper1234!')
        resp = client.get('/zadania-qa/')
        assert resp.status_code == 200
        assert 'Zadania QA'.encode('utf-8') in resp.data


class TestTaskCrud:
    def test_add_task(self, client):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/zadania-qa/new', data={
            'title': 'Kontrola testowa', 'description': 'Opis testowy',
            '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert 'Kontrola testowa'.encode('utf-8') in resp.data

    def test_edit_task(self, client):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        client.post('/zadania-qa/new', data={
            'title': 'Do edycji', 'description': '', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            task = QATask.query.filter_by(title='Do edycji').first()
            task_id = task.id
        token = _csrf(client)
        resp = client.post(f'/zadania-qa/{task_id}/edit', data={
            'title': 'Po edycji', 'description': 'Nowy opis', '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert 'Po edycji'.encode('utf-8') in resp.data

    def test_toggle_task_done(self, client):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        client.post('/zadania-qa/new', data={
            'title': 'Do przelaczenia', 'description': '', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            task = QATask.query.filter_by(title='Do przelaczenia').first()
            task_id = task.id
            assert task.is_done is False
        resp = client.post(f'/zadania-qa/{task_id}/toggle',
                           headers={'X-CSRF-Token': _csrf(client)})
        assert resp.status_code == 200
        assert resp.get_json()['is_done'] is True
        with flask_app.app_context():
            assert QATask.query.get(task_id).is_done is True

    def test_update_notes(self, client):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        client.post('/zadania-qa/new', data={
            'title': 'Zadanie z uwaga', 'description': '', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            task_id = QATask.query.filter_by(title='Zadanie z uwaga').first().id
        resp = client.post(f'/zadania-qa/{task_id}/notes',
                           json={'notes': 'Odchylka 2mm'},
                           headers={'X-CSRF-Token': _csrf(client)})
        assert resp.status_code == 200
        with flask_app.app_context():
            assert QATask.query.get(task_id).notes == 'Odchylka 2mm'

    def test_delete_task(self, client):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        client.post('/zadania-qa/new', data={
            'title': 'Do usuniecia', 'description': '', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            task_id = QATask.query.filter_by(title='Do usuniecia').first().id
        token = _csrf(client)
        resp = client.post(f'/zadania-qa/{task_id}/delete',
                           data={'_csrf_token': token}, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert QATask.query.get(task_id) is None


class TestPdfExport:
    def test_export_pdf(self, client):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        client.post('/zadania-qa/new', data={
            'title': 'Zadanie do PDF', 'description': 'Opis', '_csrf_token': token,
        }, follow_redirects=True)
        resp = client.get('/zadania-qa/pdf?shift=06:00-14:00&area=Hala A')
        assert resp.status_code == 200
        assert resp.headers['Content-Type'] == 'application/pdf'
