"""Kontrola międzyetapowa, etap 2: zgłoszenie wykonania na Stanowisku."""
import pytest

from app import db
from models import (User, Alert, ControlOrder, ProductionDepartment, DepartmentEmployee,
                    ProductionStage, DefectCause, StageReport, Nonconformity)
from test_app import login, logout, _csrf

QR = {'o': 'ZO-MLD-1', 'p': 'PSH 600X800X300', 'c': 'Klient Test', 'q': 10}


@pytest.fixture
def setup(app):
    with app.app_context():
        for name, role, pw in (('stan2', 'produkcja', 'Stan1234!'), ('kontr2', 'kontroler', 'Kont1234!')):
            if not User.query.filter_by(username=name).first():
                u = User(username=name, email=f'{name}@test.pl', role=role)
                u.set_password(pw)
                db.session.add(u)
        dept = ProductionDepartment.query.filter_by(name='Gięcie MLD').first()
        if not dept:
            dept = ProductionDepartment(name='Gięcie MLD', order=90)
            db.session.add(dept)
            db.session.flush()
            db.session.add(DepartmentEmployee(department_id=dept.id, name='Jan Testowy'))
            db.session.add(ProductionStage(name='Gięcie MLD', order=900, department_id=dept.id))
            db.session.add(DefectCause(name='Wymiar MLD', order=900))
        db.session.commit()
        emp = DepartmentEmployee.query.filter_by(name='Jan Testowy').first()
        stage = ProductionStage.query.filter_by(name='Gięcie MLD').first()
        cause = DefectCause.query.filter_by(name='Wymiar MLD').first()
        return {'token': emp.token, 'emp_id': emp.id, 'stage_id': stage.id, 'cause_id': cause.id}


def _hdr(client):
    return {'X-CSRF-Token': _csrf(client)}


def _station(client, setup):
    login(client, 'stan2', 'Stan1234!')
    r = client.post('/produkcja/skan/pracownik', json={'code': 'PSH-DEP:' + setup['token']}, headers=_hdr(client))
    assert r.status_code == 200 and r.get_json()['employee']['kind'] == 'dep'
    return client


def _scan(client, zo=None):
    payload = {**QR, 'o': zo} if zo else QR
    r = client.post('/produkcja/skan/meldunek/zlecenie', json=payload, headers=_hdr(client))
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()['order']


def test_pierwszy_skan_tworzy_zlecenie_w_systemie(app, client, setup):
    _station(client, setup)
    o = _scan(client)
    assert o['is_new'] is True and o['qty'] == 10 and o['client'] == 'Klient Test'
    assert _scan(client)['is_new'] is False        # ponowny skan nie tworzy drugiego
    with app.app_context():
        assert ControlOrder.query.filter_by(zo_number='ZO-MLD-1').count() == 1


def test_meldunek_9_zgodnych_1_niezgodna_tworzy_alert(app, client, setup):
    _station(client, setup)
    o = _scan(client)
    r = client.post('/produkcja/skan/meldunek', json={
        'order_id': o['id'], 'stage_id': setup['stage_id'], 'qty_ok': 9, 'qty_ng': 1,
        'cause_id': setup['cause_id']}, headers=_hdr(client))
    d = r.get_json()
    assert r.status_code == 200 and d['ok'], d
    st = [s for s in d['order']['stages'] if s['id'] == setup['stage_id']][0]
    assert (st['ok'], st['ng']) == (9, 1)
    with app.app_context():
        rep = StageReport.query.filter_by(control_order_id=o['id']).one()
        assert rep.employee_name == 'Jan Testowy' and rep.department_name == 'Gięcie MLD'
        nc = Nonconformity.query.filter_by(control_order_id=o['id']).one()
        assert nc.qty == 1 and nc.cause_name == 'Wymiar MLD' and nc.status == 'do_weryfikacji'
        kontr = User.query.filter_by(username='kontr2').first()
        alerts = Alert.query.filter_by(recipient_id=kontr.id).all()
        assert any('Niezgodność do weryfikacji' in a.message and 'ZO-MLD-1' in a.message for a in alerts)


def test_niezgodnosc_wymaga_przyczyny_i_ilosc_nie_moze_byc_zerowa(client, setup):
    _station(client, setup)
    o = _scan(client)
    base = {'order_id': o['id'], 'stage_id': setup['stage_id']}
    r = client.post('/produkcja/skan/meldunek', json={**base, 'qty_ok': 0, 'qty_ng': 0}, headers=_hdr(client))
    assert r.status_code == 400
    r = client.post('/produkcja/skan/meldunek', json={**base, 'qty_ok': 1, 'qty_ng': 1}, headers=_hdr(client))
    assert r.status_code == 400 and 'przyczynę' in r.get_json()['error']
    r = client.post('/produkcja/skan/meldunek', json={**base, 'qty_ok': -1, 'qty_ng': 0}, headers=_hdr(client))
    assert r.status_code == 400


def test_przekroczenie_ilosci_zlecenia_wymaga_potwierdzenia(app, client, setup):
    _station(client, setup)
    o = _scan(client, 'ZO-MLD-2')
    base = {'order_id': o['id'], 'stage_id': setup['stage_id'], 'qty_ok': 5, 'qty_ng': 0}
    assert client.post('/produkcja/skan/meldunek', json=base, headers=_hdr(client)).get_json()['ok']
    big = {**base, 'qty_ok': 6}
    d = client.post('/produkcja/skan/meldunek', json=big, headers=_hdr(client)).get_json()
    assert d['status'] == 'confirm'
    d = client.post('/produkcja/skan/meldunek', json={**big, 'confirm': True}, headers=_hdr(client)).get_json()
    assert d['ok'] is True


def test_bez_plakietki_dzialu_brak_dostepu(client, setup):
    login(client, 'stan2', 'Stan1234!')
    r = client.post('/produkcja/skan/meldunek/zlecenie', json=QR, headers=_hdr(client))
    assert r.status_code == 401
    r = client.post('/produkcja/skan/pracownik', json={'code': 'PSH-DEP:zly-token'}, headers=_hdr(client))
    assert r.status_code == 404


def test_kontroler_nie_ma_dostepu_do_zgloszen(client, setup):
    login(client, 'kontr2', 'Kont1234!')
    r = client.post('/produkcja/skan/meldunek/zlecenie', json=QR, headers=_hdr(client))
    assert r.status_code == 403


def test_przyczyny_w_ustawieniach(app, client, setup):
    login(client, 'admin', 'Admin1234!')
    page = client.get('/ustawienia-produkcji/przyczyny')
    assert page.status_code == 200 and 'Wymiar MLD' in page.get_data(as_text=True)
    r = client.post('/ustawienia-produkcji/przyczyny',
                    data={'action': 'add', 'name': 'Gratowanie MLD', '_csrf_token': _csrf(client)},
                    follow_redirects=True)
    assert 'Dodano przyczynę' in r.get_data(as_text=True)
    with app.app_context():
        c = DefectCause.query.filter_by(name='Gratowanie MLD').one()
        cid = c.id
    r = client.post('/ustawienia-produkcji/przyczyny',
                    data={'action': 'delete', 'cause_id': cid, '_csrf_token': _csrf(client)},
                    follow_redirects=True)
    with app.app_context():
        assert DefectCause.query.filter_by(name='Gratowanie MLD').first() is None
