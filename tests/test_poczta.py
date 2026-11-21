"""Testy poczty: ustawienia, wiadomość próbna i jedna wiadomość o ukończeniu serii kontroli.
Prawdziwy serwer SMTP nigdy nie jest wywoływany (`mailer._smtp_send` jest podmieniany)."""
import uuid
from datetime import datetime, UTC

import pytest

import mailer
from app import app as flask_app, db
from models import (AppSetting, AuditLog, ChecklistTemplate, Report, SentNotice, User)
from test_app import login, logout, _csrf


@pytest.fixture(autouse=True)
def _mail(app, monkeypatch):
    """Konto pocztowe jest udawane, a wysyłka zapisuje wiadomości do listy."""
    sent = []
    monkeypatch.setitem(flask_app.config, 'MAIL_USER', 'psh@example.test')
    monkeypatch.setitem(flask_app.config, 'MAIL_PASSWORD', 'x')
    monkeypatch.setitem(flask_app.config, 'MAIL_SMTP_HOST', 'smtp.example.test')
    monkeypatch.setitem(flask_app.config, 'MAIL_PORT', 587)
    monkeypatch.setattr(mailer, '_smtp_send', lambda msg, cfg: sent.append(msg))
    yield sent
    with flask_app.app_context():
        AppSetting.query.delete()
        SentNotice.query.delete()
        AuditLog.query.filter(AuditLog.action.in_(('mail_sent', 'mail_failed'))).delete(synchronize_session=False)
        Report.query.filter(Report.title.like('MAIL-%')).delete(synchronize_session=False)
        db.session.commit()


def _enable(recipients='kierownik@example.test', report_done=True, limit=30):
    with flask_app.app_context():
        AppSetting.set('mail_enabled', '1')
        AppSetting.set('mail_report_done', '1' if report_done else '0')
        AppSetting.set('mail_recipients', recipients)
        AppSetting.set('mail_hourly_limit', str(limit))
        db.session.commit()


def _series(n, status='in_progress', batch=True, product='PSH 600X800', client='Klient Testowy', zo='ZO-77'):
    """n list kontrolnych jednej serii; zwraca ich id."""
    with flask_app.app_context():
        user = User.query.filter_by(username='oper').first()
        tmpl = ChecklistTemplate.query.filter_by(name='Test szablon').first()
        bid = uuid.uuid4().hex if batch else None
        ids = []
        for i in range(n):
            r = Report(user_id=user.id, template_id=tmpl.id, title=f'MAIL-{product}', status=status,
                       report_type='kontroler', batch_id=bid, batch_index=i + 1 if batch else None,
                       batch_total=n if batch else None, qr_key=f'{product}|{client}|{zo}'.upper())
            db.session.add(r)
            db.session.flush()
            ids.append(r.id)
        db.session.commit()
        return ids


def _complete(report_id):
    with flask_app.test_request_context('/', base_url='https://profit.example.test'):
        r = db.session.get(Report, report_id)
        r.status = 'completed'
        r.completed_at = datetime.now(UTC).replace(tzinfo=None)
        db.session.commit()
        return mailer.notify_report_done(r)


class TestUstawienia:
    def test_parse_adresow(self):
        assert mailer.parse_recipients('a@b.pl, c@d.pl;\n a@b.pl, zly, e@f') == ['a@b.pl', 'c@d.pl']

    def test_zapis_i_walidacja(self, app):
        with app.app_context():
            ok = {'mail_enabled': '1', 'mail_recipients': 'a@b.pl, c@d.pl', 'mail_hourly_limit': '10'}
            assert mailer.save_settings(ok) == []
            s = mailer.get_settings()
            assert s['enabled'] and not s['report_done'] and s['recipients'] == ['a@b.pl', 'c@d.pl']
            assert mailer.save_settings({**ok, 'mail_recipients': 'to nie adres'})
            assert mailer.save_settings({**ok, 'mail_hourly_limit': '0'})
            assert mailer.save_settings({**ok, 'mail_recipients': ', '.join(f'u{i}@x.pl' for i in range(11))})

    def test_strona_tylko_superuser(self, client):
        login(client, 'admin', 'Admin1234!')
        assert client.get('/admin/poczta/').status_code == 403
        logout(client)
        login(client, 'super', 'Super1234!')
        resp = client.get('/admin/poczta/')
        assert resp.status_code == 200 and 'Poczta' in resp.get_data(as_text=True)
        assert 'x</' not in resp.get_data(as_text=True)   # hasła nie ma na stronie

    def test_wiadomosc_probna(self, client, _mail):
        login(client, 'super', 'Super1234!')
        resp = client.post('/admin/poczta/probna', data={'to': 'ja@example.test', '_csrf_token': _csrf(client)})
        assert resp.status_code == 302
        assert len(_mail) == 1 and _mail[0]['To'] == 'ja@example.test'
        assert _mail[0]['From'].endswith('<psh@example.test>')

    def test_probna_zly_adres(self, client, _mail):
        login(client, 'super', 'Super1234!')
        client.post('/admin/poczta/probna', data={'to': 'abc', '_csrf_token': _csrf(client)})
        assert _mail == []


