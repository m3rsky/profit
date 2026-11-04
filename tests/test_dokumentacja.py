"""Testy modułu DOKUMENTACJA (teczki: dokumenty, zdjęcia, notatki)."""
import io
import os

import pytest
from PIL import Image

from app import app as flask_app, db
from models import User, DocEntry, DocFile, DocNote, DocMeasurement
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
        DocMeasurement.query.delete()
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

    def test_create_document_requires_title(self, client):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/dokumentacja/nowa', data={
            'rodzaj': 'dokument', 'title': '  ', 'description': 'x', '_csrf_token': token,
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


class TestMeasurementLabelOffsets:
    """Położenie czerwonej etykiety wymiaru (piksele w 3D, mm na wykroju) zapisuje się przy pomiarze."""

    def _mk(self, client, **extra):
        login(client, 'oper', 'Oper1234!')
        fid = _step_file(client)
        payload = _meas_payload()
        payload['data'].update(extra)
        mid = _post_json(client, f'/dokumentacja/podglad3d/{fid}/pomiary', payload).get_json()
        return fid, mid

    def test_offsets_saved_with_measurement(self, client):
        fid, rec = self._mk(client, offsets={'3d': [80, -40], 'flat': [12.5, 3]})
        assert rec['data']['offsets'] == {'3d': [80.0, -40.0], 'flat': [12.5, 3.0]}
        got = client.get(f'/dokumentacja/podglad3d/{fid}/pomiary').get_json()[0]['data']
        assert got['offsets']['3d'] == [80.0, -40.0]

    def test_no_offsets_by_default(self, client):
        _, rec = self._mk(client)
        assert 'offsets' not in rec['data']

    def test_offsets_changed_without_touching_label(self, client):
        fid, rec = self._mk(client)
        url = f"/dokumentacja/pomiar/{rec['id']}/edytuj"
        r = _post_json(client, url, {'offsets': {'3d': [10, 20]}}).get_json()
        assert r['label'] == 'Otwór do krawędzi' and r['data']['offsets'] == {'3d': [10.0, 20.0]}
        r = _post_json(client, url, {'offsets': {'flat': [1, 2]}}).get_json()           # scalanie widoków
        assert r['data']['offsets'] == {'3d': [10.0, 20.0], 'flat': [1.0, 2.0]}
        r = _post_json(client, url, {'label': 'Nowa'}).get_json()                       # nazwa nie kasuje położenia
        assert r['label'] == 'Nowa' and r['data']['offsets'] == {'3d': [10.0, 20.0], 'flat': [1.0, 2.0]}

    def test_reset_offsets_with_null(self, client):
        _, rec = self._mk(client, offsets={'3d': [5, 5], 'flat': [1, 1]})
        url = f"/dokumentacja/pomiar/{rec['id']}/edytuj"
        r = _post_json(client, url, {'offsets': {'3d': None}}).get_json()
        assert r['data']['offsets'] == {'flat': [1.0, 1.0]}
        r = _post_json(client, url, {'offsets': {'flat': None}}).get_json()
        assert 'offsets' not in r['data']

    def test_invalid_offsets_rejected(self, client):
        fid, rec = self._mk(client)
        url = f"/dokumentacja/pomiar/{rec['id']}/edytuj"
        for bad in ({'inny': [1, 2]}, {'3d': [1]}, {'3d': 'x'}, {'3d': [float('nan'), 0]}, 'x'):
            assert _post_json(client, url, {'offsets': bad}).status_code == 400, bad
        payload = _meas_payload()
        payload['data']['offsets'] = {'3d': [1]}
        assert _post_json(client, f'/dokumentacja/podglad3d/{fid}/pomiary', payload).status_code == 400

    def test_offsets_are_clamped(self, client):
        _, rec = self._mk(client, offsets={'3d': [999999, -999999]})
        assert rec['data']['offsets']['3d'] == [5000.0, -5000.0]

    def test_other_user_cannot_move_label(self, client, other_user):
        _, rec = self._mk(client)
        logout(client)
        login(client, other_user, 'Other1234!')
        r = _post_json(client, f"/dokumentacja/pomiar/{rec['id']}/edytuj", {'offsets': {'3d': [1, 1]}})
        assert r.status_code == 403


# ── Numer ZO ─────────────────────────────────────────────────────────────────

class TestZoNumber:
    def test_create_with_zo_and_search(self, client):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        client.post('/dokumentacja/nowa', data={
            'title': 'Szafa z ZO', 'zo_number': ' ZO/77/2026 ', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            e = DocEntry.query.filter_by(title='Szafa z ZO').first()
            assert e.zo_number == 'ZO/77/2026'
        resp = client.get('/dokumentacja/?q=77/2026')
        assert b'Szafa z ZO' in resp.data
        assert b'ZO/77/2026' in resp.data

    def test_edit_zo(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client, 'Teczka ZO')
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/edytuj', data={
            'title': 'Teczka ZO', 'zo_number': 'ZO/1', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            assert db.session.get(DocEntry, eid).zo_number == 'ZO/1'
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/edytuj', data={
            'title': 'Teczka ZO', 'zo_number': '', '_csrf_token': token,
        }, follow_redirects=True)
        with flask_app.app_context():
            assert db.session.get(DocEntry, eid).zo_number is None


