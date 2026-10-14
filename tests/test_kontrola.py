"""Testy modulu Kontrola zamowien (stale punkty kontrolne, operator, kontrola sprawdzajaca)."""
import pytest

from app import db
from models import (User, Alert, QARReport, ControlPoint, ControlRecord, ControlResult)
from test_app import login, _csrf

QR = {'p': 'Szafa PSH', 'c': 'Klient X', 'o': 'ZO/100/2026', 'q': 3}


@pytest.fixture
def operator_user(app):
    with app.app_context():
        if not User.query.filter_by(username='operator1').first():
            u = User(username='operator1', email='operator1@test.pl', role='operator')
            u.set_password('Operator123!')
            db.session.add(u)
            db.session.commit()
    return 'operator1'


@pytest.fixture(autouse=True)
def _clean(app):
    with app.app_context():
        for m in (ControlResult, ControlRecord, ControlPoint):
            m.query.delete()
        db.session.add_all([ControlPoint(title='Wymiary', order=1),
                            ControlPoint(title='Malowanie', order=2),
                            ControlPoint(title='Wylaczony', order=3, is_active=False)])
        db.session.commit()
    yield
    with app.app_context():
        for m in (ControlResult, ControlRecord, ControlPoint):
            m.query.delete()
        Alert.query.filter(Alert.message.like('ZO ZO/100%')).delete(synchronize_session=False)
        QARReport.query.filter(QARReport.zo_number == 'ZO/100/2026').delete()
        db.session.commit()


def _scan(client, qr=QR):
    token = _csrf(client)
    return client.post('/kontrola/from-qr', json=qr, headers={'X-CSRF-Token': token})


def _rec_id(app, rnd=1):
    with app.app_context():
        return ControlRecord.query.filter_by(zo_number='ZO/100/2026', round=rnd).first().id


def _fill_and_complete(client, app, rec_id, results):
    """results: lista 'OK'/'NG' w kolejnosci punktow."""
    with app.app_context():
        ids = [r.id for r in db.session.get(ControlRecord, rec_id).results]
    data = {'action': 'complete'}
    for rid, val in zip(ids, results):
        data[f'result_{rid}'] = val
    return client.post(f'/kontrola/{rec_id}', data=data, headers={'X-CSRF-Token': _csrf(client)})


class TestAccess:
    def test_requires_login(self, client):
        assert client.get('/kontrola/').status_code in (302, 401)

    def test_operator_cannot_scan_or_manage_points(self, client, operator_user):
        login(client, operator_user, 'Operator123!')
        assert client.get('/kontrola/').status_code == 200
        assert client.get('/kontrola/scan').status_code == 403
        assert client.get('/kontrola/points').status_code == 403
        assert _scan(client).status_code == 403

    def test_kontroler_cannot_manage_points(self, client):
        login(client, 'oper', 'Oper1234!')
        assert client.get('/kontrola/points').status_code == 403

    def test_admin_sees_points(self, client):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/kontrola/points')
        assert resp.status_code == 200
        assert b'Wymiary' in resp.data

    def test_operator_dashboard_redirects(self, client, operator_user):
        login(client, operator_user, 'Operator123!')
        resp = client.get('/')
        assert resp.status_code == 302 and '/kontrola/' in resp.headers['Location']


class TestScanAndFill:
    def test_scan_creates_record_with_active_points_only(self, client, app):
        login(client, 'oper', 'Oper1234!')
        resp = _scan(client)
        assert resp.status_code == 200 and resp.get_json()['ok']
        with app.app_context():
            rec = ControlRecord.query.filter_by(zo_number='ZO/100/2026').one()
            assert [r.title for r in rec.results] == ['Wymiary', 'Malowanie']
            assert rec.round == 1 and rec.status == 'in_progress' and rec.quantity == 3

    def test_scan_twice_opens_existing(self, client, app):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        resp = _scan(client)
        assert 'już istnieje' in resp.get_json()['msg']
        with app.app_context():
            assert ControlRecord.query.filter_by(zo_number='ZO/100/2026').count() == 1

    def test_detail_renders_for_each_role(self, client, app, operator_user):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        page = client.get(f'/kontrola/{rid}')
        assert page.status_code == 200 and b'Zako' in page.data and b'Wymiary' in page.data
        _fill_and_complete(client, app, rid, ['OK', 'OK'])
        assert client.get(f'/kontrola/{rid}').status_code == 200
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        resp = client.get(f'/kontrola/{rid}')
        assert resp.status_code == 200 and b'Wykonane' in resp.data

    def test_scan_without_zo_rejected(self, client):
        login(client, 'oper', 'Oper1234!')
        assert _scan(client, {'p': 'X', 'q': 1}).status_code == 400

    def test_scan_without_points_rejected(self, client, app):
        with app.app_context():
            ControlPoint.query.delete()
            db.session.commit()
        login(client, 'oper', 'Oper1234!')
        assert _scan(client).status_code == 400

    def test_complete_requires_all_rated(self, client, app):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        _fill_and_complete(client, app, rid, ['OK', ''])
        with app.app_context():
            assert db.session.get(ControlRecord, rid).status == 'in_progress'

    def test_ng_creates_qar_once(self, client, app):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        _fill_and_complete(client, app, rid, ['OK', 'NG'])
        with app.app_context():
            rec = db.session.get(ControlRecord, rid)
            assert rec.status == 'completed' and rec.result == 'NG'
            assert QARReport.query.filter_by(zo_number='ZO/100/2026').count() == 1


