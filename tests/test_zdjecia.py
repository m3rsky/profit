"""Testy zdjęć: zmniejszanie przy zapisie, kompresja starych plików, retencja, strona superusera."""
import io
import os
import random
from datetime import datetime, timedelta

import pytest
from PIL import Image

import photo_tools
from app import app as flask_app, db
from models import (AppSetting, AuditLog, ChecklistTemplate, Photo, QARReport, Report,
                    ReportItem, User)
from test_app import login, logout, _csrf


def _jpeg_bytes(w, h, quality=95):
    """Zdjęcie z szumem (źle się kompresuje, więc jest duże jak z telefonu)."""
    rnd = random.Random(1)
    img = Image.new('RGB', (w, h))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(w * h)])
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=quality)
    return buf.getvalue()


BIG = _jpeg_bytes(2400, 1800)


@pytest.fixture(autouse=True)
def _clean():
    yield
    with flask_app.app_context():
        AppSetting.query.delete()
        Photo.query.delete()
        db.session.commit()


def _upload_dir():
    return flask_app.config['UPLOAD_FOLDER']


def _new_item(client):
    login(client, 'oper', 'Oper1234!')
    token = _csrf(client)
    with flask_app.app_context():
        tmpl_id = ChecklistTemplate.query.filter_by(name='Test szablon').first().id
    client.post('/checklist/new', data={'template_id': tmpl_id, 'title': 'ZDJ-test', '_csrf_token': token},
                follow_redirects=True)
    with flask_app.app_context():
        report = Report.query.filter(Report.title.like('%ZDJ-test%')).order_by(Report.id.desc()).first()
        return report.id, report.items.first().id


def _old_photo(name, days, data=BIG, status='completed'):
    """Zdjęcie sprzed `days` dni w raporcie o danym statusie (plik w folderze uploadów)."""
    os.makedirs(_upload_dir(), exist_ok=True)
    with open(os.path.join(_upload_dir(), name), 'wb') as fh:
        fh.write(data)
    with flask_app.app_context():
        user = User.query.filter_by(username='oper').first()
        tmpl = ChecklistTemplate.query.filter_by(name='Test szablon').first()
        report = Report(user_id=user.id, template_id=tmpl.id, title='ZDJ-' + name, status=status,
                        report_type='kontroler')
        db.session.add(report)
        db.session.flush()
        task = tmpl.categories[0].tasks.first() if hasattr(tmpl.categories[0].tasks, 'first') else tmpl.categories[0].tasks[0]
        item = ReportItem(report_id=report.id, task_id=task.id)
        db.session.add(item)
        db.session.flush()
        photo = Photo(report_item_id=item.id, filename=name,
                      uploaded_at=datetime.utcnow() - timedelta(days=days))
        db.session.add(photo)
        db.session.commit()
        return photo.id


class TestUstawienia:
    def test_defaults(self, app):
        with app.app_context():
            s = photo_tools.get_settings()
            assert s['photo_kk_max_px'] == 1600 and s['photo_retention_days'] == 90
            assert s['photo_compress_days'] == 30 and s['photo_qar_max_px'] == 0

    def test_walidacja(self, app):
        with app.app_context():
            form = {k: str(v[0]) for k, v in photo_tools.SETTINGS.items()}
            assert photo_tools.save_settings(form) == []
            form['photo_kk_quality'] = '10'
            assert photo_tools.save_settings(form)
            form['photo_kk_quality'] = '75'
            form['photo_kk_max_px'] = '100'
            assert photo_tools.save_settings(form)
            form['photo_kk_max_px'] = 'abc'
            assert photo_tools.save_settings(form)

    def test_strona_tylko_superuser(self, client):
        login(client, 'oper', 'Oper1234!')
        assert client.get('/admin/zdjecia/').status_code == 403
        logout(client)
        login(client, 'admin', 'Admin1234!')
        assert client.get('/admin/zdjecia/').status_code == 403
        logout(client)
        login(client, 'super', 'Super1234!')
        resp = client.get('/admin/zdjecia/')
        assert resp.status_code == 200 and 'Zdjęcia i dysk' in resp.get_data(as_text=True)

    def test_zapis_przez_formularz(self, client):
        login(client, 'super', 'Super1234!')
        form = {k: str(v[0]) for k, v in photo_tools.SETTINGS.items()}
        form['photo_retention_days'] = '120'
        form['_csrf_token'] = _csrf(client)
        client.post('/admin/zdjecia/zapisz', data=form)
        with flask_app.app_context():
            assert photo_tools.get_settings()['photo_retention_days'] == 120