class TestUkonczenieSerii:
    def test_jedna_wiadomosc_na_cala_serie(self, _mail):
        _enable()
        ids = _series(3)
        assert _complete(ids[0]) is False and _complete(ids[1]) is False
        assert _mail == []
        assert _complete(ids[2]) is True
        assert len(_mail) == 1
        msg = _mail[0]
        assert msg['To'] == 'kierownik@example.test'
        assert msg['Subject'] == 'Ukończono kontrolę: KLIENT TESTOWY, ZO ZO-77, PSH 600X800 (3 szt.)'
        body = msg.get_content()
        for part in ('Klient: KLIENT TESTOWY', 'Numer ZO: ZO-77', 'Produkt: PSH 600X800', 'Ilość: 3 szt.',
                     'Kontroler: oper', 'https://profit.example.test/reports/'):
            assert part in body

    def test_nie_wysyla_drugi_raz(self, _mail):
        _enable()
        ids = _series(2)
        _complete(ids[0])
        assert _complete(ids[1]) is True
        assert _complete(ids[1]) is False
        assert len(_mail) == 1

    def test_pojedyncza_lista_bez_serii(self, _mail):
        _enable()
        (rid,) = _series(1, batch=False)
        assert _complete(rid) is True
        assert 'Ilość: 1 szt.' in _mail[0].get_content()

    def test_wylaczone_nic_nie_wysyla(self, _mail):
        ids = _series(1, batch=False)
        assert _complete(ids[0]) is False   # domyślnie wyłączone
        _enable(report_done=False)
        ids = _series(1, batch=False)
        assert _complete(ids[0]) is False
        _enable(recipients='')
        ids = _series(1, batch=False)
        assert _complete(ids[0]) is False
        assert _mail == []

    def test_limit_na_godzine(self, _mail):
        _enable(limit=1)
        for _ in range(2):
            (rid,) = _series(1, batch=False)
            _complete(rid)
        assert len(_mail) == 1
        with flask_app.app_context():
            assert AuditLog.query.filter_by(action='mail_failed').count() == 1

    def test_blad_smtp_nie_psuje_zamkniecia(self, monkeypatch, _mail):
        def boom(msg, cfg):
            raise OSError('serwer niedostępny')
        monkeypatch.setattr(mailer, '_smtp_send', boom)
        _enable()
        (rid,) = _series(1, batch=False)
        assert _complete(rid) is False   # bez wyjątku
        with flask_app.app_context():
            log = AuditLog.query.filter_by(action='mail_failed').first()
            assert log and 'serwer niedostępny' in log.detail and 'x' != log.detail

    def test_przez_zamkniecie_listy_w_aplikacji(self, client, _mail):
        _enable()
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        (rid,) = _series(1, batch=False)
        resp = client.post(f'/checklist/{rid}/complete', data={'_csrf_token': token})
        assert resp.status_code in (200, 302)
        assert len(_mail) == 1
        assert 'ZO ZO-77' in _mail[0]['Subject']

    def test_bez_konta_w_env(self, monkeypatch, _mail):
        monkeypatch.setitem(flask_app.config, 'MAIL_PASSWORD', '')
        _enable()
        (rid,) = _series(1, batch=False)
        assert _complete(rid) is False and _mail == []
