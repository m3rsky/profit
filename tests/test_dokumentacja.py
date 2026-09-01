"""Testy modułu DOKUMENTACJA (teczki: dokumenty, zdjęcia, notatki)."""
import io
import os

import pytest
from PIL import Image

from app import app as flask_app, db
from models import User, DocEntry, DocFile, DocNote
from test_app import login, logout, _csrf


@pytest.fixture(autouse=True)
def _isolated_upload_dir(tmp_path):
    """Każdy test pisze do własnego katalogu uploadów."""
    prev = flask_app.config.get('DOKUMENTACJA_UPLOAD_FOLDER')
    d = tmp_path / 'dok_uploads'
    d.mkdir()
    flask_app.config['DOKUMENTACJA_UPLOAD_FOLDER'] = str(d)
    yield str(d)
    flask_app.config['DOKUMENTACJA_UPLOAD_FOLDER'] = prev


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with flask_app.app_context():
        DocNote.query.delete()
        DocFile.query.delete()
        DocEntry.query.delete()
        db.session.commit()


@pytest.fixture
def other_user(app):
    with app.app_context():
        if not User.query.filter_by(username='dok_other').first():
            u = User(username='dok_other', email='dok_other@test.pl', role='kontroler')
            u.set_password('Other1234!')
            db.session.add(u)
            db.session.commit()
    return 'dok_other'


def _png_bytes(w=1000, h=700, color=(180, 90, 40)):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), color).save(buf, 'PNG')
    buf.seek(0)
    return buf


def _mk_entry(client, title='Teczka testowa', description='opis'):
    token = _csrf(client)
    client.post('/dokumentacja/nowa', data={
        'title': title, 'description': description, '_csrf_token': token,
    }, follow_redirects=True)
    with flask_app.app_context():
        return DocEntry.query.filter_by(title=title).first().id


# ── Dostęp ───────────────────────────────────────────────────────────────────

class TestAccess:
    def test_list_requires_login(self, client):
        logout(client)
        resp = client.get('/dokumentacja/')
        assert resp.status_code in (302, 401)

    def test_list_loads_for_logged_in(self, client):
        login(client, 'oper', 'Oper1234!')
        resp = client.get('/dokumentacja/')
        assert resp.status_code == 200
        assert 'Dokumentacja'.encode('utf-8') in resp.data


# ── Tworzenie / edycja teczki ────────────────────────────────────────────────