class TestZapisZdjecia:
    def test_upload_kk_zmniejsza(self, client):
        _, item_id = _new_item(client)
        resp = client.post(f'/api/item/{item_id}/photo',
                           data={'photo': (io.BytesIO(BIG), 'duze.jpg')},
                           headers={'X-CSRF-Token': _csrf(client)}, content_type='multipart/form-data')
        assert resp.status_code == 200
        with flask_app.app_context():
            photo = Photo.query.filter_by(report_item_id=item_id).first()
            path = os.path.join(_upload_dir(), photo.filename)
            assert os.path.getsize(path) < len(BIG) / 3
            assert max(Image.open(path).size) <= 1600
            assert photo.optimized_at is not None

    def test_upload_bez_zmniejszania_gdy_zero(self, client):
        with flask_app.app_context():
            AppSetting.set('photo_kk_max_px', 0)
            db.session.commit()
        _, item_id = _new_item(client)
        client.post(f'/api/item/{item_id}/photo', data={'photo': (io.BytesIO(BIG), 'duze.jpg')},
                    headers={'X-CSRF-Token': _csrf(client)}, content_type='multipart/form-data')
        with flask_app.app_context():
            photo = Photo.query.filter_by(report_item_id=item_id).first()
            assert os.path.getsize(os.path.join(_upload_dir(), photo.filename)) == len(BIG)
            assert photo.optimized_at is None

    def test_upload_qar_domyslnie_oryginal(self, client):
        login(client, 'admin', 'Admin1234!')
        with flask_app.app_context():
            qar_id = QARReport.query.first().id
        resp = client.post(f'/qar/{qar_id}/photo', data={'photo': (io.BytesIO(BIG), 'q.jpg')},
                           headers={'X-CSRF-Token': _csrf(client)}, content_type='multipart/form-data')
        assert resp.status_code == 200
        name = resp.get_json()['url'].rsplit('/', 1)[-1]
        path = os.path.join(flask_app.config['QAR_UPLOAD_FOLDER'], name)
        assert os.path.getsize(path) == len(BIG)

    def test_upload_qar_z_ustawieniem(self, client):
        with flask_app.app_context():
            AppSetting.set('photo_qar_max_px', 1200)
            db.session.commit()
        login(client, 'admin', 'Admin1234!')
        with flask_app.app_context():
            qar_id = QARReport.query.first().id
        resp = client.post(f'/qar/{qar_id}/photo', data={'photo': (io.BytesIO(BIG), 'q.jpg')},
                           headers={'X-CSRF-Token': _csrf(client)}, content_type='multipart/form-data')
        name = resp.get_json()['url'].rsplit('/', 1)[-1]
        assert max(Image.open(os.path.join(flask_app.config['QAR_UPLOAD_FOLDER'], name)).size) <= 1200


