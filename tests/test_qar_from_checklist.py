"""Testy szybkiego zgłaszania QAR z punktu kontrolnego listy QA."""
import os

import pytest

from app import app as flask_app, db
from models import ChecklistTemplate, Report, ReportItem, Photo, QARReport, User
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
                                 'Zła kategoria', 'Dobra kategoria', 'Z fotką'])).delete(
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

    def test_checklist_item_row_carries_item_id_attribute(self, client):
        """Regresja: modal QAR czytał item id z data-item-id na .checklist-item,
        którego ten element nie miał — dawało JS string "undefined" i front
        wołał POST /qar/from-checklist-item/undefined -> 404 ('Nieoczekiwana
        odpowiedź serwera HTTP 404'). Element musi nosić ten atrybut."""
        report_id, item_id = _new_report_with_item(client)
        resp = client.get(f'/checklist/{report_id}')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert f'id="item-{item_id}" data-item-id="{item_id}"' in html


class TestQarFromChecklistItemErrorHandling:
    """Regresja: dowolny niespodziewany wyjątek w endpointcie musi wrócić jako
    JSON 500 (z komunikatem), a nie jako strona HTML globalnego error-handlera
    — front robi na odpowiedzi `r.json()`/parsuje tekst i przy HTML pokazywał
    mylące „Błąd połączenia" zamiast prawdziwej przyczyny."""

    def test_unexpected_exception_returns_json_500(self, client, monkeypatch):
        import qar.routes as qar_routes
        _, item_id = _new_report_with_item(client)

        def _boom():
            raise RuntimeError('symulowana awaria numeracji QAR')
        monkeypatch.setattr(qar_routes, '_next_qar_number', _boom)

        resp = _post_qar(client, item_id, title='Awaria', description='Opis')
        assert resp.status_code == 500
        data = resp.get_json()
        assert data is not None
        assert 'RuntimeError' in data['error']
        with flask_app.app_context():
            assert db.session.get(ReportItem, item_id).qar_report_id is None


class TestQarFromChecklistItemWithPhoto:
    """Regresja: kopiowanie zdjęcia punktu do galerii QAR (Photo nie ma pola
    'caption' — wcześniej powodowało AttributeError -> 500 -> 'Błąd połączenia'
    po stronie frontu za każdym razem, gdy NG miało dołączone zdjęcie)."""

    def test_copies_item_photo_into_qar(self, app, client):
        report_id, item_id = _new_report_with_item(client)
        upload_dir = app.config['UPLOAD_FOLDER']
        qar_dir    = app.config['QAR_UPLOAD_FOLDER']
        os.makedirs(upload_dir, exist_ok=True)
        os.makedirs(qar_dir, exist_ok=True)
        fname = f'test_ng_photo_{item_id}.jpg'
        src_path = os.path.join(upload_dir, fname)
        with open(src_path, 'wb') as f:
            f.write(b'fake-jpeg-bytes')

        with flask_app.app_context():
            photo = Photo(report_item_id=item_id, filename=fname, original_name='orig.jpg')
            db.session.add(photo)
            db.session.commit()
            photo_id = photo.id

        try:
            resp = _post_qar(client, item_id, title='Z fotką', description='Opis',
                             photo_ids=[str(photo_id)])
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['ok'] is True

            with flask_app.app_context():
                qar = QARReport.query.filter_by(number=data['number']).first()
                assert qar.photos.count() == 1
                qphoto = qar.photos.first()
                dst_path = os.path.join(qar_dir, qphoto.filename)
                assert os.path.exists(dst_path)
                os.remove(dst_path)
        finally:
            if os.path.exists(src_path):
                os.remove(src_path)
            with flask_app.app_context():
                Photo.query.filter_by(id=photo_id).delete()
                db.session.commit()
