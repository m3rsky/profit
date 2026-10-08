"""Testy wyszukiwania istniejących list kontrolnych po kodzie QR."""
from datetime import datetime
from app import db
from models import Report
from test_app import login, _csrf


def _post(client, url, **kw):
    payload = {'p': 'Test szablon QR', 'c': 'Klient QR', 'o': 'ZO-QR-1', 'q': 1}
    payload.update(kw)
    return client.post(url, json=payload, headers={'X-CSRF-Token': _csrf(client)})


class TestQrLookup:
    def test_lookup_empty_then_found(self, client):
        login(client, 'oper', 'Oper1234!')
        assert _post(client, '/checklist/qr-lookup').get_json()['reports'] == []
        created = _post(client, '/checklist/from-qr').get_json()
        assert created['ok'] and not created.get('existing')
        found = _post(client, '/checklist/qr-lookup').get_json()['reports']
        assert len(found) == 1
        assert found[0]['action'] == 'Rozpocznij'
        assert found[0]['url'] == created['redirect']

    def test_second_scan_opens_existing(self, client, app):
        login(client, 'oper', 'Oper1234!')
        first = _post(client, '/checklist/from-qr', o='ZO-QR-2', q=2).get_json()
        again = _post(client, '/checklist/from-qr', o='ZO-QR-2', q=2).get_json()
        assert again['existing'] is True
        assert again['redirect'] == first['redirect']
        with app.app_context():
            assert Report.query.filter(Report.qr_key.like('%ZO-QR-2')).count() == 2

    def test_force_creates_new(self, client, app):
        login(client, 'oper', 'Oper1234!')
        _post(client, '/checklist/from-qr', o='ZO-QR-3')
        with app.app_context():
            # omiń 10 s zabezpieczenie przed podwójnym submitem
            Report.query.filter(Report.qr_key.like('%ZO-QR-3')).update(
                {'created_at': datetime(2026, 1, 1)})
            db.session.commit()
        forced = _post(client, '/checklist/from-qr', o='ZO-QR-3', force=True).get_json()
        assert forced['ok'] and not forced.get('existing')
        with app.app_context():
            assert Report.query.filter(Report.qr_key.like('%ZO-QR-3')).count() == 2

    def test_key_is_case_and_space_insensitive(self, client):
        login(client, 'oper', 'Oper1234!')
        _post(client, '/checklist/from-qr', o='ZO-QR-4')
        found = _post(client, '/checklist/qr-lookup', o=' zo-qr-4 ',
                      p='test  SZABLON qr').get_json()['reports']
        assert len(found) == 1

    def test_without_order_number_no_lookup(self, client):
        login(client, 'oper', 'Oper1234!')
        _post(client, '/checklist/from-qr', o='')
        assert _post(client, '/checklist/qr-lookup', o='').get_json()['reports'] == []

    def test_legacy_report_without_key_is_found(self, client, app):
        login(client, 'oper', 'Oper1234!')
        with app.app_context():
            r = Report(user_id=1, title='Test szablon QR – Klient QR – ZO-QR-5 – 01.10.2026 12:30',
                       report_type='kontroler')
            db.session.add(r)
            db.session.commit()
        found = _post(client, '/checklist/qr-lookup', o='ZO-QR-5').get_json()['reports']
        assert len(found) == 1
