"""Testy etapu 5b: decyzje o ilości wstrzymanej (zwolnienie, zwrot do poprawki, złomowanie), ponowna kontrola
po poprawce, archiwum złomu i kontrola doraźna."""
import pytest

from app import app as flask_app, db
from models import (User, ProductionDepartment, DepartmentEmployee, ProductionStage, Buffer, WorkOrder,
                    WorkOrderComponent, WorkOrderComponentStage, StockPosition, Movement, WorkInProgress,
                    StageControl, StageControlResult, StageControlPoint, WorkOrderControl, ScrapRecord, QARReport)
from test_app import login, _csrf
from kp import stock, decisions

KP = '/kontrolaprodukcji'


def api(client, path, **payload):
    return client.post(KP + path, json=payload, headers={'X-CSRF-Token': _csrf(client)})


def form_post(client, url, **data):
    data['_csrf_token'] = _csrf(client)
    return client.post(url, data=data, follow_redirects=True)


@pytest.fixture(autouse=True)
def _clean(app):
    def wipe():
        with app.app_context():
            ids = [c.id for c in StageControl.query.all()]
            if ids:
                StageControlResult.query.filter(StageControlResult.control_id.in_(ids)).delete(
                    synchronize_session=False)
            for m in (ScrapRecord, StageControl, Movement, WorkInProgress, StockPosition, WorkOrderControl,
                      WorkOrderComponentStage, WorkOrderComponent):
                m.query.delete()
            WorkOrder.query.filter(WorkOrder.zo_number.like('T7-%')).delete(synchronize_session=False)
            Buffer.query.filter(Buffer.code.like('T7%')).delete(synchronize_session=False)
            StageControlPoint.query.filter(StageControlPoint.title.like('T7 %')).delete(synchronize_session=False)
            ProductionStage.query.filter(ProductionStage.name.like('T7 %')).delete(synchronize_session=False)
            DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T7 %')).delete(synchronize_session=False)
            ProductionDepartment.query.filter(ProductionDepartment.name.like('T7 %')).delete(
                synchronize_session=False)
            QARReport.query.filter(QARReport.zo_number.like('T7-%')).delete(synchronize_session=False)
            db.session.commit()
    wipe()
    yield
    wipe()


@pytest.fixture
def users(app):
    with app.app_context():
        if not User.query.filter_by(username='kp7_operator').first():
            u = User(username='kp7_operator', email='kp7_operator@test.pl', role='operator')
            u.set_password('Rola1234!')
            db.session.add(u)
            db.session.commit()


@pytest.fixture
def w(app):
    """A Wycinanie (kontrola obowiązkowa, 2 punkty), B Gięcie, K Kompletacja (elementy), S Składanie (produkt);
    wózki W1, W2, W3; zlecenie T7-ZO-1 q=4 z elementem Drzwi (2 na sztukę, wymagane 8, trasa A,B,K)."""
    with app.app_context():
        dept = ProductionDepartment(name='T7 Dział', order=600)
        db.session.add(dept)
        db.session.flush()
        spec = {'A': ('Wycinanie', 'element'), 'B': ('Gięcie', 'element'), 'K': ('Kompletacja', 'element'),
                'S': ('Składanie', 'produkt')}
        st = {k: ProductionStage(name=f'T7 {n}', order=640 + i, level=lv, department_id=dept.id,
                                 control_required=(k == 'A')) for i, (k, (n, lv)) in enumerate(spec.items())}
        db.session.add_all(st.values())
        db.session.flush()
        pts = {'A1': StageControlPoint(stage_id=st['A'].id, order=1, title='T7 Wymiary'),
               'A2': StageControlPoint(stage_id=st['A'].id, order=2, title='T7 Krawędzie'),
               'B1': StageControlPoint(stage_id=st['B'].id, order=1, title='T7 Kąty')}
        db.session.add_all(pts.values())
        bufs = {k: Buffer(code=f'T7{k}') for k in ('W1', 'W2', 'W3')}
        db.session.add_all(bufs.values())
        o = WorkOrder(zo_number='T7-ZO-1', product_name='PSH-T7', client='WEG', quantity=4,
                      created_by_id=User.query.filter_by(username='admin').one().id)
        db.session.add(o)
        db.session.flush()
        c = WorkOrderComponent(order_id=o.id, name='Drzwi', per_unit=2, required_qty=8, position=1)
        db.session.add(c)
        db.session.flush()
        for k in 'ABK':
            db.session.add(WorkOrderComponentStage(component_id=c.id, stage_id=st[k].id))
        emp = DepartmentEmployee(department_id=dept.id, name='T7 Jan')
        db.session.add(emp)
        db.session.commit()
        return {'order': o.id, 'comp': c.id, 'stage': {k: v.id for k, v in st.items()},
                'point': {k: v.id for k, v in pts.items()}, 'buf': {k: v.id for k, v in bufs.items()},
                'token': emp.token}


