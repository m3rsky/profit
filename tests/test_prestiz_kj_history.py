"""Testy modułu KJ Prestiż, etap 5: dane historyczne do raportów (11 zleceń z 4 tygodni z kontrolami, zawrotami,
złomami, przerwanymi kontrolami i zdjęciami), deterministyczność, spójność i bezpieczeństwo resetu."""
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from app import app as flask_app, db
from prestiz_kj import clock, demo_data, demo_history, orders as ord_, service
from prestiz_kj.models import (PkjEvent, PkjInspection, PkjInspectionMaterial, PkjOrder, PkjOrderMaterial,
                               PkjOrderOperation, PkjPhoto, PkjResult, PkjReturn, PkjReturnOperation)
from test_app import login, _csrf


@pytest.fixture(autouse=True)
def _clean(app, tmp_path):
    flask_app.config['PKJ_UPLOAD_FOLDER'] = str(tmp_path / 'pkj')
    with app.app_context():
        service.reset_all()
    yield
    with app.app_context():
        service.reset_all()
        clock.clear()


def _seed(app, orders=False):
    with app.app_context():
        demo_data.seed_configuration()
        if orders:
            demo_data.seed_orders()
        assert demo_history.seed_history()


def _naive_utc(dt):
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _history_orders():
    return PkjOrder.query.filter(PkjOrder.number.like('ZP-DEMO-1%'), PkjOrder.type == 'ZP').order_by(PkjOrder.number)


def _fingerprint():
    first = PkjInspectionMaterial.query.join(PkjInspection).filter(PkjInspection.round == 1).all()
    return (PkjOrder.query.count(), PkjInspection.query.count(), PkjResult.query.count(), PkjPhoto.query.count(),
            sorted(Counter(r.reason_name for r in PkjReturn.query.all()).items()),
            sorted(Counter(r.decision for r in PkjReturn.query.all()).items()),
            sorted(Counter(m.verdict for m in first).items()),
            sorted(Counter(i.round for i in PkjInspection.query.all()).items()))