class TestKompresjaStarych:
    def test_zmniejsza_tylko_starsze(self, app):
        old_id = _old_photo('stare-kompresja.jpg', 45)
        new_id = _old_photo('nowe-kompresja.jpg', 5)
        with app.app_context():
            result = photo_tools.run_compress(_upload_dir())
            assert result['done'] == 1 and result['remaining'] == 0 and result['saved'] > 0
            old, new = db.session.get(Photo, old_id), db.session.get(Photo, new_id)
            assert old.optimized_at is not None and new.optimized_at is None
            assert os.path.getsize(os.path.join(_upload_dir(), 'stare-kompresja.jpg')) < len(BIG) / 3
            assert os.path.getsize(os.path.join(_upload_dir(), 'nowe-kompresja.jpg')) == len(BIG)

    def test_powtorne_uruchomienie_nic_nie_robi(self, app):
        _old_photo('powtorka.jpg', 45)
        with app.app_context():
            photo_tools.run_compress(_upload_dir())
            size = os.path.getsize(os.path.join(_upload_dir(), 'powtorka.jpg'))
            again = photo_tools.run_compress(_upload_dir())
            assert again['done'] == 0
            assert os.path.getsize(os.path.join(_upload_dir(), 'powtorka.jpg')) == size

    def test_brak_pliku_nie_zapetla(self, app):
        pid = _old_photo('zniknie.jpg', 45)
        os.remove(os.path.join(_upload_dir(), 'zniknie.jpg'))
        with app.app_context():
            result = photo_tools.run_compress(_upload_dir())
            assert result['remaining'] == 0 and db.session.get(Photo, pid).optimized_at is not None

    def test_wylaczone(self, app):
        with app.app_context():
            AppSetting.set('photo_compress_days', 0)
            db.session.commit()
            assert photo_tools.run_compress(_upload_dir()) is None

    def test_orientacja_exif_zachowana(self, app):
        img = Image.new('RGB', (2400, 1200), (200, 10, 10))
        exif = Image.Exif()
        exif[0x0112] = 6   # obrót o 90 stopni
        buf = io.BytesIO()
        img.save(buf, 'JPEG', exif=exif)
        _old_photo('exif.jpg', 45, data=buf.getvalue() + b'\0' * 1500000)
        with app.app_context():
            photo_tools.run_compress(_upload_dir())
            w, h = Image.open(os.path.join(_upload_dir(), 'exif.jpg')).size
            assert h > w   # po obróceniu wg EXIF zdjęcie jest pionowe


class TestRetencja:
    def test_usuwa_stare_z_zakonczonych(self, app):
        old_id = _old_photo('stare-ret.jpg', 100)
        recent_id = _old_photo('swieze-ret.jpg', 10)
        open_id = _old_photo('otwarty-ret.jpg', 100, status='in_progress')
        with app.app_context():
            result = photo_tools.run_purge(_upload_dir())
            assert result['done'] == 1 and result['freed'] > 0
            assert db.session.get(Photo, old_id) is None
            assert not os.path.exists(os.path.join(_upload_dir(), 'stare-ret.jpg'))
            assert db.session.get(Photo, recent_id) is not None
            assert db.session.get(Photo, open_id) is not None   # raport w toku zostaje
            assert AuditLog.query.filter_by(action='photos_purge').count() >= 1

    def test_wylaczona(self, app):
        with app.app_context():
            AppSetting.set('photo_retention_days', 0)
            db.session.commit()
            assert photo_tools.run_purge(_upload_dir()) is None

    def test_endpoint_wymaga_potwierdzenia(self, client):
        _old_photo('endpoint-ret.jpg', 100)
        login(client, 'super', 'Super1234!')
        token = _csrf(client)
        resp = client.post('/admin/zdjecia/uruchom/usuwanie', json={}, headers={'X-CSRF-Token': token})
        assert resp.status_code == 400
        resp = client.post('/admin/zdjecia/uruchom/usuwanie', json={'confirm': True},
                           headers={'X-CSRF-Token': token})
        assert resp.status_code == 200 and resp.get_json()['done'] == 1

    def test_endpoint_tylko_superuser(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.post('/admin/zdjecia/uruchom/kompresja', json={},
                           headers={'X-CSRF-Token': _csrf(client)})
        assert resp.status_code == 403

    def test_podglad(self, app):
        _old_photo('podglad-a.jpg', 45)
        _old_photo('podglad-b.jpg', 100)
        with app.app_context():
            info = photo_tools.preview(_upload_dir(), flask_app.config['QAR_UPLOAD_FOLDER'], flask_app.root_path)
            assert info['compress']['count'] == 2 and info['purge']['count'] == 1
            assert info['compress']['estimated_saving'] > 0
