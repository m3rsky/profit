"""Testy modelu wózków: bufor jest tylko miejscem, a ilość pamięta etap, na który czeka (zmiana planu etapu 5)."""
import pytest
from sqlalchemy import text

from app import app as flask_app, db, _migrate_stock_positions
from models import (User, ProductionDepartment, DepartmentEmployee, ProductionStage, Buffer, WorkOrder,
                    WorkOrderComponent, WorkOrderComponentStage, StockPosition, Movement, WorkInProgress,
                    StageControl)
from test_app import login, _csrf
from kp import stock, flow

KP = '/kontrolaprodukcji'


def api(client, path, **payload):
    return client.post(KP + path, json=payload, headers={'X-CSRF-Token': _csrf(client)})


@pytest.fixture(autouse=True)
def _clean(app):
    def wipe():
        with app.app_context():
            for m in (StageControl, Movement, WorkInProgress, StockPosition, WorkOrderComponentStage,
                      WorkOrderComponent):
                m.query.delete()
            WorkOrder.query.filter(WorkOrder.zo_number.like('T6-%')).delete(synchronize_session=False)
            Buffer.query.filter(Buffer.code.like('T6%')).delete(synchronize_session=False)
            ProductionStage.query.filter(ProductionStage.name.like('T6 %')).delete(synchronize_session=False)
            DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T6 %')).delete(synchronize_session=False)
            ProductionDepartment.query.filter(ProductionDepartment.name.like('T6 %')).delete(
                synchronize_session=False)
            db.session.commit()
    wipe()
    yield
    wipe()


@pytest.fixture
def w(app):
    """Etapy A, B, C (elementowe) i S (składanie); dwa wózki bez etapu; dwa zlecenia z elementem Rama
    (trasa A,B,C); pracownicy działów."""
    with app.app_context():
        dept = ProductionDepartment(name='T6 Dział', order=700)
        db.session.add(dept)
        db.session.flush()
        st = {k: ProductionStage(name=f'T6 {k}', order=740 + i, level='produkt' if k == 'S' else 'element',
                                 department_id=dept.id) for i, k in enumerate('ABCS')}
        db.session.add_all(st.values())
        db.session.flush()
        bufs = {k: Buffer(code=f'T6{k}') for k in ('W1', 'W2')}
        db.session.add_all(bufs.values())
        admin = User.query.filter_by(username='admin').one()
        comps = {}
        for zo in ('T6-ZO-1', 'T6-ZO-2'):
            o = WorkOrder(zo_number=zo, product_name='PSH-T6', client='X', quantity=10, created_by_id=admin.id)
            db.session.add(o)
            db.session.flush()
            c = WorkOrderComponent(order_id=o.id, name='Rama', per_unit=1, required_qty=10, position=1)
            db.session.add(c)
            db.session.flush()
            for k in 'ABC':
                db.session.add(WorkOrderComponentStage(component_id=c.id, stage_id=st[k].id))
            comps[zo] = c.id
        emp = DepartmentEmployee(department_id=dept.id, name='T6 Jan')
        db.session.add(emp)
        db.session.commit()
        return {'stage': {k: v.id for k, v in st.items()}, 'buf': {k: v.id for k, v in bufs.items()},
                'comp': comps, 'token': emp.token}


def station(client, w):
    login(client, 'oper', 'Oper1234!')
    assert api(client, '/stanowisko/skan', code='PSH-DEP:' + w['token']).status_code == 200


def positions(app):
    with app.app_context():
        return sorted((p.component.order.zo_number, p.buffer.code, p.stage.name, p.status, p.quantity)
                      for p in StockPosition.query.filter(StockPosition.quantity > 0).all())


