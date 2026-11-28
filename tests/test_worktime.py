"""Czas pracy nad listą: odcinki pracy liczone tylko, gdy użytkownik jest przy liście."""
from datetime import timedelta

import pytest

import worktime
from app import app as flask_app, db
from models import AppSetting, ChecklistSession, ChecklistTemplate, Report, ReportItem, User
from test_app import login, logout, _csrf

API_HEADERS = {'X-API-Key': 'test-api-key-12345'}


@pytest.fixture(autouse=True)
def _clean(app):
    yield
    with flask_app.app_context():
        ids = [r.id for r in Report.query.filter(Report.title.like('CZAS-%')).all()]
        if ids:
            ChecklistSession.query.filter(ChecklistSession.report_id.in_(ids)).delete(synchronize_session=False)
            ReportItem.query.filter(ReportItem.report_id.in_(ids)).delete(synchronize_session=False)
            Report.query.filter(Report.id.in_(ids)).delete(synchronize_session=False)
        AppSetting.query.delete()
        db.session.commit()
    worktime._last_sweep = 0.0


def _report(title='CZAS-1'):
    """Lista kontrolna użytkownika 'oper' z jednym punktem; zwraca (id raportu, id punktu)."""
    with flask_app.app_context():
        user = User.query.filter_by(username='oper').first()
        tmpl = ChecklistTemplate.query.filter_by(name='Test szablon').first()
        r = Report(user_id=user.id, template_id=tmpl.id, title=title, status='in_progress', report_type='kontroler')
        db.session.add(r)
        db.session.flush()
        task = tmpl.categories[0].tasks.first() if hasattr(tmpl.categories[0].tasks, 'first') else tmpl.categories[0].tasks[0]
        item = ReportItem(report_id=r.id, task_id=task.id)
        db.session.add(item)
        db.session.commit()
        return r.id, item.id


def _rate_all(rid):
    """Ocenia wszystkie punkty (lista z brakami nie daje się zamknąć); nie dotyka odcinków czasu."""
    with flask_app.app_context():
        for item in ReportItem.query.filter_by(report_id=rid).all():
            item.result = 'ok'
        db.session.commit()


def _sessions(rid):
    with flask_app.app_context():
        return [(s.started_at, s.ended_at, s.last_activity_at)
                for s in ChecklistSession.query.filter_by(report_id=rid).order_by(ChecklistSession.id).all()]


def _age_open_session(rid, hours=0, minutes=0):
    """Cofa początek i ostatnią aktywność otwartego odcinka o podany czas (symulacja bezczynności)."""
    delta = timedelta(hours=hours, minutes=minutes)
    with flask_app.app_context():
        for s in ChecklistSession.query.filter_by(report_id=rid, ended_at=None).all():
            s.started_at = s.started_at - delta
            s.last_activity_at = s.last_activity_at - delta
        r = db.session.get(Report, rid)
        r.locked_at = r.locked_at - delta if r.locked_at else None
        db.session.commit()


def _open(client, rid):
    login(client, 'oper', 'Oper1234!')
    resp = client.get(f'/checklist/{rid}')
    assert resp.status_code == 200
    return resp


def _post(client, url, token=None, **kw):
    return client.post(url, headers={'X-CSRF-Token': token or _csrf(client)}, **kw)


class TestOdcinki:
    def test_wejscie_otwiera_odcinek(self, client):
        rid, _ = _report()
        _open(client, rid)
        (s,) = _sessions(rid)
        assert s[1] is None and s[2] is not None

    def test_ponowne_wejscie_nie_zostawia_dwoch_otwartych(self, client):
        rid, _ = _report()
        _open(client, rid)
        client.get(f'/checklist/{rid}')
        rows = _sessions(rid)
        assert len(rows) == 2 and sum(1 for r in rows if r[1] is None) == 1

    def test_20_godzin_bezczynnosci_nie_liczy_sie(self, client):
        """Scenariusz z produkcji: lista otwarta, urządzenie nieużywane, zamknięcie po wielu godzinach."""
        rid, _ = _report()
        _open(client, rid)
        _age_open_session(rid, hours=20)
        _rate_all(rid)
        _post(client, f'/checklist/{rid}/complete')
        with flask_app.app_context():
            r = db.session.get(Report, rid)
            assert r.status == 'completed'
            assert r.duration_seconds is not None and r.duration_seconds <= 125   # tylko zapas, nie 20 godzin

    def test_krotka_praca_liczy_sie_w_calosci(self, client):
        rid, _ = _report()
        _open(client, rid)
        _age_open_session(rid, minutes=7)            # 7 minut temu wszedł, od tego czasu pracuje
        with flask_app.app_context():
            s = ChecklistSession.query.filter_by(report_id=rid).first()
            s.last_activity_at = worktime.now() - timedelta(minutes=1)
            db.session.commit()
        _rate_all(rid)
        _post(client, f'/checklist/{rid}/complete')
        with flask_app.app_context():
            assert 6 * 60 <= db.session.get(Report, rid).duration_seconds <= 8 * 60

    def test_zapis_punktu_odswieza_aktywnosc(self, client):
        rid, item = _report()
        _open(client, rid)
        _age_open_session(rid, minutes=30)
        before = _sessions(rid)[0][2]
        resp = _post(client, f'/api/item/{item}/result', json={'result': 'ok'})
        assert resp.status_code == 200
        assert _sessions(rid)[0][2] > before + timedelta(minutes=25)

    def test_zapis_po_zamknieciu_odcinka_otwiera_nowy(self, client):
        rid, item = _report()
        _open(client, rid)
        _age_open_session(rid, hours=3)
        with flask_app.app_context():
            assert worktime.sweep(force=True) == 1
        assert all(r[1] is not None for r in _sessions(rid))
        _post(client, f'/api/item/{item}/result', json={'result': 'ok'})
        assert sum(1 for r in _sessions(rid) if r[1] is None) == 1

    def test_nakladajace_sie_odcinki_sa_scalone(self, app):
        rid, _ = _report()
        base = worktime.now() - timedelta(hours=3)
        with flask_app.app_context():
            for start, end in ((0, 60), (30, 90), (200, 230)):     # minuty od `base`; pierwsze dwa nachodzą na siebie
                db.session.add(ChecklistSession(report_id=rid, started_at=base + timedelta(minutes=start),
                                                ended_at=base + timedelta(minutes=end),
                                                last_activity_at=base + timedelta(minutes=end)))
            db.session.commit()
            assert worktime.total_seconds(rid) == (90 + 30) * 60