class TestEntryCrud:
    def test_create_entry_redirects_to_detail(self, client):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/dokumentacja/nowa', data={
            'title': 'Dokumentacja szafy PSH', 'description': 'komplet rysunków',
            '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            e = DocEntry.query.filter_by(title='Dokumentacja szafy PSH').first()
            assert e is not None
            assert e.description == 'komplet rysunków'

    def test_create_entry_requires_title(self, client):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/dokumentacja/nowa', data={
            'title': '  ', 'description': 'x', '_csrf_token': token,
        })
        assert resp.status_code == 200
        assert 'wymagany'.encode('utf-8') in resp.data
        with flask_app.app_context():
            assert DocEntry.query.count() == 0

    def test_edit_entry_by_author(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client, 'Przed zmianą')
        token = _csrf(client)
        resp = client.post(f'/dokumentacja/{eid}/edytuj', data={
            'title': 'Po zmianie', 'description': 'nowy opis', '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            e = db.session.get(DocEntry, eid)
            assert e.title == 'Po zmianie'
            assert e.description == 'nowy opis'

    def test_delete_entry_forbidden_for_non_author(self, client, other_user):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client, 'Cudza teczka')
        logout(client)
        login(client, other_user, 'Other1234!')
        token = _csrf(client)
        resp = client.post(f'/dokumentacja/{eid}/usun', data={'_csrf_token': token})
        assert resp.status_code == 403
        with flask_app.app_context():
            assert db.session.get(DocEntry, eid) is not None

    def test_delete_entry_by_admin_cascades(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client, 'Do skasowania')
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (io.BytesIO(b'%PDF-1.4 test'), 'plik.pdf'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/notatki', data={
            'body': 'notatka', '_csrf_token': token,
        }, follow_redirects=True)

        logout(client)
        login(client, 'admin', 'Admin1234!')
        token = _csrf(client)
        resp = client.post(f'/dokumentacja/{eid}/usun', data={'_csrf_token': token},
                           follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert db.session.get(DocEntry, eid) is None
            assert DocFile.query.filter_by(entry_id=eid).count() == 0
            assert DocNote.query.filter_by(entry_id=eid).count() == 0


# ── Dokumenty ────────────────────────────────────────────────────────────────

class TestDocuments:
    def test_add_document_stores_file(self, client, _isolated_upload_dir):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        resp = client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (io.BytesIO(b'%PDF-1.4 hello'), 'instrukcja.pdf'),
            '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            f = DocFile.query.filter_by(entry_id=eid).first()
            assert f is not None
            assert f.kind == 'document'
            assert f.original_name == 'instrukcja.pdf'
            assert os.path.exists(os.path.join(_isolated_upload_dir, f.filename))

    def test_add_multiple_documents(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        resp = client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': [
                (io.BytesIO(b'a'), 'a.pdf'),
                (io.BytesIO(b'b'), 'b.docx'),
                (io.BytesIO(b'c'), 'c.xlsx'),
            ],
            '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert DocFile.query.filter_by(entry_id=eid, kind='document').count() == 3

    def test_reject_disallowed_extension(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        resp = client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (io.BytesIO(b'MZ...'), 'wirus.exe'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert DocFile.query.filter_by(entry_id=eid).count() == 0

    def test_image_via_documents_endpoint_classified_as_image(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (_png_bytes(), 'zdjecie.png'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        with flask_app.app_context():
            f = DocFile.query.filter_by(entry_id=eid).first()
            assert f.kind == 'image'

    def test_delete_document_removes_disk_file(self, client, _isolated_upload_dir):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (io.BytesIO(b'%PDF-1.4'), 'x.pdf'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        with flask_app.app_context():
            f = DocFile.query.filter_by(entry_id=eid).first()
            fid, fname = f.id, f.filename
        token = _csrf(client)
        resp = client.post(f'/dokumentacja/plik/{fid}/usun', data={'_csrf_token': token},
                           follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            assert db.session.get(DocFile, fid) is None
        assert not os.path.exists(os.path.join(_isolated_upload_dir, fname))


# ── Zdjęcia ──────────────────────────────────────────────────────────────────

class TestImages:
    def test_add_image_generates_thumbnail_and_jpeg(self, client, _isolated_upload_dir):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        resp = client.post(f'/dokumentacja/{eid}/zdjecia', data={
            'images': (_png_bytes(2000, 1500), 'foto.png'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            f = DocFile.query.filter_by(entry_id=eid, kind='image').first()
            assert f is not None
            assert f.filename.endswith('.jpg')
            assert f.thumb_filename and f.thumb_filename.startswith('thumb_')
            assert f.mime_type == 'image/jpeg'
            main = os.path.join(_isolated_upload_dir, f.filename)
            assert os.path.exists(main)
            with Image.open(main) as im:
                assert max(im.size) <= 2560

    def test_non_image_rejected_by_images_endpoint(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/zdjecia', data={
            'images': (io.BytesIO(b'%PDF-1.4'), 'niezdjecie.pdf'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        with flask_app.app_context():
            assert DocFile.query.filter_by(entry_id=eid).count() == 0

    def test_update_caption(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/zdjecia', data={
            'images': (_png_bytes(), 'foto.png'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        with flask_app.app_context():
            fid = DocFile.query.filter_by(entry_id=eid).first().id
        token = _csrf(client)
        client.post(f'/dokumentacja/plik/{fid}/podpis', data={
            'caption': 'Widok z przodu', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            assert db.session.get(DocFile, fid).caption == 'Widok z przodu'


# ── Serwowanie plików ────────────────────────────────────────────────────────

class TestServe:
    def _upload_pdf(self, client, eid):
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (io.BytesIO(b'%PDF-1.4 body'), 'dok.pdf'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        with flask_app.app_context():
            return DocFile.query.filter_by(entry_id=eid).first().filename

    def test_serve_inline_by_default(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        fname = self._upload_pdf(client, eid)
        resp = client.get(f'/dokumentacja/plik/{fname}')
        assert resp.status_code == 200
        assert 'attachment' not in resp.headers.get('Content-Disposition', '')
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'

    def test_serve_download_forces_attachment(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        fname = self._upload_pdf(client, eid)
        resp = client.get(f'/dokumentacja/plik/{fname}?download=1')
        assert resp.status_code == 200
        assert 'attachment' in resp.headers.get('Content-Disposition', '')
        assert 'dok.pdf' in resp.headers.get('Content-Disposition', '')

    def test_serve_forces_download_for_svg(self, client, _isolated_upload_dir):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (io.BytesIO(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'), 'rys.svg'),
            '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        with flask_app.app_context():
            f = DocFile.query.filter_by(entry_id=eid).first()
            assert f is not None
            fname = f.filename
        resp = client.get(f'/dokumentacja/plik/{fname}')
        assert resp.status_code == 200
        assert 'attachment' in resp.headers.get('Content-Disposition', '')
        assert resp.headers['Content-Type'] == 'application/octet-stream'

    def test_serve_unknown_file_404(self, client):
        login(client, 'oper', 'Oper1234!')
        resp = client.get('/dokumentacja/plik/nie-istnieje.pdf')
        assert resp.status_code == 404


# ── Notatki ──────────────────────────────────────────────────────────────────

class TestNotes:
    def test_add_note(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        resp = client.post(f'/dokumentacja/{eid}/notatki', data={
            'body': 'Pierwsza notatka', '_csrf_token': token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        with flask_app.app_context():
            n = DocNote.query.filter_by(entry_id=eid).first()
            assert n is not None and n.body == 'Pierwsza notatka'

    def test_empty_note_rejected(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/notatki', data={
            'body': '   ', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            assert DocNote.query.filter_by(entry_id=eid).count() == 0

    def test_edit_and_delete_note_by_author(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/notatki', data={
            'body': 'stara', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            nid = DocNote.query.filter_by(entry_id=eid).first().id

        token = _csrf(client)
        client.post(f'/dokumentacja/notatka/{nid}/edytuj', data={
            'body': 'nowa treść', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            assert db.session.get(DocNote, nid).body == 'nowa treść'

        token = _csrf(client)
        client.post(f'/dokumentacja/notatka/{nid}/usun', data={'_csrf_token': token},
                    follow_redirects=True)
        with flask_app.app_context():
            assert db.session.get(DocNote, nid) is None

    def test_edit_note_forbidden_for_other_user(self, client, other_user):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/notatki', data={
            'body': 'moja', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            nid = DocNote.query.filter_by(entry_id=eid).first().id
        logout(client)
        login(client, other_user, 'Other1234!')
        token = _csrf(client)
        resp = client.post(f'/dokumentacja/notatka/{nid}/usun', data={'_csrf_token': token})
        assert resp.status_code == 403