class TestGenerator:
    def test_requires_configuration_and_is_idempotent(self, app):
        with app.app_context():
            assert demo_history.seed_history() is False             # bez konfiguracji nic nie powstaje
            assert PkjOrder.query.count() == 0
        _seed(app)
        with app.app_context():
            count = PkjOrder.query.count()
            assert demo_history.seed_history() is False
            assert PkjOrder.query.count() == count

    def test_eleven_historical_orders(self, app):
        _seed(app)
        with app.app_context():
            numbers = [o.number for o in _history_orders()]
            assert numbers == [f'ZP-DEMO-1{i:02d}' for i in range(1, 12)]
            assert all(o.is_demo and not o.is_demo_scenario for o in _history_orders())
            assert Counter(o.status for o in _history_orders()) == {'zakonczone': 8, 'do_kontroli': 3}

    def test_deterministic(self, app):
        _seed(app)
        with app.app_context():
            first = _fingerprint()
            service.reset_orders()
            assert PkjOrder.query.count() == 0
            assert demo_history.seed_history()
            assert _fingerprint() == first

    def test_distribution_suits_reports(self, app):
        _seed(app)
        with app.app_context():
            first = PkjInspectionMaterial.query.join(PkjInspection).filter(PkjInspection.round == 1).all()
            ok = sum(1 for m in first if m.verdict == 'OK')
            assert 0.70 <= ok / len(first) <= 0.90                      # "dobrze za pierwszym razem" ok. 4 na 5
            reasons = Counter(r.reason_name for r in PkjReturn.query.all())
            (top, top_n), (_, second_n) = reasons.most_common(2)
            assert top == 'Spoina niepełna' and top_n >= 1.5 * second_n   # wyraźny lider Pareto
            assert len(reasons) >= 5
            assert 15 <= PkjReturn.query.count() <= 40
            decisions = Counter(r.decision for r in PkjReturn.query.all())
            assert decisions['zlom'] == 2 and decisions['poprawka'] >= 15
            assert PkjInspection.query.filter_by(status='przerwana').count() == 2
            assert PkjInspection.query.filter(PkjInspection.round >= 3).count() >= 1     # powtórna niezgodność

    def test_orders_in_progress_cover_every_state(self, app):
        _seed(app)
        with app.app_context():
            assert PkjReturn.query.filter_by(status='w_naprawie').count() == 1
            assert PkjReturn.query.filter_by(status='do_ponownej_kontroli').count() == 1
            assert PkjOrder.query.filter_by(type='ZN', status='zamkniete').count() >= 10
            late = PkjOrder.query.filter_by(number='ZP-DEMO-109').one()
            assert [(o.operation_name, o.status) for o in ord_.operations_of(late.id)][-1] == ('Składanie', 'do_kontroli')
            assert PkjOrderMaterial.query.filter_by(order_id=late.id, rw_status='wstrzymane').count() == 3
            waiting = PkjOrderMaterial.query.filter_by(element_status='do_ponownej_kontroli').count()
            repairing = PkjOrderMaterial.query.filter_by(element_status='w_naprawie').count()
            assert waiting == 1 and repairing == 1

    def test_dates_are_in_the_past_four_weeks_and_ordered(self, app):
        _seed(app)
        with app.app_context():
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            lo = now - timedelta(days=29)
            stamps = [_naive_utc(e.ts) for e in PkjEvent.query.filter(PkjEvent.actor.isnot(None)).all()]
            hist = [_naive_utc(o.created_at) for o in _history_orders()]
            assert max(hist) <= now and min(hist) >= lo
            assert all(t <= now for t in stamps)
            assert (now - min(hist)).days >= 20 and (now - max(hist)).days <= 4
            for insp in PkjInspection.query.all():
                assert _naive_utc(insp.started_at) <= now
                if insp.finished_at:
                    assert _naive_utc(insp.started_at) <= _naive_utc(insp.finished_at) <= now
            for o in _history_orders():
                events = PkjEvent.query.filter_by(order_id=o.id).order_by(PkjEvent.id).all()
                times = [_naive_utc(e.ts) for e in events]
                assert times == sorted(times)                           # oś czasu rośnie
            assert abs((clock.now() - datetime.now(timezone.utc)).total_seconds()) < 5   # zegar wrócił do normy

    def test_work_happens_in_local_work_hours_on_weekdays(self, app):
        from zoneinfo import ZoneInfo
        _seed(app)
        with app.app_context():
            ids = {o.id for o in _history_orders()}
            checked = 0
            for e in PkjEvent.query.filter(PkjEvent.type.in_(['meldunek', 'kontrola_start'])).all():
                if e.order_id not in ids:
                    continue
                local = _naive_utc(e.ts).replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Europe/Warsaw'))
                now_local = datetime.now(timezone.utc).astimezone(ZoneInfo('Europe/Warsaw'))
                if (now_local - local).total_seconds() < 6 * 3600:
                    continue                                              # zdarzenia przycięte do "teraz"
                assert local.weekday() < 5 and 6 <= local.hour < 18, local
                checked += 1
            assert checked > 100

    def test_consistency_of_states(self, app):
        _seed(app)
        with app.app_context():
            for m in PkjOrderMaterial.query.all():
                assert (m.rw_status == 'wydane') == (m.element_status == 'zgodny') or m.element_status in (
                    'w_kontroli', 'zawrocony', 'w_naprawie', 'do_ponownej_kontroli', 'zlom', 'oczekuje')
            for r in PkjReturn.query.all():
                assert r.repair_order_id and PkjReturnOperation.query.filter_by(return_id=r.id).count() >= 1
                zn = db.session.get(PkjOrder, r.repair_order_id)
                assert zn.type == 'ZN' and zn.source_return_id == r.id and zn.parent_id
            for insp in PkjInspection.query.filter_by(status='zakonczona').all():
                results = PkjResult.query.filter_by(inspection_id=insp.id).all()
                assert all(x.result for x in results)                   # żaden punkt nie jest nieoceniony
                for x in results:
                    if x.result == 'NOK' and x.photo_required_on_nok:
                        assert PkjPhoto.query.filter_by(result_id=x.id).count() >= 1
                    if x.kind == 'value' and x.value is not None:
                        assert x.out_of_range == (x.value < x.min_value or x.value > x.max_value)
            scrapped = PkjReturn.query.filter_by(decision='zlom').all()
            assert len(scrapped) == 2 and all(
                PkjEvent.query.filter_by(type='rozchod', ref_id=r.id).count() == 1 for r in scrapped)
            closed_ok = [o for o in PkjOrder.query.filter_by(type='ZN', status='zamkniete').all()]
            assert closed_ok

    def test_photos_exist_on_disk_and_are_removed_with_orders(self, app):
        _seed(app)
        with app.app_context():
            folder = service.upload_folder()
            files = [p.filename for p in PkjPhoto.query.all()]
            assert len(files) >= 10 and all(os.path.isfile(os.path.join(folder, f)) for f in files)
            assert len(set(files)) == len(files)                         # osobny plik dla każdego zdjęcia
            service.reset_orders()
            assert not any(os.path.exists(os.path.join(folder, f)) for f in files)

    def test_approval_setting_is_preserved(self, app):
        with app.app_context():
            demo_data.seed_configuration()
            service.set_setting('approval_mode', 'manager')
            db.session.commit()
            assert demo_history.seed_history()
            assert service.get_setting('approval_mode') == 'manager'
            assert PkjReturn.query.filter_by(status='do_zatwierdzenia').count() == 0

    def test_deleting_one_historical_order_keeps_the_rest(self, app):
        _seed(app)
        with app.app_context():
            before = PkjOrder.query.count()
            target = PkjOrder.query.filter_by(number='ZP-DEMO-101').one()
            zns = PkjOrder.query.filter_by(parent_id=target.id).count()
            assert ord_.delete_orders([target.id]) == 1 + zns
            assert PkjOrder.query.count() == before - 1 - zns
            assert PkjOrder.query.filter_by(number='ZP-DEMO-102').count() == 1