# ── Modele STEP i podgląd 3D ─────────────────────────────────────────────────

STEP_BYTES = (b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x'),'2;1');\nENDSEC;\n"
              b"DATA;\nENDSEC;\nEND-ISO-10303-21;\n")


def _upload_step(client, eid, name='czesc.step', data=STEP_BYTES):
    token = _csrf(client)
    client.post(f'/dokumentacja/{eid}/dokumenty', data={
        'files': (io.BytesIO(data), name), '_csrf_token': token,
    }, content_type='multipart/form-data', follow_redirects=True)


class TestStep:
    @pytest.mark.parametrize('name', ['a.step', 'a.stp', 'A.STEP', 'A.STP', 'A.Stp'])
    def test_step_extensions_case_insensitive(self, client, name):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        _upload_step(client, eid, name)
        with flask_app.app_context():
            f = DocFile.query.filter_by(entry_id=eid).first()
            assert f is not None and f.kind == 'document'
            assert f.filename.endswith(('.step', '.stp'))
            assert f.is_step
            assert f.icon == 'bi-box'

    def test_step_without_header_rejected(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        _upload_step(client, eid, 'falsz.step', b'to nie jest STEP')
        with flask_app.app_context():
            assert DocFile.query.filter_by(entry_id=eid).count() == 0

    def test_view_3d_page_for_step(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client, 'Teczka 3D')
        _upload_step(client, eid, 'zlozenie.STEP')
        with flask_app.app_context():
            f = DocFile.query.filter_by(entry_id=eid).first()
            fid, fname = f.id, f.filename
        resp = client.get(f'/dokumentacja/podglad3d/{fid}')
        assert resp.status_code == 200
        assert f'/dokumentacja/plik/{fname}'.encode() in resp.data
        assert b'zlozenie.STEP' in resp.data
        assert b'/static/podglad3d/lib/three.module.js' in resp.data
        detail = client.get(f'/dokumentacja/{eid}')
        assert f'/dokumentacja/podglad3d/{fid}'.encode() in detail.data

    def test_view_3d_requires_login(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        _upload_step(client, eid)
        with flask_app.app_context():
            fid = DocFile.query.filter_by(entry_id=eid).first().id
        logout(client)
        resp = client.get(f'/dokumentacja/podglad3d/{fid}')
        assert resp.status_code in (302, 401)

    def test_view_3d_404_for_non_step_and_missing(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (io.BytesIO(b'%PDF-1.4'), 'x.pdf'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        with flask_app.app_context():
            fid = DocFile.query.filter_by(entry_id=eid).first().id
        assert client.get(f'/dokumentacja/podglad3d/{fid}').status_code == 404
        assert client.get('/dokumentacja/podglad3d/999999').status_code == 404

    def test_view_3d_does_not_accept_path(self, client):
        login(client, 'oper', 'Oper1234!')
        assert client.get('/dokumentacja/podglad3d/..%2f..%2fconfig.py').status_code == 404
        assert client.get('/dokumentacja/podglad3d/abc').status_code == 404

    def test_step_served_from_same_origin(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        _upload_step(client, eid)
        with flask_app.app_context():
            fname = DocFile.query.filter_by(entry_id=eid).first().filename
        resp = client.get(f'/dokumentacja/plik/{fname}')
        assert resp.status_code == 200
        assert resp.data == STEP_BYTES

    def test_wasm_served_with_proper_mime(self, client):
        resp = client.get('/static/podglad3d/lib/occt-import-js.wasm')
        assert resp.status_code == 200
        assert resp.mimetype == 'application/wasm'
        resp.close()

    def test_viewer_libs_present(self, client):
        for name in ('three.module.js', 'three.core.js', 'addons/controls/OrbitControls.js',
                     'occt-import-js.js', 'occt-import-js-worker.js'):
            resp = client.get(f'/static/podglad3d/lib/{name}')
            assert resp.status_code == 200, name
            resp.close()


# ── Limity rozmiaru ──────────────────────────────────────────────────────────

class TestSizeLimits:
    @pytest.fixture(autouse=True)
    def _restore_limits(self):
        keys = ('DOC_MAX_FILE_BYTES', 'DOC_STEP_MAX_BYTES', 'DEFAULT_MAX_UPLOAD')
        prev = {k: flask_app.config[k] for k in keys}
        yield
        flask_app.config.update(prev)

    def test_default_limits_configured(self):
        assert flask_app.config['MAX_CONTENT_LENGTH'] >= 200 * 1024 * 1024
        assert flask_app.config['DOC_STEP_MAX_BYTES'] == 200 * 1024 * 1024
        assert flask_app.config['DOC_MAX_FILE_BYTES'] == 32 * 1024 * 1024

    def test_step_has_higher_per_file_limit_than_other_documents(self, client):
        flask_app.config['DOC_MAX_FILE_BYTES'] = 200
        flask_app.config['DOC_STEP_MAX_BYTES'] = 5000
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        _upload_step(client, eid, 'duzy.step', STEP_BYTES + b' ' * 500)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (io.BytesIO(b'%PDF-1.4' + b' ' * 500), 'duzy.pdf'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        with flask_app.app_context():
            names = [f.original_name for f in DocFile.query.filter_by(entry_id=eid)]
        assert names == ['duzy.step']

    def test_step_over_its_limit_rejected_with_message(self, client):
        flask_app.config['DOC_STEP_MAX_BYTES'] = 50
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        resp = client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (io.BytesIO(STEP_BYTES), 'za_duzy.step'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        assert 'limit to'.encode() in resp.data
        with flask_app.app_context():
            assert DocFile.query.filter_by(entry_id=eid).count() == 0

    def test_other_endpoints_keep_old_request_limit(self, client):
        flask_app.config['DEFAULT_MAX_UPLOAD'] = 1000
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/zdjecia', data={
            'images': (_png_bytes(300, 300), 'foto.png'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        with flask_app.app_context():
            assert DocFile.query.filter_by(entry_id=eid).count() == 0
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (_png_bytes(300, 300), 'foto.png'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        with flask_app.app_context():
            assert DocFile.query.filter_by(entry_id=eid).count() == 1


# ── Lokalizacja plików ───────────────────────────────────────────────────────

class TestStorage:
    def test_upload_folder_outside_static(self):
        from config import Config
        norm = Config.DOKUMENTACJA_UPLOAD_FOLDER.replace('\\', '/')
        assert '/static/' not in norm
        assert '/instance/' in norm

    def test_legacy_files_are_moved(self, tmp_path):
        from dokumentacja.storage import migrate_legacy_uploads
        old, new = tmp_path / 'static_old', tmp_path / 'instance_new'
        old.mkdir()
        new.mkdir()
        (old / 'a.pdf').write_bytes(b'A')
        (old / 'b.jpg').write_bytes(b'B')
        (new / 'b.jpg').write_bytes(b'NEW')
        assert migrate_legacy_uploads(str(old), str(new)) == 1
        assert (new / 'a.pdf').read_bytes() == b'A'
        assert (new / 'b.jpg').read_bytes() == b'NEW'
        assert (old / 'b.jpg').exists()
        assert migrate_legacy_uploads(str(old), str(new)) == 0

    def test_legacy_folder_removed_when_empty(self, tmp_path):
        from dokumentacja.storage import migrate_legacy_uploads
        old, new = tmp_path / 'o', tmp_path / 'n'
        old.mkdir()
        (old / 'x.pdf').write_bytes(b'X')
        assert migrate_legacy_uploads(str(old), str(new)) == 1
        assert not old.exists()

    def test_missing_legacy_folder_is_noop(self, tmp_path):
        from dokumentacja.storage import migrate_legacy_uploads
        assert migrate_legacy_uploads(str(tmp_path / 'brak'), str(tmp_path / 'n')) == 0
        assert migrate_legacy_uploads(None, str(tmp_path / 'n')) == 0


class TestMigration:
    def test_migration_adds_zo_number_to_existing_table(self, app):
        """Na produkcji tabela doc_entries już istnieje bez kolumny zo_number."""
        from sqlalchemy import create_engine, inspect, text
        import app as app_module
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE doc_entries (id INTEGER PRIMARY KEY, '
                              'title VARCHAR(256), description TEXT)'))

        class FakeDb:   # _migrate_schema używa tylko db.engine
            pass
        FakeDb.engine = engine
        real = app_module.db
        app_module.db = FakeDb
        try:
            app_module._migrate_schema()
            app_module._migrate_schema()   # drugi raz nie wywala
        finally:
            app_module.db = real
        assert 'zo_number' in [c['name'] for c in inspect(engine).get_columns('doc_entries')]


# ── Zapisane pomiary w podglądzie 3D ─────────────────────────────────────────

def _meas_payload(label='Otwór do krawędzi', element='Zamek D5'):
    return {'label': label, 'element': element, 'data': {
        'A': {'k': 'circle', 'c': [1, 2, 3], 'r': 3.0, 'n': [0, 0, 1], 'part': 1},
        'B': {'k': 'line', 'a': [0, 0, 0], 'b': [10, 0, 0], 'dir': [1, 0, 0], 'len': 10.0},
        'rows': [['Odległość', '5.00 mm'], ['Średnica otworu', 'Ø6.00']],
        'segs': [[[1, 2, 3], [1, 0, 3]]],
    }}


def _post_json(client, url, payload):
    return client.post(url, json=payload, headers={'X-CSRF-Token': _csrf(client)})


def _step_file(client, name='model.step'):
    eid = _mk_entry(client)
    _upload_step(client, eid, name)
    with flask_app.app_context():
        return DocFile.query.filter_by(entry_id=eid).first().id


class TestMeasurements:
    def test_create_and_list(self, client):
        login(client, 'oper', 'Oper1234!')
        fid = _step_file(client)
        resp = _post_json(client, f'/dokumentacja/podglad3d/{fid}/pomiary', _meas_payload())
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['label'] == 'Otwór do krawędzi' and body['element'] == 'Zamek D5' and body['can_edit'] is True
        lst = client.get(f'/dokumentacja/podglad3d/{fid}/pomiary').get_json()
        assert len(lst) == 1 and lst[0]['data']['rows'][0] == ['Odległość', '5.00 mm']
        assert lst[0]['data']['A']['r'] == 3.0 and lst[0]['author'] == 'oper'
        with flask_app.app_context():
            assert DocMeasurement.query.filter_by(file_id=fid).count() == 1

    def test_only_step_files(self, client):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/dokumenty', data={
            'files': (io.BytesIO(b'%PDF-1.4'), 'x.pdf'), '_csrf_token': token,
        }, content_type='multipart/form-data', follow_redirects=True)
        with flask_app.app_context():
            fid = DocFile.query.filter_by(entry_id=eid).first().id
        assert client.get(f'/dokumentacja/podglad3d/{fid}/pomiary').status_code == 404
        assert _post_json(client, f'/dokumentacja/podglad3d/{fid}/pomiary', _meas_payload()).status_code == 404

    def test_requires_login_and_csrf(self, client):
        login(client, 'oper', 'Oper1234!')
        fid = _step_file(client)
        no_csrf = client.post(f'/dokumentacja/podglad3d/{fid}/pomiary', json=_meas_payload())
        assert no_csrf.status_code == 403
        logout(client)
        assert client.get(f'/dokumentacja/podglad3d/{fid}/pomiary').status_code in (302, 401)

    def test_validation_rejects_bad_data(self, client):
        login(client, 'oper', 'Oper1234!')
        fid = _step_file(client)
        url = f'/dokumentacja/podglad3d/{fid}/pomiary'
        bad_kind = _meas_payload(); bad_kind['data']['A']['k'] = 'sfera'
        no_rows = _meas_payload(); no_rows['data']['rows'] = []
        nan = _meas_payload(); nan['data']['A']['r'] = float('nan')
        too_many = _meas_payload(); too_many['data']['rows'] = [['a', 'b']] * 13
        bad_vec = _meas_payload(); bad_vec['data']['A']['c'] = [1, 2]
        for payload in (bad_kind, no_rows, nan, too_many, bad_vec, {'data': 'x'}, {}):
            assert _post_json(client, url, payload).status_code == 400, payload
        assert client.get(url).get_json() == []

    def test_unknown_fields_are_dropped(self, client):
        login(client, 'oper', 'Oper1234!')
        fid = _step_file(client)
        payload = _meas_payload()
        payload['data']['A']['evil'] = '<script>alert(1)</script>'
        payload['data']['injected'] = {'x': 1}
        _post_json(client, f'/dokumentacja/podglad3d/{fid}/pomiary', payload)
        data = client.get(f'/dokumentacja/podglad3d/{fid}/pomiary').get_json()[0]['data']
        assert 'evil' not in data['A'] and 'injected' not in data

    def test_rename_and_delete_by_author(self, client):
        login(client, 'oper', 'Oper1234!')
        fid = _step_file(client)
        mid = _post_json(client, f'/dokumentacja/podglad3d/{fid}/pomiary', _meas_payload()).get_json()['id']
        r = _post_json(client, f'/dokumentacja/pomiar/{mid}/edytuj', {'label': 'Nowa nazwa'})
        assert r.status_code == 200 and r.get_json()['label'] == 'Nowa nazwa'
        assert _post_json(client, f'/dokumentacja/pomiar/{mid}/usun', {}).status_code == 200
        with flask_app.app_context():
            assert DocMeasurement.query.count() == 0

    def test_other_user_sees_but_cannot_change(self, client, other_user):
        login(client, 'oper', 'Oper1234!')
        fid = _step_file(client)
        mid = _post_json(client, f'/dokumentacja/podglad3d/{fid}/pomiary', _meas_payload()).get_json()['id']
        logout(client)
        login(client, other_user, 'Other1234!')
        lst = client.get(f'/dokumentacja/podglad3d/{fid}/pomiary').get_json()
        assert len(lst) == 1 and lst[0]['can_edit'] is False
        assert _post_json(client, f'/dokumentacja/pomiar/{mid}/edytuj', {'label': 'x'}).status_code == 403
        assert _post_json(client, f'/dokumentacja/pomiar/{mid}/usun', {}).status_code == 403
        # ale może dodać własny pomiar do cudzego pliku
        assert _post_json(client, f'/dokumentacja/podglad3d/{fid}/pomiary', _meas_payload('Mój')).status_code == 201

    def test_admin_can_delete_foreign_measurement(self, client):
        login(client, 'oper', 'Oper1234!')
        fid = _step_file(client)
        mid = _post_json(client, f'/dokumentacja/podglad3d/{fid}/pomiary', _meas_payload()).get_json()['id']
        logout(client)
        login(client, 'admin', 'Admin1234!')
        assert _post_json(client, f'/dokumentacja/pomiar/{mid}/usun', {}).status_code == 200

    def test_deleting_file_removes_its_measurements(self, client):
        login(client, 'oper', 'Oper1234!')
        fid = _step_file(client)
        _post_json(client, f'/dokumentacja/podglad3d/{fid}/pomiary', _meas_payload())
        _post_json(client, f'/dokumentacja/podglad3d/{fid}/pomiary', _meas_payload('Drugi'))
        client.post(f'/dokumentacja/plik/{fid}/usun', data={'_csrf_token': _csrf(client)}, follow_redirects=True)
        with flask_app.app_context():
            assert DocMeasurement.query.count() == 0

    def test_flat_space_and_k_roundtrip(self, client):
        login(client, 'oper', 'Oper1234!')
        fid = _step_file(client)
        url = f'/dokumentacja/podglad3d/{fid}/pomiary'
        payload = _meas_payload()
        payload['data']['space'] = 'flat'
        payload['data']['K'] = 0.33
        assert _post_json(client, url, payload).status_code == 201
        data = client.get(url).get_json()[0]['data']
        assert data['space'] == 'flat' and data['K'] == 0.33

    def test_space_defaults_to_3d_and_rejects_unknown(self, client):
        login(client, 'oper', 'Oper1234!')
        fid = _step_file(client)
        url = f'/dokumentacja/podglad3d/{fid}/pomiary'
        assert _post_json(client, url, _meas_payload()).status_code == 201
        assert client.get(url).get_json()[0]['data']['space'] == '3d'
        bad_space = _meas_payload(); bad_space['data']['space'] = 'inna'
        bad_k = _meas_payload(); bad_k['data']['K'] = 7
        nan_k = _meas_payload(); nan_k['data']['K'] = float('nan')
        for payload in (bad_space, bad_k, nan_k):
            assert _post_json(client, url, payload).status_code == 400



# ── Podział na Projekty i Dokumenty ──────────────────────────────────────────

def _oper_id():
    with flask_app.app_context():
        return User.query.filter_by(username='oper').first().id


def _db_entry(kind='project', user_id=None, **fields):
    with flask_app.app_context():
        e = DocEntry(kind=kind, title=fields.pop('title', ''), user_id=user_id or _oper_id(), **fields)
        db.session.add(e)
        db.session.commit()
        return e.id


def _post_project(http, **fields):
    data = {'rodzaj': 'projekt', '_csrf_token': _csrf(http)}
    data.update(fields)
    http.post('/dokumentacja/nowa', data=data, follow_redirects=True)


class TestProjects:
    def test_all_project_fields_are_optional(self, client):
        login(client, 'oper', 'Oper1234!')
        resp = client.post('/dokumentacja/nowa', data={'rodzaj': 'projekt', '_csrf_token': _csrf(client)})
        assert resp.status_code == 302
        with flask_app.app_context():
            e = DocEntry.query.one()
            assert e.kind == 'project' and e.display_title == 'Projekt bez danych'

    def test_project_fields_saved_and_listed(self, client):
        login(client, 'oper', 'Oper1234!')
        _post_project(client, zo_number=' ZO/12/2026 ', drawing_number='RYS-327', client='ACME Sp. z o.o.', product='PSH 600x800')
        with flask_app.app_context():
            e = DocEntry.query.one()
            assert (e.zo_number, e.drawing_number, e.client, e.product) == ('ZO/12/2026', 'RYS-327', 'ACME Sp. z o.o.', 'PSH 600x800')
        html = client.get('/dokumentacja/').get_data(as_text=True)
        for text in ('Nr ZO', 'Nr rysunku', 'Klient', 'Produkt', 'ZO/12/2026', 'RYS-327', 'ACME Sp. z o.o.', 'PSH 600x800'):
            assert text in html

    @pytest.mark.parametrize('q', ['12/2026', 'rys-327', 'acme', 'psh 600'])
    def test_search_by_each_column(self, client, q):
        login(client, 'oper', 'Oper1234!')
        _db_entry(zo_number='ZO/12/2026', drawing_number='RYS-327', client='ACME', product='PSH 600x800')
        _db_entry(zo_number='ZO/99/2026', drawing_number='INNY', client='Beta', product='RW-N')
        html = client.get('/dokumentacja/', query_string={'q': q}).get_data(as_text=True)
        assert 'ZO/12/2026' in html and 'ZO/99/2026' not in html

    def test_search_treats_wildcards_literally(self, client):
        login(client, 'oper', 'Oper1234!')
        _db_entry(zo_number='ZO/1', product='A')
        _db_entry(zo_number='ZO/2', product='B')
        html = client.get('/dokumentacja/', query_string={'q': '%'}).get_data(as_text=True)
        assert 'ZO/1' not in html and 'ZO/2' not in html

    def test_client_filter_and_sorting(self, client):
        login(client, 'oper', 'Oper1234!')
        _db_entry(zo_number='ZO/B', client='Beta')
        _db_entry(zo_number='ZO/A', client='Alfa')
        _db_entry(zo_number='ZO/C', client='Alfa')
        only = client.get('/dokumentacja/', query_string={'klient': 'Alfa'}).get_data(as_text=True)
        assert 'ZO/A' in only and 'ZO/C' in only and 'ZO/B' not in only
        asc = client.get('/dokumentacja/', query_string={'sort': 'zo', 'dir': 'asc'}).get_data(as_text=True)
        assert asc.index('ZO/A') < asc.index('ZO/B') < asc.index('ZO/C')
        desc = client.get('/dokumentacja/', query_string={'sort': 'zo', 'dir': 'desc'}).get_data(as_text=True)
        assert desc.index('ZO/C') < desc.index('ZO/B') < desc.index('ZO/A')

    def test_pagination(self, client):
        login(client, 'oper', 'Oper1234!')
        for i in range(25):
            _db_entry(zo_number=f'ZO/{i:03d}')
        p1 = client.get('/dokumentacja/', query_string={'per_page': 10, 'sort': 'zo', 'dir': 'asc'}).get_data(as_text=True)
        p3 = client.get('/dokumentacja/', query_string={'per_page': 10, 'sort': 'zo', 'dir': 'asc', 'page': 3}).get_data(as_text=True)
        assert 'ZO/000' in p1 and 'ZO/010' not in p1 and '1 / 3 (25 pozycji)' in p1
        assert 'ZO/020' in p3 and 'ZO/019' not in p3

    def test_several_projects_under_one_zo_with_duplicate_warning(self, client):
        login(client, 'oper', 'Oper1234!')
        _post_project(client, zo_number='ZO/5', drawing_number='R1', product='Szafa A')
        _post_project(client, zo_number='ZO/5', drawing_number='R2', product='Szafa B')
        with flask_app.app_context():
            assert DocEntry.query.filter_by(zo_number='ZO/5').count() == 2
        resp = client.post('/dokumentacja/nowa', data={'rodzaj': 'projekt', 'zo_number': 'zo/5', 'product': 'SZAFA A',
                                                       '_csrf_token': _csrf(client)}, follow_redirects=True)
        assert 'istnieje już projekt'.encode() in resp.data      # tylko ostrzeżenie
        with flask_app.app_context():
            assert DocEntry.query.filter_by(kind='project').count() == 3


class TestDocumentsSplit:
    def test_projects_and_documents_are_separate_lists(self, client):
        login(client, 'oper', 'Oper1234!')
        _db_entry('project', zo_number='ZO/PROJ')
        _db_entry('document', title='Instrukcja montazu', category='Instrukcje')
        projects = client.get('/dokumentacja/').get_data(as_text=True)
        documents = client.get('/dokumentacja/dokumenty').get_data(as_text=True)
        assert 'ZO/PROJ' in projects and 'Instrukcja montazu' not in projects
        assert 'Instrukcja montazu' in documents and 'ZO/PROJ' not in documents
        assert 'Instrukcje' in documents
        assert 'doc-tab-count' in projects and 'Dokumenty' in projects and 'Projekty' in documents

    def test_document_search_and_category_filter(self, client):
        login(client, 'oper', 'Oper1234!')
        _db_entry('document', title='Norma EN 1090', category='Normy')
        _db_entry('document', title='Procedura NG', category='Procedury')
        html = client.get('/dokumentacja/dokumenty', query_string={'kategoria': 'Normy'}).get_data(as_text=True)
        assert 'Norma EN 1090' in html and 'Procedura NG' not in html
        html = client.get('/dokumentacja/dokumenty', query_string={'q': 'procedura'}).get_data(as_text=True)
        assert 'Procedura NG' in html and 'Norma EN 1090' not in html

    def test_document_can_be_linked_to_project(self, client):
        login(client, 'oper', 'Oper1234!')
        pid = _db_entry('project', zo_number='ZO/LINK', product='Szafa')
        other_doc = _db_entry('document', title='Inny dokument')
        resp = client.post('/dokumentacja/nowa', data={'rodzaj': 'dokument', 'title': 'Karta pomiarowa',
                                                       'project_id': str(pid), '_csrf_token': _csrf(client)})
        assert resp.status_code == 302
        with flask_app.app_context():
            d = DocEntry.query.filter_by(title='Karta pomiarowa').one()
            assert d.project_id == pid and d.project.zo_number == 'ZO/LINK'
        # projekt pokazuje powiązane dokumenty, lista dokumentów filtruje po projekcie
        detail = client.get(f'/dokumentacja/{pid}').get_data(as_text=True)
        assert 'Dokumenty powiązane' in detail and 'Karta pomiarowa' in detail and 'Inny dokument' not in detail
        listed = client.get('/dokumentacja/dokumenty', query_string={'projekt': pid}).get_data(as_text=True)
        assert 'Karta pomiarowa' in listed and 'Inny dokument' not in listed
        only_free = client.get('/dokumentacja/dokumenty', query_string={'powiazanie': 'nie'}).get_data(as_text=True)
        assert 'Inny dokument' in only_free and 'Karta pomiarowa' not in only_free
        # powiązać można tylko z projektem (nie z dokumentem ani nieistniejącym wpisem)
        for bad in (str(other_doc), '99999', 'abc'):
            client.post('/dokumentacja/nowa', data={'rodzaj': 'dokument', 'title': f'Zly {bad}', 'project_id': bad,
                                                    '_csrf_token': _csrf(client)})
        with flask_app.app_context():
            assert all(e.project_id is None for e in DocEntry.query.filter(DocEntry.title.like('Zly %')))

    def test_new_document_form_preselects_project(self, client):
        login(client, 'oper', 'Oper1234!')
        pid = _db_entry('project', zo_number='ZO/PRE')
        html = client.get('/dokumentacja/nowa', query_string={'rodzaj': 'dokument', 'projekt': pid}).get_data(as_text=True)
        assert f'value="{pid}" selected' in html

    def test_deleting_project_keeps_linked_documents(self, client):
        login(client, 'oper', 'Oper1234!')
        pid = _db_entry('project', zo_number='ZO/DEL')
        did = _db_entry('document', title='Zostaje', project_id=pid)
        client.post(f'/dokumentacja/{pid}/usun', data={'_csrf_token': _csrf(client)})
        with flask_app.app_context():
            assert db.session.get(DocEntry, pid) is None
            assert db.session.get(DocEntry, did).project_id is None

    def test_edit_document_category_and_project(self, client):
        login(client, 'oper', 'Oper1234!')
        pid = _db_entry('project', zo_number='ZO/E')
        did = _db_entry('document', title='Dok')
        client.post(f'/dokumentacja/{did}/edytuj', data={'title': 'Dok 2', 'category': 'Normy', 'project_id': str(pid),
                                                         '_csrf_token': _csrf(client)})
        with flask_app.app_context():
            d = db.session.get(DocEntry, did)
            assert (d.title, d.category, d.project_id) == ('Dok 2', 'Normy', pid)
        client.post(f'/dokumentacja/{pid}/edytuj', data={'zo_number': 'ZO/E2', 'client': 'K', 'product': 'P',
                                                         'drawing_number': 'D', '_csrf_token': _csrf(client)})
        with flask_app.app_context():
            p = db.session.get(DocEntry, pid)
            assert (p.zo_number, p.client, p.product, p.drawing_number) == ('ZO/E2', 'K', 'P', 'D')

    def test_change_kind_both_ways(self, client):
        login(client, 'oper', 'Oper1234!')
        pid = _db_entry('project', zo_number='ZO/K', client='Klient', product='Prod')
        did = _db_entry('document', title='Dokument', category='Normy')
        child = _db_entry('document', title='Dziecko', project_id=pid)
        client.post(f'/dokumentacja/{pid}/rodzaj', data={'rodzaj': 'dokument', '_csrf_token': _csrf(client)})
        client.post(f'/dokumentacja/{did}/rodzaj', data={'rodzaj': 'projekt', '_csrf_token': _csrf(client)})
        with flask_app.app_context():
            p, d, c = db.session.get(DocEntry, pid), db.session.get(DocEntry, did), db.session.get(DocEntry, child)
            assert p.kind == 'document' and 'ZO/K' in p.title and c.project_id is None
            assert d.kind == 'project' and d.category is None and d.project_id is None

    def test_change_kind_forbidden_for_other_user(self, client, other_user):
        login(client, 'oper', 'Oper1234!')
        pid = _db_entry('project', zo_number='ZO/F')
        logout(client)
        login(client, other_user, 'Other1234!')
        resp = client.post(f'/dokumentacja/{pid}/rodzaj', data={'rodzaj': 'dokument', '_csrf_token': _csrf(client)})
        assert resp.status_code == 403


class TestBulkDelete:
    def test_bulk_delete_own_entries_and_skip_foreign(self, client, other_user):
        with flask_app.app_context():
            other_id = User.query.filter_by(username=other_user).first().id
        mine = [_db_entry(zo_number=f'ZO/M{i}') for i in range(3)]
        foreign = _db_entry(zo_number='ZO/OBCY', user_id=other_id)
        login(client, 'oper', 'Oper1234!')
        resp = client.post('/dokumentacja/usun-zaznaczone', data={'entry_ids': [str(i) for i in mine + [foreign]],
                                                                   'back': 'projects', '_csrf_token': _csrf(client)},
                           follow_redirects=True)
        assert 'Usunięto pozycji: 3'.encode() in resp.data and 'Pominięto'.encode() in resp.data
        with flask_app.app_context():
            assert DocEntry.query.count() == 1 and db.session.get(DocEntry, foreign) is not None

    def test_bulk_delete_by_admin_and_nothing_selected(self, client):
        ids = [_db_entry('document', title=f'D{i}') for i in range(2)]
        login(client, 'admin', 'Admin1234!')
        resp = client.post('/dokumentacja/usun-zaznaczone', data={'back': 'documents', '_csrf_token': _csrf(client)},
                           follow_redirects=True)
        assert 'Nie zaznaczono'.encode() in resp.data
        client.post('/dokumentacja/usun-zaznaczone', data={'entry_ids': [str(i) for i in ids], 'back': 'documents',
                                                          '_csrf_token': _csrf(client)})
        with flask_app.app_context():
            assert DocEntry.query.count() == 0

    def test_bulk_delete_removes_files_from_disk(self, client, _isolated_upload_dir):
        login(client, 'oper', 'Oper1234!')
        eid = _mk_entry(client)
        token = _csrf(client)
        client.post(f'/dokumentacja/{eid}/dokumenty', data={'files': (io.BytesIO(b'%PDF-1.4'), 'a.pdf'),
                                                           '_csrf_token': token}, content_type='multipart/form-data')
        with flask_app.app_context():
            fname = DocFile.query.filter_by(entry_id=eid).first().filename
        assert os.path.exists(os.path.join(_isolated_upload_dir, fname))
        client.post('/dokumentacja/usun-zaznaczone', data={'entry_ids': [str(eid)], '_csrf_token': _csrf(client)})
        assert not os.path.exists(os.path.join(_isolated_upload_dir, fname))


class TestZoSuggestions:
    def test_requires_login_and_min_length(self, client):
        logout(client)
        assert client.get('/dokumentacja/zo-info?zo=ZO1').status_code in (302, 401)
        login(client, 'oper', 'Oper1234!')
        assert client.get('/dokumentacja/zo-info?zo=Z').get_json() == {'items': []}
        assert client.get('/dokumentacja/zo-info?zo=ZO/NIEMA/1').get_json() == {'items': []}

    def test_suggestions_from_kontrola_produkcja_and_projects(self, client):
        from models import ControlOrder, ProdSheet, ProdRow
        import json as _json
        login(client, 'oper', 'Oper1234!')
        with flask_app.app_context():
            db.session.add(ControlOrder(zo_number='ZO/777', product_name='PSH 600x800', client='ACME'))
            sheet = ProdSheet(name='TEST-ZO-INFO', columns_json='["ZO","Nazwa towaru","Odbiorca"]',
                              mapping_json=_json.dumps({'zo': 0, 'product': 1, 'client': 2}))
            db.session.add(sheet)
            db.session.flush()
            db.session.add(ProdRow(sheet_id=sheet.id, position=1, zo='ZO/777',
                                   data=_json.dumps(['ZO/777', 'RW-N 400x600', 'Beta Sp. z o.o.'])))
            db.session.commit()
            sheet_id = sheet.id
        _db_entry(zo_number='ZO/777', client='Gamma', product='Szafa G')
        try:
            items = client.get('/dokumentacja/zo-info', query_string={'zo': 'zo/777'}).get_json()['items']
            got = {(i['client'], i['product'], i['source']) for i in items}
            assert ('ACME', 'PSH 600x800', 'Kontrola etapowa') in got
            assert ('Beta Sp. z o.o.', 'RW-N 400x600', 'Lista produkcyjna') in got
            assert ('Gamma', 'Szafa G', 'Dokumentacja') in got
        finally:
            with flask_app.app_context():
                ProdRow.query.filter_by(sheet_id=sheet_id).delete()
                ProdSheet.query.filter_by(id=sheet_id).delete()
                ControlOrder.query.filter_by(zo_number='ZO/777').delete()
                db.session.commit()

    def test_identical_pairs_are_returned_once(self, client):
        login(client, 'oper', 'Oper1234!')
        _db_entry(zo_number='ZO/DUP', client='K', product='P')
        _db_entry(zo_number='ZO/DUP', client='k', product='p')
        items = client.get('/dokumentacja/zo-info', query_string={'zo': 'ZO/DUP'}).get_json()['items']
        assert len(items) == 1

    def test_form_includes_suggestion_script(self, client):
        login(client, 'oper', 'Oper1234!')
        html = client.get('/dokumentacja/nowa').get_data(as_text=True)
        assert '/dokumentacja/zo-info' in html and 'name="zo_number"' in html


class TestKindMigration:
    def test_existing_entries_are_classified(self, app):
        """Stare teczki: z numerem ZO albo plikiem STEP trafiają do Projektów, reszta do Dokumentów."""
        from sqlalchemy import create_engine, text
        import app as app_module
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE doc_entries (id INTEGER PRIMARY KEY, title VARCHAR(256), '
                              'description TEXT, zo_number VARCHAR(64))'))
            conn.execute(text('CREATE TABLE doc_files (id INTEGER PRIMARY KEY, entry_id INTEGER, original_name VARCHAR(256))'))
            conn.execute(text("INSERT INTO doc_entries (id, title, zo_number) VALUES "
                              "(1, 'Z numerem ZO', 'ZO/1'), (2, 'Ze STEP', NULL), (3, 'Instrukcja', NULL), (4, 'Pusty ZO', '')"))
            conn.execute(text("INSERT INTO doc_files (entry_id, original_name) VALUES (2, 'Czesc.STEP'), (3, 'instr.pdf')"))

        class FakeDb:
            pass
        FakeDb.engine = engine
        real = app_module.db
        app_module.db = FakeDb
        try:
            app_module._migrate_schema()
            app_module._migrate_schema()          # drugi raz niczego nie zmienia
        finally:
            app_module.db = real
        with engine.connect() as conn:
            kinds = dict(conn.execute(text('SELECT id, kind FROM doc_entries')).fetchall())
            cols = {r[1] for r in conn.execute(text('PRAGMA table_info(doc_entries)')).fetchall()}
        assert kinds == {1: 'project', 2: 'project', 3: 'document', 4: 'document'}
        assert {'drawing_number', 'client', 'product', 'category', 'project_id', 'kind'} <= cols
