"""Kontrola etapowa w raporcie Kontroli Końcowej: dopasowanie po ZO + produkt (klient tylko ostrzega),
pełne punkty z pomiarami, osoba odpowiedzialna, ostrzeżenia, ekran raportu i PDF."""
import pytest

from app import db
from models import (User, Report, ControlRecord, ControlResult, ControlOrder, ControlRecordElement,
                    ControlElement, ProductionDepartment, DepartmentEmployee)
from kontrola.report_link import stage_controls_for_report
from test_app import login

ZO = 'ZO/960/2026'


def _wipe():
    ControlResult.query.delete()
    ControlRecordElement.query.delete()
    ControlRecord.query.update({'employee_id': None})
    ControlRecord.query.delete()
    ControlOrder.query.delete()
    ControlElement.query.filter(ControlElement.name.like('T11 %')).delete(synchronize_session=False)
    DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T11 %')).delete(synchronize_session=False)
    ProductionDepartment.query.filter(ProductionDepartment.name.like('T11 %')).delete(synchronize_session=False)
    Report.query.filter(Report.qr_key.like('%|ZO/960/%')).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture(autouse=True)
def _clean(app):
    with app.app_context():
        _wipe()
    yield
    with app.app_context():
        _wipe()


def _report(key):
    uid = User.query.filter_by(username='oper').first().id
    r = Report(user_id=uid, title='Szafa', qr_key=key, report_type='kontroler')
    db.session.add(r)
    db.session.commit()
    return r


def _order(product, client='Klient X', zo=ZO):
    o = ControlOrder(zo_number=zo, product_name=product, client=client)
    db.session.add(o)
    db.session.commit()
    return o


def _control(order, dept, status='completed', results=(), element=None, employee=None):
    uid = User.query.filter_by(username='oper').first().id
    rec = ControlRecord(zo_number=order.zo_number, product_name=order.product_name, client=order.client,
                        user_id=uid, status=status, department_id=dept.id, control_order_id=order.id,
                        employee_id=employee.id if employee else None)
    db.session.add(rec)
    db.session.flush()
    if element:
        db.session.add(ControlRecordElement(record_id=rec.id, name=element))
    for i, (title, result, extra) in enumerate(results):
        db.session.add(ControlResult(record_id=rec.id, title=title, result=result, position=i, **extra))
    db.session.commit()
    return rec


def _dept(name='T11 Spawalnia'):
    d = ProductionDepartment(name=name, order=960)
    db.session.add(d)
    db.session.commit()
    return d


def test_dopasowanie_po_produkcie_a_nie_tylko_po_zo(app):
    with app.app_context():
        d = _dept()
        mine = _order('PSH 600X800X300 (P)')
        other = _order('RUL 1900X1150X250')
        _control(mine, d, results=[('Spoiny', 'OK', {})])
        _control(other, d, results=[('Inne', 'NG', {})])
        report = _report('PSH-600-800-300 IP65|KLIENT X|' + ZO)
        data = stage_controls_for_report(report)
        assert [s['record'].control_order_id for s in data['stages']] == [mine.id]
        assert [r.title for r in data['stages'][0]['record'].results] == ['Spoiny']
        assert any(lvl == 'info' and 'innych produktów' in t for lvl, t in data['warnings'])


def test_pelne_punkty_pomiar_i_osoba_odpowiedzialna(app):
    with app.app_context():
        d = _dept()
        emp = DepartmentEmployee(name='T11 Jan Spawacz', department_id=d.id)
        db.session.add(emp)
        db.session.commit()
        o = _order('PSH 600X800X300')
        _control(o, d, employee=emp, results=[
            ('Wymiar', 'OK', {'point_type': 'pomiar', 'unit': 'mm', 'value_min': 10, 'value_max': 12,
                              'value_text': '11,5'})])
        data = stage_controls_for_report(_report('PSH 600X800X300|KLIENT X|' + ZO))
        s = data['stages'][0]
        assert s['performer'] == 'T11 Jan Spawacz'
        r = s['groups'][0]['results'][0]
        assert r.value_text == '11,5' and r.is_measure
        assert data['warnings'] == []


def test_klient_rozny_tylko_ostrzega(app):
    with app.app_context():
        d = _dept()
        o = _order('PSH 600X800X300', client='Klient Y')
        _control(o, d, results=[('Spoiny', 'OK', {})])
        data = stage_controls_for_report(_report('PSH 600X800X300|KLIENT X|' + ZO))
        assert len(data['stages']) == 1
        assert any('Klient' in t for _, t in data['warnings'])


def test_ostrzezenia_brak_ng_i_w_toku(app):
    with app.app_context():
        d = _dept()
        report = _report('PSH 600X800X300|KLIENT X|' + ZO)
        assert any('Brak kontroli etapowej' in t for _, t in stage_controls_for_report(report)['warnings'])
        o = _order('PSH 600X800X300')
        rec = _control(o, d, results=[('Spoiny', 'NG', {})])
        assert ('ng', 'T11 Spawalnia: ostatnia kontrola z wynikiem NG.') in \
            stage_controls_for_report(report)['warnings']
        rec.status = 'in_progress'
        db.session.commit()
        assert any('nie jest zakończona' in t for _, t in stage_controls_for_report(report)['warnings'])


def test_ponowna_kontrola_pokazuje_najnowsza(app):
    with app.app_context():
        d = _dept()
        o = _order('PSH 600X800X300')
        _control(o, d, results=[('Spoiny', 'NG', {})])
        _control(o, d, results=[('Spoiny', 'OK', {})])
        data = stage_controls_for_report(_report('PSH 600X800X300|KLIENT X|' + ZO))
        assert len(data['stages']) == 1
        assert data['stages'][0]['result'] == 'OK' and data['stages'][0]['attempt'] == 2


def test_raport_bez_zo_nie_ma_sekcji(app):
    with app.app_context():
        assert stage_controls_for_report(_report(None)) is None


def test_ekran_i_pdf_raportu(app, client):
    with app.app_context():
        d = _dept()
        o = _order('PSH 600X800X300')
        _control(o, d, results=[('Spoiny T11', 'OK', {}),
                                ('Wymiar', 'NG', {'point_type': 'pomiar', 'unit': 'mm', 'value_min': 1,
                                                  'value_max': 2, 'value_text': '5'})])
        rid = _report('PSH 600X800X300|KLIENT X|' + ZO).id
    login(client, 'oper', 'Oper1234!')
    html = client.get(f'/reports/{rid}').get_data(as_text=True)
    assert 'Kontrola etapowa' in html and 'Spoiny T11' in html and 'T11 Spawalnia' in html
    pdf = client.get(f'/reports/{rid}/pdf')
    assert pdf.status_code == 200 and pdf.data.startswith(b'%PDF')