class TestScreensWithHistory:
    def test_load_button_creates_everything(self, client, app):
        login(client, 'admin', 'Admin1234!')
        resp = client.post('/kj-prestiz/demo/wczytaj', data={'_csrf_token': _csrf(client)}, follow_redirects=True)
        html = resp.get_data(as_text=True)
        assert 'dane historyczne z 4 tygodni' in html
        again = client.post('/kj-prestiz/demo/wczytaj', data={'_csrf_token': _csrf(client)}, follow_redirects=True)
        assert 'już istnieją' in again.get_data(as_text=True)
        with app.app_context():
            assert PkjOrder.query.filter_by(type='ZP').count() == 15       # scenariusz + 3 bieżące + 11 historycznych

    def test_lists_and_pages_render_with_history(self, client, app):
        _seed(app, orders=True)
        login(client, 'admin', 'Admin1234!')
        with app.app_context():
            hist = PkjOrder.query.filter_by(number='ZP-DEMO-103').one().id
            ret = PkjReturn.query.order_by(PkjReturn.id).first().id
            insp = PkjInspection.query.filter_by(order_id=hist).first().id
        assert 'ZP-DEMO-103' in client.get('/kj-prestiz/zlecenia?q=DEMO-103').get_data(as_text=True)
        page = client.get(f'/kj-prestiz/zlecenia/{hist}').get_data(as_text=True)
        assert 'Kontrole KJ' in page and 'Oś czasu' in page
        zl = client.get('/kj-prestiz/zawroty').get_data(as_text=True)
        assert 'Spoina niepełna' in zl and 'Następna' in zl                 # ponad 20 zawrotów, jest stronicowanie
        assert client.get(f'/kj-prestiz/zawroty/{ret}').status_code == 200
        assert client.get(f'/kj-prestiz/m/kontrola/{insp}').status_code == 200
        assert client.get(f'/kj-prestiz/m/historia/{hist}').status_code == 200
        assert client.get('/kj-prestiz/zlecenia?s=zamkniete').status_code == 200
