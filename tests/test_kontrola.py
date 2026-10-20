"""Testy modulu Kontrola zamowien (stale punkty kontrolne, operator, kontrola sprawdzajaca)."""
import pytest

from app import db
from models import (User, Alert, QARReport, ControlPoint, ControlRecord, ControlResult,
                    ProductionDepartment, DepartmentEmployee)
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

    def test_same_zo_other_product_creates_separate_control(self, client, app):
        login(client, 'oper', 'Oper1234!')
        first = _scan(client).get_json()
        second = _scan(client, {**QR, 'p': 'Szafa INOX 400', 'q': 2}).get_json()
        assert second['ok'] and second['redirect'] != first['redirect']
        assert 'już istnieje' not in second['msg']
        with app.app_context():
            recs = ControlRecord.query.filter_by(zo_number='ZO/100/2026', round=1).all()
            assert sorted(r.product_name for r in recs) == ['Szafa INOX 400', 'Szafa PSH']
        # ponowny skan kazdego z produktow otwiera wlasciwa kontrole
        again = _scan(client, {**QR, 'p': '  szafa   psh '}).get_json()
        assert again['redirect'] == first['redirect'] and 'już istnieje' in again['msg']
        again2 = _scan(client, {**QR, 'p': 'Szafa INOX 400'}).get_json()
        assert again2['redirect'] == second['redirect']

    def test_list_search_by_product_and_client(self, client, app):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        assert b'ZO/100/2026' in client.get('/kontrola/?zo=psh').data
        assert b'ZO/100/2026' in client.get('/kontrola/?zo=klient+x').data
        assert b'ZO/100/2026' not in client.get('/kontrola/?zo=nieistnieje').data

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


class TestDelete:
    def _cycle(self, client, app, operator_user):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        _fill_and_complete(client, app, rid, ['OK', 'NG'])
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        client.post(f'/kontrola/{rid}/done', headers={'X-CSRF-Token': _csrf(client)})
        client.get('/logout')
        return rid

    def test_admin_deletes_whole_cycle_keeps_qar(self, client, app, operator_user):
        rid = self._cycle(client, app, operator_user)
        login(client, 'admin', 'Admin1234!')
        resp = client.post(f'/kontrola/{rid}/delete', headers={'X-CSRF-Token': _csrf(client)})
        assert resp.status_code == 302
        with app.app_context():
            assert ControlRecord.query.filter_by(zo_number='ZO/100/2026').count() == 0
            assert ControlResult.query.count() == 0
            assert QARReport.query.filter_by(zo_number='ZO/100/2026').count() == 1

    def test_bulk_delete(self, client, app, operator_user):
        self._cycle(client, app, operator_user)
        ids = []
        with app.app_context():
            ids = [r.id for r in ControlRecord.query.all()]
        login(client, 'admin', 'Admin1234!')
        client.post('/kontrola/bulk-delete', data={'record_ids': ids},
                    headers={'X-CSRF-Token': _csrf(client)})
        with app.app_context():
            assert ControlRecord.query.count() == 0

    def test_others_cannot_delete(self, client, app, operator_user):
        rid = self._cycle(client, app, operator_user)
        for user, pw in (('oper', 'Oper1234!'), (operator_user, 'Operator123!')):
            login(client, user, pw)
            h = {'X-CSRF-Token': _csrf(client)}
            assert client.post(f'/kontrola/{rid}/delete', headers=h).status_code == 403
            assert client.post('/kontrola/bulk-delete', data={'record_ids': [rid]},
                               headers=h).status_code == 403
            client.get('/logout')


# ── Wykonawca kontrolowanej pracy (pracownik i dział) ─────────────────────────