class TestOperatorFlow:
    def _completed(self, client, app, results=('OK', 'OK')):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        _fill_and_complete(client, app, rid, list(results))
        client.get('/logout')
        return rid

    def test_operator_does_not_see_unfinished_control(self, client, app, operator_user):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        assert b'ZO/100/2026' not in client.get('/kontrola/?s=to_do').data
        assert client.get(f'/kontrola/{rid}').status_code == 403

    def test_completion_alerts_operator_and_lists_order(self, client, app, operator_user):
        rid = self._completed(client, app)
        with app.app_context():
            op = User.query.filter_by(username=operator_user).one()
            assert Alert.query.filter_by(recipient_id=op.id).filter(
                Alert.message.like('ZO ZO/100/2026%')).count() == 1
        login(client, operator_user, 'Operator123!')
        resp = client.get('/kontrola/')
        assert b'ZO/100/2026' in resp.data and b'Wykonane' in resp.data

    def test_done_creates_recheck_for_controller(self, client, app, operator_user):
        rid = self._completed(client, app)
        login(client, operator_user, 'Operator123!')
        resp = client.post(f'/kontrola/{rid}/done', headers={'X-CSRF-Token': _csrf(client)})
        assert resp.status_code == 302
        with app.app_context():
            first = db.session.get(ControlRecord, rid)
            assert first.done_at and first.done_by.username == operator_user
            assert first.state == 'recheck'
            fu = first.followup
            assert fu.round == 2 and fu.status == 'in_progress' and len(fu.results) == 2
            assert fu.user_id == first.user_id
            fu_id = fu.id
        # drugie klikniecie nie tworzy kolejnej kontroli
        client.post(f'/kontrola/{rid}/done', headers={'X-CSRF-Token': _csrf(client)})
        with app.app_context():
            assert ControlRecord.query.filter_by(zo_number='ZO/100/2026', round=2).count() == 1
        # operator nie moze wypelniac kontroli sprawdzajacej
        resp = client.post(f'/kontrola/{fu_id}', data={'action': 'save'},
                           headers={'X-CSRF-Token': _csrf(client)})
        assert resp.status_code == 403

    def test_kontroler_cannot_mark_done(self, client, app):
        rid = self._completed(client, app)
        login(client, 'oper', 'Oper1234!')
        resp = client.post(f'/kontrola/{rid}/done', headers={'X-CSRF-Token': _csrf(client)})
        assert resp.status_code == 403

    def test_recheck_closes_cycle_and_scan_opens_latest(self, client, app, operator_user):
        rid = self._completed(client, app, ('NG', 'OK'))
        login(client, operator_user, 'Operator123!')
        client.post(f'/kontrola/{rid}/done', headers={'X-CSRF-Token': _csrf(client)})
        client.get('/logout')

        login(client, 'oper', 'Oper1234!')
        fu_id = _rec_id(app, 2)
        # skan kodu otwiera kontrole sprawdzajaca
        assert str(fu_id) in _scan(client).get_json()['redirect']
        _fill_and_complete(client, app, fu_id, ['OK', 'OK'])
        with app.app_context():
            first = db.session.get(ControlRecord, rid)
            assert first.state == 'closed' and first.followup.result == 'OK'
        assert b'Zamkni' in client.get('/kontrola/?s=closed').data


class TestPointsAdmin:
    def test_add_edit_move_delete(self, client, app):
        login(client, 'admin', 'Admin1234!')
        h = {'X-CSRF-Token': _csrf(client)}
        client.post('/kontrola/points/add', data={'title': 'Etykiety'}, headers=h)
        with app.app_context():
            pts = ControlPoint.query.order_by(ControlPoint.order).all()
            assert [p.title for p in pts][-1] == 'Etykiety'
            new_id, first_id = pts[-1].id, pts[0].id
        client.post(f'/kontrola/points/{new_id}/save',
                    data={'title': 'Etykiety CE', 'is_active': '1'}, headers=h)
        client.post(f'/kontrola/points/{new_id}/move/up', headers=h)
        with app.app_context():
            ordered = [p.title for p in ControlPoint.query.order_by(ControlPoint.order).all()]
            assert ordered.index('Etykiety CE') == len(ordered) - 2
        client.post(f'/kontrola/points/{new_id}/delete', headers=h)
        with app.app_context():
            assert db.session.get(ControlPoint, new_id) is None
            assert db.session.get(ControlPoint, first_id) is not None

    def test_delete_keeps_history(self, client, app):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        with app.app_context():
            pid = ControlPoint.query.filter_by(title='Wymiary').one().id
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        client.post(f'/kontrola/points/{pid}/delete', headers={'X-CSRF-Token': _csrf(client)})
        with app.app_context():
            titles = [r.title for r in db.session.get(ControlRecord, rid).results]
            assert 'Wymiary' in titles