def boss(client, user='oper', pw='Oper1234!'):
    client.get('/logout')
    login(client, user, pw)


def worker(client, w):
    boss(client, 'oper', 'Oper1234!')
    assert api(client, '/stanowisko/skan', code='PSH-DEP:' + w['token']).status_code == 200


def produce(client, w, qty, src_stage, dst_buf, dst_stage, src_buf='W1'):
    """Wpis początkowy, przejęcie etapu i odstawienie na `dst_buf` (czeka na `dst_stage`)."""
    worker(client, w)
    assert api(client, '/stanowisko/wpis', component_id=w['comp'], buffer_id=w['buf'][src_buf], quantity=qty,
               stage_id=w['stage'][src_stage]).status_code == 200
    res = api(client, '/stanowisko/przejecie', component_id=w['comp'], buffer_id=w['buf'][src_buf],
              stage_id=w['stage'][src_stage], quantity=qty)
    assert res.status_code == 200, res.get_json()
    wip = max(res.get_json()['wip'], key=lambda r: r['id'])['id']
    out = api(client, '/stanowisko/przenies', wip_id=wip, buffer_id=w['buf'][dst_buf], quantity=qty,
              wait_stage_id=w['stage'][dst_stage])
    assert out.status_code == 200, out.get_json()
    boss(client)


def rate(client, control_id, expected, ok, ng, results=None):
    data = {'expected': expected, 'ok_qty': ok, 'ng_qty': ng}
    for pid, (res, notes) in (results or {}).items():
        data[f'result_{pid}'] = res
        data[f'pnotes_{pid}'] = notes
    return form_post(client, f'{KP}/kontrole/{control_id}/ocen', **data)


def pos_rows(app):
    with app.app_context():
        return sorted((p.buffer.code, p.stage.name if p.stage else None, p.status,
                       p.recheck_stage.name if p.recheck_stage else None, p.quantity)
                      for p in StockPosition.query.filter(StockPosition.quantity > 0).all())


def held_id(app):
    with app.app_context():
        return StockPosition.query.filter_by(status='wstrzymane').one().id


def decide(client, pos_id, **data):
    return form_post(client, f'{KP}/kontrole/wstrzymane/{pos_id}/decyzja', **data)


@pytest.fixture
def held(client, app, users, w):
    """5 szt. Drzwi po Wycinaniu na wózku W1 (czeka na Gięcie); kontrola: 3 zgodne, 2 niezgodne."""
    produce(client, w, 5, 'A', 'W1', 'B')
    with app.app_context():
        cid = StageControl.query.one().id
    rate(client, cid, 5, 3, 2, {w['point']['A1']: ('OK', ''), w['point']['A2']: ('NG', 'zadziory')})
    assert pos_rows(app) == [('T7W1', 'T7 Gięcie', 'ok', None, 3), ('T7W1', 'T7 Gięcie', 'wstrzymane', 'T7 Wycinanie', 2)]
    return w


class TestRelease:
    def test_release_returns_quantity_to_circulation(self, client, app, held):
        res = decide(client, held_id(app), action='zwolnij', qty=1, note='uwaga niekrytyczna')
        assert 'Zwolniono: 1 szt.' in res.get_data(as_text=True)
        assert pos_rows(app) == [('T7W1', 'T7 Gięcie', 'ok', None, 4), ('T7W1', 'T7 Gięcie', 'wstrzymane', 'T7 Wycinanie', 1)]
        with app.app_context():
            mv = Movement.query.filter_by(kind='zwolnienie').one()
            assert (mv.quantity, mv.note, mv.user.username, mv.wait_stage.name) == (
                1, 'uwaga niekrytyczna', 'oper', 'T7 Gięcie')
        decide(client, held_id(app), action='zwolnij', qty=1)
        assert pos_rows(app) == [('T7W1', 'T7 Gięcie', 'ok', None, 5)]

    def test_quantity_validation(self, client, app, held):
        pid = held_id(app)
        for qty, text in ((0, 'większą od zera'), (3, 'tylko 2 szt.'), ('x', 'całkowitą')):
            assert text in decide(client, pid, action='zwolnij', qty=qty).get_data(as_text=True), qty
        assert 'Wybierz decyzję' in decide(client, pid, action='cokolwiek', qty=1).get_data(as_text=True)
        assert pos_rows(app)[1][4] == 2