class TestWyjscieIPowrot:
    def test_pauza_i_wznowienie_sumuja_odcinki(self, client):
        rid, _ = _report()
        _open(client, rid)
        assert _post(client, f'/api/report/{rid}/pause').status_code == 200
        assert all(r[1] is not None for r in _sessions(rid))
        assert _post(client, f'/api/report/{rid}/resume').status_code == 200
        rows = _sessions(rid)
        assert len(rows) == 2 and rows[1][1] is None

    def test_wznowienie_nie_dubluje_otwartego_odcinka(self, client):
        rid, _ = _report()
        _open(client, rid)
        _post(client, f'/api/report/{rid}/resume')
        assert len(_sessions(rid)) == 1

    def test_unlock_przy_wyjsciu_zamyka_odcinek(self, client):
        rid, _ = _report()
        _open(client, rid)
        _post(client, f'/api/report/{rid}/unlock')
        assert _sessions(rid)[0][1] is not None

    def test_wznowienie_gdy_lista_zajeta_przez_innego(self, client):
        rid, _ = _report()
        with flask_app.app_context():
            other = User.query.filter_by(username='admin').first()
            r = db.session.get(Report, rid)
            r.locked_by_id, r.locked_at = other.id, worktime.now()
            db.session.commit()
        login(client, 'oper', 'Oper1234!')
        assert _post(client, f'/api/report/{rid}/resume').status_code == 423

    def test_wznowienie_zamknietej_listy(self, client):
        rid, _ = _report()
        with flask_app.app_context():
            db.session.get(Report, rid).status = 'completed'
            db.session.commit()
        login(client, 'oper', 'Oper1234!')
        assert _post(client, f'/api/report/{rid}/resume').status_code == 409


class TestBezczynnosc:
    def test_sprzatanie_zamyka_wg_progu(self, client):
        rid, _ = _report()
        _open(client, rid)
        _age_open_session(rid, minutes=90)
        with flask_app.app_context():
            assert worktime.sweep(force=True) == 1
        s = _sessions(rid)[0]
        assert s[1] is not None and s[1] - s[2] == worktime.GRACE

    def test_sprzatanie_nie_zamyka_przed_progiem(self, client):
        rid, _ = _report()
        _open(client, rid)
        _age_open_session(rid, minutes=30)
        with flask_app.app_context():
            assert worktime.sweep(force=True) == 0
        assert _sessions(rid)[0][1] is None

    def test_prog_ustawialny(self, client):
        rid, _ = _report()
        _open(client, rid)
        _age_open_session(rid, minutes=90)
        with flask_app.app_context():
            AppSetting.set('work_idle_minutes', 120)
            db.session.commit()
            assert worktime.sweep(force=True) == 0

    def test_wylogowanie_zamyka_odcinek(self, client):
        rid, _ = _report()
        _open(client, rid)
        client.get('/logout')
        assert _sessions(rid)[0][1] is not None

    def test_wygasniecie_sesji_logowania_zamyka_odcinek(self, client):
        rid, _ = _report()
        _open(client, rid)
        _age_open_session(rid, hours=3)
        with client.session_transaction() as sess:
            sess['_last_activity'] = (worktime.now() - timedelta(hours=2)).isoformat() + '+00:00'
        resp = client.get('/checklist/' + str(rid))
        assert resp.status_code == 302 and '/login' in resp.headers['Location']
        s = _sessions(rid)[0]
        assert s[1] is not None and s[1] - s[2] == worktime.GRACE        # bez 3 godzin bezczynności