@pytest.fixture
def staff(app):
    """Dwa działy testowe T8 z pracownikami (jeden nieaktywny) oraz nieaktywny dział."""
    with app.app_context():
        d1 = ProductionDepartment(name='T8 Spawalnia', order=990)
        d2 = ProductionDepartment(name='T8 Montaż', order=991)
        d3 = ProductionDepartment(name='T8 Stary', order=992, is_active=False)
        db.session.add_all([d1, d2, d3])
        db.session.flush()
        e = {'jan': DepartmentEmployee(department_id=d1.id, name='T8 Jan Spawacz'),
             'ola': DepartmentEmployee(department_id=d2.id, name='T8 Ola Monterka'),
             'off': DepartmentEmployee(department_id=d1.id, name='T8 Nieaktywny', is_active=False),
             'old': DepartmentEmployee(department_id=d3.id, name='T8 Z starego działu')}
        db.session.add_all(e.values())
        db.session.commit()
        ids = {k: v.id for k, v in e.items()}
        ids['d1'], ids['d2'] = d1.id, d2.id
    yield ids
    with app.app_context():
        QARReport.query.filter(QARReport.employee_id.in_([ids[k] for k in ('jan', 'ola', 'off', 'old')])).update(
            {'employee_id': None}, synchronize_session=False)
        ControlResult.query.delete()
        ControlRecord.query.update({'employee_id': None})
        ControlRecord.query.delete()
        DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T8 %')).delete(synchronize_session=False)
        ProductionDepartment.query.filter(ProductionDepartment.name.like('T8 %')).delete(synchronize_session=False)
        db.session.commit()


def _save(client, app, rid, results=None, employee=None, action='save'):
    with app.app_context():
        ids = [r.id for r in db.session.get(ControlRecord, rid).results]
    data = {'action': action}
    for res_id, val in zip(ids, results or []):
        data[f'result_{res_id}'] = val
    if employee is not None:
        data['employee_id'] = str(employee)
    return client.post(f'/kontrola/{rid}', data=data, headers={'X-CSRF-Token': _csrf(client)},
                       follow_redirects=True)


