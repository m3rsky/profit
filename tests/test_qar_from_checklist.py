"""Testy szybkiego zgłaszania QAR z punktu kontrolnego listy QA."""
import pytest

from app import app as flask_app, db
from models import ChecklistTemplate, Report, ReportItem, QARReport, User
from test_app import login, logout, _csrf


def _post_qar(client, item_id, **payload):
    """POST na endpoint tworzący QAR z punktu — z nagłówkiem CSRF (sesyjny)."""
    return client.post(f'/qar/from-checklist-item/{item_id}', json=payload,
                       headers={'X-CSRF-Token': _csrf(client)})


def _new_report_with_item(client):
    """Tworzy raport z szablonu 'Test szablon' i zwraca (report_id, item_id)."""
    login(client, 'oper', 'Oper1234!')
    token = _csrf(client)
    with flask_app.app_context():
        tmpl_id = ChecklistTemplate.query.filter_by(name='Test szablon').first().id
    client.post('/checklist/new',
                data={'template_id': tmpl_id, 'title': 'QAR-z-punktu',
                      '_csrf_token': token},
                follow_redirects=True)
    with flask_app.app_context():
        report = (Report.query.filter(Report.title.like('%QAR-z-punktu%'))
                  .order_by(Report.id.desc()).first())
        item = report.items.first()
        return report.id, item.id


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with flask_app.app_context():
        ReportItem.query.filter(ReportItem.qar_report_id.isnot(None)).update(
            {'qar_report_id': None}, synchronize_session=False)
        QARReport.query.filter(QARReport.number.like('QAR-%')).filter(
            QARReport.title.in_(['Zadanie testowe', 'Pęknięta spoina',
                                 'Zła kategoria', 'Dobra kategoria'])).delete(
            synchronize_session=False)
        db.session.commit()


class TestQarFromChecklistItem:
    def test_creates_and_links_qar(self, client):
        report_id, item_id = _new_report_with_item(client)
        resp = _post_qar(client, item_id, title='Pęknięta spoina',
                         description='Widoczne pęknięcie na spoinie pionowej')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        assert data['number'].startswith('QAR-')
        with flask_app.app_context():
            item = db.session.get(ReportItem, item_id)
            assert item.qar_report_id is not None
            assert item.qar_report.title == 'Pęknięta spoina'
            assert item.qar_report.author.username == 'oper'

    def test_second_call_returns_existing(self, client):
        _, item_id = _new_report_with_item(client)
        first = _post_qar(client, item_id, title='Pęknięta spoina',
                          description='Opis').get_json()
        again = _post_qar(client, item_id, title='Inny tytuł', description='Inny opis')
        assert again.status_code == 200
        data = again.get_json()
        assert data.get('already') is True
        assert data['number'] == first['number']
        with flask_app.app_context():
            assert QARReport.query.filter_by(number=first['number']).count() == 1

    def test_missing_fields_rejected(self, client):
        _, item_id = _new_report_with_item(client)
        resp = _post_qar(client, item_id, title='Bez opisu')
        assert resp.status_code == 400
        with flask_app.app_context():
            assert db.session.get(ReportItem, item_id).qar_report_id is None

    def test_invalid_category_is_dropped(self, client):
        _, item_id = _new_report_with_item(client)
        _post_qar(client, item_id, title='Zła kategoria', description='Opis',
                  category='Kategoria-której-nie-ma')
        with flask_app.app_context():
            item = db.session.get(ReportItem, item_id)
            assert item.qar_report.category is None

    def test_forbidden_for_unprivileged_user(self, client):
        report_id, item_id = _new_report_with_item(client)
        logout(client)
        with flask_app.app_context():
            if not User.query.filter_by(username='zam_qar_fc').first():
                u = User(username='zam_qar_fc', email='zam_fc@test.pl', role='order')
                u.set_password('Order1234!')
                db.session.add(u)
                db.session.commit()
        login(client, 'zam_qar_fc', 'Order1234!')
        resp = _post_qar(client, item_id, title='X', description='Y')
        assert resp.status_code == 403

    def test_checklist_view_shows_chip_after_link(self, client):
        report_id, item_id = _new_report_with_item(client)
        num = _post_qar(client, item_id, title='Pęknięta spoina',
                        description='Opis').get_json()['number']
        resp = client.get(f'/checklist/{report_id}')
        assert resp.status_code == 200
        assert num.encode('utf-8') in resp.data