class TestSendBack:
    def test_back_to_stage_and_cart_then_recheck_after_correction(self, client, app, held, w):
        pid = held_id(app)
        res = decide(client, pid, action='popraw', qty=2, stage_id=w['stage']['A'], buffer_code='t7 w2',
                     note='dociąć zadziory')
        assert 'Zwrócono do poprawki: 2 szt.' in res.get_data(as_text=True)
        assert pos_rows(app) == [('T7W1', 'T7 Gięcie', 'ok', None, 3),
                                 ('T7W2', 'T7 Wycinanie', 'do_poprawki', 'T7 Wycinanie', 2)]
        with app.app_context():
            mv = Movement.query.filter_by(kind='zwrot').one()
            assert (mv.from_buffer.code, mv.to_buffer.code, mv.note) == ('T7W1', 'T7W2', 'dociąć zadziory')
        # poprawia pracownik etapu: przejęcie jako poprawka, odstawienie wymusza ponowną kontrolę
        worker(client, w)
        none = api(client, '/stanowisko/przejecie', component_id=w['comp'], buffer_id=w['buf']['W2'],
                   stage_id=w['stage']['A'], quantity=2)                                 # bez znacznika poprawki
        assert none.status_code == 400
        res = api(client, '/stanowisko/przejecie', component_id=w['comp'], buffer_id=w['buf']['W2'],
                  stage_id=w['stage']['A'], recheck_stage_id=w['stage']['A'], quantity=2)
        assert res.status_code == 200
        wip = max(res.get_json()['wip'], key=lambda r: r['id'])
        assert wip['recheck_stage'] == 'T7 Wycinanie'
        out = api(client, '/stanowisko/przenies', wip_id=wip['id'], buffer_id=w['buf']['W3'], quantity=2,
                  wait_stage_id=w['stage']['B'])
        assert out.status_code == 200 and 'czeka na kontrolę' in out.get_json()['msg']
        boss(client)
        with app.app_context():
            ctrl = StageControl.query.filter_by(status='oczekuje').one()
            assert (ctrl.kind, ctrl.stage.name, ctrl.wait_stage.name, ctrl.quantity) == (
                'ponowna', 'T7 Wycinanie', 'T7 Gięcie', 2)
            cid = ctrl.id
        assert pos_rows(app)[-1] == ('T7W3', 'T7 Gięcie', 'do_kontroli', None, 2)
        rate(client, cid, 2, 2, 0, {w['point']['A1']: ('OK', ''), w['point']['A2']: ('OK', '')})
        assert ('T7W3', 'T7 Gięcie', 'ok', None, 2) in pos_rows(app)

    def test_second_ng_after_correction_is_held_again_with_failing_stage(self, client, app, held, w):
        decide(client, held_id(app), action='popraw', qty=2, stage_id=w['stage']['A'], buffer_code='T7W2', note='x')
        worker(client, w)
        res = api(client, '/stanowisko/przejecie', component_id=w['comp'], buffer_id=w['buf']['W2'],
                  stage_id=w['stage']['A'], recheck_stage_id=w['stage']['A'], quantity=2)
        wip = max(res.get_json()['wip'], key=lambda r: r['id'])['id']
        api(client, '/stanowisko/przenies', wip_id=wip, buffer_id=w['buf']['W2'], quantity=2,
            wait_stage_id=w['stage']['B'])
        boss(client)
        with app.app_context():
            cid = StageControl.query.filter_by(status='oczekuje').one().id
        rate(client, cid, 2, 0, 2, {w['point']['A1']: ('NG', ''), w['point']['A2']: ('OK', '')})
        assert ('T7W2', 'T7 Gięcie', 'wstrzymane', 'T7 Wycinanie', 2) in pos_rows(app)

    def test_revert_takeover_restores_correction_status(self, client, app, held, w):
        decide(client, held_id(app), action='popraw', qty=2, stage_id=w['stage']['A'], buffer_code='T7W2', note='x')
        worker(client, w)
        res = api(client, '/stanowisko/przejecie', component_id=w['comp'], buffer_id=w['buf']['W2'],
                  stage_id=w['stage']['A'], recheck_stage_id=w['stage']['A'], quantity=2)
        mid = res.get_json()['recent'][0]['id']
        out = client.post(f'{KP}/ruch/{mid}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert out.status_code == 200
        assert ('T7W2', 'T7 Wycinanie', 'do_poprawki', 'T7 Wycinanie', 2) in pos_rows(app)

    def test_validation(self, client, app, held, w):
        pid = held_id(app)
        cases = [
            (dict(stage_id=w['stage']['B'], buffer_code='T7W2', note='x'), 'na etap: T7 Wycinanie'),   # późniejszy niż etap NG
            (dict(stage_id=w['stage']['A'], buffer_code='NIEMA', note='x'), 'aktywny bufor'),
            (dict(stage_id=w['stage']['A'], buffer_code='T7W2', note=''), 'Opisz, co ma zostać poprawione'),
            (dict(stage_id=w['stage']['A'], buffer_code='T7W2', note='x', qty=9), 'tylko 2 szt.'),
        ]
        for extra, text in cases:
            data = {'action': 'popraw', 'qty': 1, **extra}
            assert text in decide(client, pid, **data).get_data(as_text=True), text
        with app.app_context():
            assert Movement.query.filter_by(kind='zwrot').count() == 0


class TestScrap:
    def test_scrap_archives_and_reopens_required_quantity(self, client, app, held, w):
        with app.app_context():
            comp = db.session.get(WorkOrderComponent, w['comp'])
            assert (stock.entered_qty(comp), stock.remaining_qty(comp)) == (5, 3)
        assert 'Podaj powód' in decide(client, held_id(app), action='zlomuj', qty=2, note='').get_data(as_text=True)
        res = decide(client, held_id(app), action='zlomuj', qty=2, note='pęknięta blacha')
        assert 'Zezłomowano: 2 szt.' in res.get_data(as_text=True)
        assert pos_rows(app) == [('T7W1', 'T7 Gięcie', 'ok', None, 3)]
        with app.app_context():
            r = ScrapRecord.query.one()
            assert (r.quantity, r.reason, r.stage.name, r.buffer.code, r.user.username) == (
                2, 'pęknięta blacha', 'T7 Wycinanie', 'T7W1', 'oper')
            assert Movement.query.filter_by(kind='zlomowanie').one().note == 'pęknięta blacha'
            comp = db.session.get(WorkOrderComponent, w['comp'])
            assert (stock.entered_qty(comp), stock.remaining_qty(comp)) == (3, 5)
        # nowy element wpisuje się od początku
        worker(client, w)
        ok = api(client, '/stanowisko/wpis', component_id=w['comp'], buffer_id=w['buf']['W1'], quantity=5,
                 stage_id=w['stage']['A'])
        assert ok.status_code == 200

    def test_product_cannot_be_scrapped(self, client, app, held, w):
        with app.app_context():
            from kp import flow
            product = flow._ensure_product_component(db.session.get(WorkOrder, w['order']))
            pos = stock._position(product, db.session.get(Buffer, w['buf']['W1']), 'wstrzymane', w['stage']['S'],
                                  w['stage']['S'])
            pos.quantity = 1
            db.session.commit()
            user = User.query.filter_by(username='oper').one()
            mv, err = decisions.scrap(pos, 1, user, 'x')
            assert mv is None and 'produktu nie można zezłomować' in err
            db.session.rollback()

    def test_archive_page_visible_to_everyone_with_filters(self, client, app, held, w):
        decide(client, held_id(app), action='zlomuj', qty=1, note='rysa na froncie')
        for user, pw in (('oper', 'Oper1234!'), ('kp7_operator', 'Rola1234!')):
            boss(client, user, pw)
            page = client.get(KP + '/zlom').get_data(as_text=True)
            assert 'T7-ZO-1' in page and 'rysa na froncie' in page and 'Drzwi' in page
        assert 'T7-ZO-1' in client.get(KP + '/zlom?q=rysa').get_data(as_text=True)
        assert 'T7-ZO-1' not in client.get(KP + '/zlom?q=nieistnieje').get_data(as_text=True)
        assert 'T7-ZO-1' in client.get(f'{KP}/zlom?stage={w["stage"]["A"]}').get_data(as_text=True)
        assert 'T7-ZO-1' not in client.get(f'{KP}/zlom?stage={w["stage"]["B"]}').get_data(as_text=True)

    def test_deleting_order_removes_scrap_records(self, client, app, held, w):
        decide(client, held_id(app), action='zlomuj', qty=1, note='x')
        boss(client, 'admin', 'Admin1234!')
        form_post(client, f'{KP}/{w["order"]}/usun')
        with app.app_context():
            assert ScrapRecord.query.count() == 0


class TestAccessAndViews:
    def test_only_controllers_decide_everyone_sees_list(self, client, app, held, w):
        pid = held_id(app)
        boss(client, 'kp7_operator', 'Rola1234!')
        page = client.get(KP + '/kontrole/wstrzymane').get_data(as_text=True)
        assert 'T7-ZO-1' in page and 'Zwolnij' not in page
        assert form_post(client, f'{KP}/kontrole/wstrzymane/{pid}/decyzja', action='zwolnij', qty=1).status_code == 403
        boss(client)
        page = client.get(KP + '/kontrole/wstrzymane').get_data(as_text=True)
        assert 'Zwolnij' in page and 'Do poprawki' in page and 'Zezłomuj' in page
        tabs = client.get(KP + '/bufory').get_data(as_text=True)
        assert 'Wstrzymane' in tabs and 'Złom' in tabs and 'badge-danger' in tabs

    def test_stale_position_is_handled(self, client, app, held):
        pid = held_id(app)
        decide(client, pid, action='zwolnij', qty=2)
        assert 'nie czeka już' in decide(client, pid, action='zwolnij', qty=1).get_data(as_text=True)


class TestAdHoc:
    def test_ad_hoc_control_moves_ok_quantity_to_queue(self, client, app, held, w):
        with app.app_context():
            pid = StockPosition.query.filter_by(status='ok').one().id
        res = form_post(client, f'{KP}/pozycja/{pid}/kontrola-dorazna', qty=2, stage_id=w['stage']['B'])
        assert 'Skierowano na kontrolę doraźną' in res.get_data(as_text=True)
        assert pos_rows(app)[0] == ('T7W1', 'T7 Gięcie', 'do_kontroli', None, 2)
        assert ('T7W1', 'T7 Gięcie', 'ok', None, 1) in pos_rows(app)
        with app.app_context():
            ctrl = StageControl.query.filter_by(kind='dorazna').one()
            assert (ctrl.stage.name, ctrl.wait_stage.name, ctrl.quantity, ctrl.status) == (
                'T7 Gięcie', 'T7 Gięcie', 2, 'oczekuje')
            cid = ctrl.id
        rate(client, cid, 2, 1, 1, {w['point']['B1']: ('NG', 'kąt')})
        rows = pos_rows(app)
        assert ('T7W1', 'T7 Gięcie', 'ok', None, 2) in rows
        assert ('T7W1', 'T7 Gięcie', 'wstrzymane', 'T7 Gięcie', 1) in rows

    def test_ad_hoc_validation_and_access(self, client, app, held, w):
        with app.app_context():
            pid = StockPosition.query.filter_by(status='ok').one().id
        assert 'Dostępne jest tylko 3 szt.' in form_post(
            client, f'{KP}/pozycja/{pid}/kontrola-dorazna', qty=9, stage_id=w['stage']['B']).get_data(as_text=True)
        assert 'aktywny etap' in form_post(
            client, f'{KP}/pozycja/{pid}/kontrola-dorazna', qty=1, stage_id=999999).get_data(as_text=True)
        boss(client, 'kp7_operator', 'Rola1234!')
        assert form_post(client, f'{KP}/pozycja/{pid}/kontrola-dorazna', qty=1,
                         stage_id=w['stage']['B']).status_code == 403
        with app.app_context():
            assert StageControl.query.filter_by(kind='dorazna').count() == 0

    def test_buffer_page_shows_ad_hoc_form_only_for_controllers(self, client, app, held, w):
        page = client.get(f'{KP}/bufory/{w["buf"]["W1"]}').get_data(as_text=True)
        assert 'Kontrola doraźna' in page and 'Skontroluj' in page
        boss(client, 'kp7_operator', 'Rola1234!')
        assert 'Skontroluj' not in client.get(f'{KP}/bufory/{w["buf"]["W1"]}').get_data(as_text=True)