class TestPerformer:
    def test_form_has_grouped_list_and_saves_performer(self, client, app, staff):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        page = client.get(f'/kontrola/{rid}').get_data(as_text=True)
        assert 'Kto wykonał pracę' in page and 'optgroup label="T8 Spawalnia"' in page
        assert 'T8 Jan Spawacz' in page and 'T8 Nieaktywny' not in page and 'T8 Z starego' not in page
        _save(client, app, rid, ['OK', 'OK'], staff['jan'])
        with app.app_context():
            rec = db.session.get(ControlRecord, rid)
            assert rec.employee.name == 'T8 Jan Spawacz' and rec.employee.department.name == 'T8 Spawalnia'
            assert rec.status == 'in_progress'

    def test_optional_and_can_be_cleared(self, client, app, staff):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        _save(client, app, rid, ['OK', 'OK'], action='complete')           # bez pola: wolno zakończyć
        with app.app_context():
            assert db.session.get(ControlRecord, rid).status == 'completed'
            assert db.session.get(ControlRecord, rid).employee_id is None
        client.get('/logout')
        login(client, 'admin', 'Admin1234!')
        client.post(f'/kontrola/{rid}/reopen', headers={'X-CSRF-Token': _csrf(client)})
        _save(client, app, rid, employee=staff['ola'])
        _save(client, app, rid, employee='')
        with app.app_context():
            assert db.session.get(ControlRecord, rid).employee_id is None

    def test_inactive_and_unknown_not_accepted_but_saved_one_stays(self, client, app, staff):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        res = _save(client, app, rid, employee=staff['off'])
        assert 'nieaktywnego pracownika' in res.get_data(as_text=True)
        assert 'Nie znaleziono' in _save(client, app, rid, employee=999999).get_data(as_text=True)
        assert 'nieaktywnego' in _save(client, app, rid, employee=staff['old']).get_data(as_text=True)
        with app.app_context():
            assert db.session.get(ControlRecord, rid).employee_id is None
            db.session.get(ControlRecord, rid).employee_id = staff['jan']
            db.session.commit()
            db.session.get(DepartmentEmployee, staff['jan']).is_active = False
            db.session.commit()
        page = client.get(f'/kontrola/{rid}').get_data(as_text=True)
        assert 'T8 Jan Spawacz' in page and 'nieaktywny' in page
        _save(client, app, rid, employee=staff['jan'])                     # zapisany zostaje dozwolony
        with app.app_context():
            assert db.session.get(ControlRecord, rid).employee_id == staff['jan']

    def test_performer_copied_to_recheck_and_changeable(self, client, app, staff, operator_user):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        _save(client, app, rid, ['OK', 'OK'], staff['jan'], action='complete')
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        client.post(f'/kontrola/{rid}/done', headers={'X-CSRF-Token': _csrf(client)})
        client.get('/logout')
        login(client, 'oper', 'Oper1234!')
        rid2 = _rec_id(app, 2)
        with app.app_context():
            assert db.session.get(ControlRecord, rid2).employee_id == staff['jan']
        _save(client, app, rid2, employee=staff['ola'])
        with app.app_context():
            first = db.session.get(ControlRecord, rid)
            assert (first.employee_id, first.performer.name) == (staff['jan'], 'T8 Ola Monterka')

    def test_qar_gets_performer_at_creation(self, client, app, staff):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        _save(client, app, rid, ['NG', 'OK'], staff['jan'])
        with app.app_context():
            qar = QARReport.query.filter_by(zo_number='ZO/100/2026').one()
            assert qar.employee_id == staff['jan']
            assert 'Wykonawca: T8 Jan Spawacz (T8 Spawalnia)' in qar.description

    def test_qar_performer_filled_later_but_not_overwritten(self, client, app, staff):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        _save(client, app, rid, ['NG', 'NG'])                              # raporty bez wykonawcy
        with app.app_context():
            qars = QARReport.query.filter_by(zo_number='ZO/100/2026').order_by(QARReport.id).all()
            assert [q.employee_id for q in qars] == [None, None]
            qars[0].employee_id = staff['ola']                             # jeden ma już pracownika
            db.session.commit()
        _save(client, app, rid, employee=staff['jan'])
        with app.app_context():
            qars = QARReport.query.filter_by(zo_number='ZO/100/2026').order_by(QARReport.id).all()
            assert [q.employee_id for q in qars] == [staff['ola'], staff['jan']]

    def test_list_shows_performer_and_filters_by_department_and_name(self, client, app, staff):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        _scan(client, {**QR, 'o': 'ZO/200/2026', 'p': 'Inna szafa'})
        _save(client, app, _rec_id(app), employee=staff['jan'])
        page = client.get('/kontrola/').get_data(as_text=True)
        assert 'Kontrole' in page and 'T8 Jan Spawacz' in page and 'T8 Spawalnia' in page
        assert 'T8 Montaż' in page                                          # w filtrze działów
        assert 'ZO/100/2026' in page and 'ZO/200/2026' in page
        d1 = client.get(f'/kontrola/?dzial={staff["d1"]}').get_data(as_text=True)
        assert 'ZO/100/2026' in d1 and 'ZO/200/2026' not in d1
        d2 = client.get(f'/kontrola/?dzial={staff["d2"]}').get_data(as_text=True)
        assert 'ZO/100/2026' not in d2 and 'ZO/200/2026' not in d2
        by_name = client.get('/kontrola/?zo=spawacz').get_data(as_text=True)
        assert 'ZO/100/2026' in by_name and 'ZO/200/2026' not in by_name
        assert 'ZO/100/2026' in client.get('/kontrola/?zo=ZO/100').get_data(as_text=True)

    def test_filter_matches_performer_from_recheck_and_chips_keep_it(self, client, app, staff, operator_user):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        _save(client, app, rid, ['OK', 'OK'], action='complete')
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        client.post(f'/kontrola/{rid}/done', headers={'X-CSRF-Token': _csrf(client)})
        client.get('/logout')
        login(client, 'oper', 'Oper1234!')
        _save(client, app, _rec_id(app, 2), employee=staff['ola'])        # wykonawca dopiero w sprawdzającej
        page = client.get(f'/kontrola/?dzial={staff["d2"]}&s=all').get_data(as_text=True)
        assert 'ZO/100/2026' in page and 'T8 Ola Monterka' in page
        assert f'<option value="{staff["d2"]}" selected>' in page          # wybrany dział zostaje w filtrze

    def test_operator_sees_but_cannot_edit(self, client, app, staff, operator_user):
        login(client, 'oper', 'Oper1234!')
        _scan(client)
        rid = _rec_id(app)
        _save(client, app, rid, ['OK', 'OK'], staff['jan'], action='complete')
        client.get('/logout')
        login(client, operator_user, 'Operator123!')
        page = client.get(f'/kontrola/{rid}').get_data(as_text=True)
        assert 'Wykonawca: T8 Jan Spawacz (T8 Spawalnia)' in page and 'employee_id' not in page
        assert client.post(f'/kontrola/{rid}', data={'employee_id': str(staff['ola'])},
                           headers={'X-CSRF-Token': _csrf(client)}).status_code == 403
        assert 'T8 Jan Spawacz' in client.get('/kontrola/').get_data(as_text=True)
        with app.app_context():
            assert db.session.get(ControlRecord, rid).employee_id == staff['jan']