class TestCarts:
    def test_cart_holds_many_orders_and_stages(self, client, app, w):
        station(client, w)
        a = api(client, '/stanowisko/wpis', component_id=w['comp']['T6-ZO-1'], buffer_id=w['buf']['W1'],
                quantity=4, stage_id=w['stage']['A'])
        b = api(client, '/stanowisko/wpis', component_id=w['comp']['T6-ZO-2'], buffer_id=w['buf']['W1'],
                quantity=3, stage_id=w['stage']['B'])
        assert a.status_code == 200 and b.status_code == 200
        assert positions(app) == [('T6-ZO-1', 'T6W1', 'T6 A', 'ok', 4), ('T6-ZO-2', 'T6W1', 'T6 B', 'ok', 3)]
        contents = b.get_json()['buffer']['contents']
        assert {(r['zo'], r['stage']) for r in contents} == {('T6-ZO-1', 'T6 A'), ('T6-ZO-2', 'T6 B')}

    def test_default_entry_stage_is_first_of_route(self, client, app, w):
        station(client, w)
        res = api(client, '/stanowisko/wpis', component_id=w['comp']['T6-ZO-1'], buffer_id=w['buf']['W1'],
                  quantity=2)
        assert res.status_code == 200
        assert positions(app) == [('T6-ZO-1', 'T6W1', 'T6 A', 'ok', 2)]
        comp = res.get_json()['order']['components'][0]
        assert comp['default_entry_stage_id'] == w['stage']['A']
        assert [s['name'] for s in comp['entry_stages']] == ['T6 A', 'T6 B', 'T6 C', 'T6 S']

    def test_takeover_only_quantity_waiting_for_that_stage(self, client, app, w):
        station(client, w)
        api(client, '/stanowisko/wpis', component_id=w['comp']['T6-ZO-1'], buffer_id=w['buf']['W1'],
            quantity=5, stage_id=w['stage']['B'])
        res = api(client, '/stanowisko/przejecie', component_id=w['comp']['T6-ZO-1'],
                  buffer_id=w['buf']['W1'], stage_id=w['stage']['A'], quantity=1)
        assert res.status_code == 400 and 'nie ma' in res.get_json()['error']
        res = api(client, '/stanowisko/przejecie', component_id=w['comp']['T6-ZO-1'],
                  buffer_id=w['buf']['W1'], stage_id=w['stage']['B'], quantity=5)
        assert res.status_code == 200
        assert positions(app) == []

    def test_transfer_defaults_to_next_stage_and_allows_later_one(self, client, app, w):
        station(client, w)
        c = w['comp']['T6-ZO-1']
        api(client, '/stanowisko/wpis', component_id=c, buffer_id=w['buf']['W1'], quantity=6,
            stage_id=w['stage']['A'])
        res = api(client, '/stanowisko/przejecie', component_id=c, buffer_id=w['buf']['W1'],
                  stage_id=w['stage']['A'], quantity=6)
        wip = max(res.get_json()['wip'], key=lambda r: r['id'])
        assert [t['name'] for t in wip['targets']] == ['T6 B', 'T6 C', 'T6 S']
        assert wip['default_target_id'] == w['stage']['B']
        # domyślnie następny etap trasy, na inny wózek niż pobrany
        r1 = api(client, '/stanowisko/przenies', wip_id=wip['id'], buffer_id=w['buf']['W2'], quantity=2)
        assert r1.status_code == 200
        # późniejszy etap trasy (pominięcie B)
        r2 = api(client, '/stanowisko/przenies', wip_id=wip['id'], buffer_id=w['buf']['W1'], quantity=3,
                 wait_stage_id=w['stage']['C'])
        assert r2.status_code == 200
        # etap spoza przyszłości (ten sam, wcześniejszy) odrzucony
        r3 = api(client, '/stanowisko/przenies', wip_id=wip['id'], buffer_id=w['buf']['W1'], quantity=1,
                 wait_stage_id=w['stage']['A'])
        assert r3.status_code == 400 and 'może czekać na etap' in r3.get_json()['error']
        assert positions(app) == [('T6-ZO-1', 'T6W1', 'T6 C', 'ok', 3), ('T6-ZO-1', 'T6W2', 'T6 B', 'ok', 2)]

    def test_revert_transfer_removes_exact_position(self, client, app, w):
        station(client, w)
        c = w['comp']['T6-ZO-1']
        api(client, '/stanowisko/wpis', component_id=c, buffer_id=w['buf']['W1'], quantity=4,
            stage_id=w['stage']['A'])
        res = api(client, '/stanowisko/przejecie', component_id=c, buffer_id=w['buf']['W1'],
                  stage_id=w['stage']['A'], quantity=4)
        wip = max(res.get_json()['wip'], key=lambda r: r['id'])['id']
        api(client, '/stanowisko/przenies', wip_id=wip, buffer_id=w['buf']['W2'], quantity=4,
            wait_stage_id=w['stage']['C'])
        with app.app_context():
            mid = Movement.query.filter_by(kind='przeniesienie').one().id
        res = client.post(f'{KP}/ruch/{mid}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert res.status_code == 200
        assert positions(app) == []
        with app.app_context():
            assert [(x.stage.name, x.quantity) for x in WorkInProgress.query.all()] == [('T6 A', 4)]

    def test_buffer_pages_show_waiting_stage(self, client, app, w):
        station(client, w)
        api(client, '/stanowisko/wpis', component_id=w['comp']['T6-ZO-1'], buffer_id=w['buf']['W1'],
            quantity=4, stage_id=w['stage']['B'])
        page = client.get(f'{KP}/bufory/{w["buf"]["W1"]}').get_data(as_text=True)
        assert 'Czeka na etap' in page and 'T6 B' in page
        assert 'wszystkie etapy' not in client.get(KP + '/bufory').get_data(as_text=True)


class TestMigration:
    def test_old_positions_get_stage_from_buffer(self, app, w):
        """Stara tabela pozycji (bufor przypisany do etapu) jest przebudowana: etap bufora trafia do pozycji."""
        with app.app_context():
            b = db.session.get(Buffer, w['buf']['W1'])
            b.stage_id = w['stage']['B']
            db.session.commit()
            db.session.execute(text(
                'CREATE TABLE stock_positions_old (id INTEGER PRIMARY KEY, component_id INTEGER, '
                'buffer_id INTEGER, quantity INTEGER, status VARCHAR(16), updated_at DATETIME)'))
            db.session.execute(text(
                'INSERT INTO stock_positions_old (component_id, buffer_id, quantity, status) '
                'VALUES (:c, :b, 7, \'ok\')'), {'c': w['comp']['T6-ZO-1'], 'b': w['buf']['W1']})
            db.session.commit()
            _migrate_stock_positions()
            rows = [(p.component_id, p.buffer_id, p.stage_id, p.quantity, p.status)
                    for p in StockPosition.query.all()]
            assert rows == [(w['comp']['T6-ZO-1'], w['buf']['W1'], w['stage']['B'], 7, 'ok')]
            assert db.session.get(Buffer, w['buf']['W1']).stage_id is None
            assert db.session.execute(text(
                "SELECT name FROM sqlite_master WHERE name='stock_positions_old'")).first() is None