class TestSygnalyWTle:
    @staticmethod
    def _set_activity(client, minutes_ago):
        stamp = (worktime.now() - timedelta(minutes=minutes_ago)).isoformat() + '+00:00'
        with client.session_transaction() as sess:
            sess['_last_activity'] = stamp
        return stamp

    @staticmethod
    def _get_activity(client):
        with client.session_transaction() as sess:
            return sess.get('_last_activity')

    def test_nieaktywny_heartbeat_nie_podtrzymuje_sesji(self, client):
        rid, _ = _report()
        _open(client, rid)
        token = _csrf(client)
        stamp = self._set_activity(client, 30)
        resp = _post(client, f'/api/report/{rid}/heartbeat', token=token, json={'active': False})
        assert resp.status_code == 200 and resp.get_json()['open'] is True
        assert self._get_activity(client) == stamp

    def test_aktywny_heartbeat_podtrzymuje_sesje_i_odcinek(self, client):
        rid, _ = _report()
        _open(client, rid)
        _age_open_session(rid, minutes=20)
        token = _csrf(client)
        stamp = self._set_activity(client, 30)
        _post(client, f'/api/report/{rid}/heartbeat', token=token, json={'active': True})
        assert self._get_activity(client) > stamp
        assert _sessions(rid)[0][2] > worktime.now() - timedelta(minutes=1)

    def test_odpytywanie_alertow_nie_podtrzymuje_sesji(self, client):
        _open(client, _report()[0])
        stamp = self._set_activity(client, 30)
        assert client.get('/alerts/poll').status_code == 200
        assert self._get_activity(client) == stamp

    def test_heartbeat_po_zamknieciu_odcinka_zglasza_brak(self, client):
        rid, _ = _report()
        _open(client, rid)
        _age_open_session(rid, hours=2)
        with flask_app.app_context():
            worktime.sweep(force=True)
        resp = _post(client, f'/api/report/{rid}/heartbeat', json={'active': False})
        assert resp.get_json()['open'] is False

    def test_heartbeat_po_wygasnieciu_zwraca_401(self, client):
        rid, _ = _report()
        _open(client, rid)
        token = _csrf(client)
        self._set_activity(client, 120)
        assert _post(client, f'/api/report/{rid}/heartbeat', token=token, json={'active': True}).status_code == 401


class TestInneSciezkiZamykania:
    def test_api_v1_zamkniecie(self, client):
        rid, _ = _report()
        with flask_app.app_context():
            r = db.session.get(Report, rid)
            worktime.open_session(r, worktime.now() - timedelta(hours=10))
            db.session.commit()
        resp = client.post(f'/api/v1/checklists/{rid}/complete', headers=API_HEADERS)
        assert resp.status_code == 200
        assert resp.get_json()['duration_seconds'] <= 125

    def test_aplikacja_desktop_z_odcinkami(self, client):
        rid, _ = _report()
        _open(client, rid)
        _age_open_session(rid, hours=8)
        assert _post(client, f'/api/desktop/reports/{rid}/complete').status_code == 200
        with flask_app.app_context():
            assert db.session.get(Report, rid).duration_seconds <= 125

    def test_aplikacja_desktop_bez_odcinkow_dawny_wzor(self, client):
        rid, _ = _report()
        login(client, 'oper', 'Oper1234!')
        assert _post(client, f'/api/desktop/reports/{rid}/complete').status_code == 200
        with flask_app.app_context():
            assert db.session.get(Report, rid).duration_seconds is not None

    def test_wznowienie_raportu_zaczyna_od_zera(self, client):
        rid, _ = _report()
        _open(client, rid)
        _rate_all(rid)
        _post(client, f'/checklist/{rid}/complete')
        with flask_app.app_context():
            assert db.session.get(Report, rid).status == 'completed'
        logout(client)
        login(client, 'super', 'Super1234!')
        _post(client, f'/reports/{rid}/reopen')
        rows = _sessions(rid)
        assert len(rows) == 1 and rows[0][1] is None


class TestUstawienie:
    def test_strona_tylko_superuser(self, client):
        login(client, 'admin', 'Admin1234!')
        assert client.get('/admin/czas-pracy/').status_code == 403
        logout(client)
        login(client, 'super', 'Super1234!')
        resp = client.get('/admin/czas-pracy/')
        assert resp.status_code == 200 and 'Czas pracy' in resp.get_data(as_text=True)

    def test_zapis_i_walidacja(self, client):
        login(client, 'super', 'Super1234!')
        _post(client, '/admin/czas-pracy/zapisz', data={'work_idle_minutes': '45', '_csrf_token': _csrf(client)})
        with flask_app.app_context():
            assert worktime.idle_minutes() == 45
        for bad in ('1', '9999', 'abc', ''):
            _post(client, '/admin/czas-pracy/zapisz', data={'work_idle_minutes': bad, '_csrf_token': _csrf(client)})
        with flask_app.app_context():
            assert worktime.idle_minutes() == 45

    def test_domyslny_prog_to_godzina(self, app):
        with flask_app.app_context():
            assert worktime.idle_minutes() == 60
