"""Testy etapu 5a: kontrole etapów (plan, status "do kontroli", ocena, QAR, blokada przejęcia)."""
import re

import pytest

from app import app as flask_app, db, _seed_stage_control_points
from models import (User, Alert, ProductionDepartment, DepartmentEmployee, ProductionStage, Machine, Buffer,
                    WorkOrder, WorkOrderComponent, WorkOrderComponentStage, StockPosition, Movement,
                    WorkInProgress, StageControl, StageControlResult, StageControlPoint, ProductTemplate,
                    ProductComponent, ProductComponentStage, ProductTemplateControl, WorkOrderControl,
                    QARReport, ControlPoint, AuditLog)
from test_app import login, _csrf
from kp import controls, flow, stock

KP = '/kontrolaprodukcji'
US = '/ustawienia-produkcji'


def api(client, path, **payload):
    return client.post(KP + path, json=payload, headers={'X-CSRF-Token': _csrf(client)})


def form_post(client, url, **data):
    data['_csrf_token'] = _csrf(client)
    return client.post(url, data=data, follow_redirects=True)


def badge(token):
    return 'PSH-DEP:' + token


@pytest.fixture(autouse=True)
def _clean(app):
    def wipe():
        with app.app_context():
            ids = [c.id for c in StageControl.query.all()]
            if ids:
                StageControlResult.query.filter(StageControlResult.control_id.in_(ids)).delete(
                    synchronize_session=False)
            for m in (StageControl, Movement, WorkInProgress, StockPosition, WorkOrderControl,
                      WorkOrderComponentStage, WorkOrderComponent, ProductTemplateControl,
                      ProductComponentStage, ProductComponent, ProductTemplate):
                m.query.delete()
            WorkOrder.query.filter(WorkOrder.zo_number.like('T5-%')).delete(synchronize_session=False)
            Buffer.query.filter(Buffer.code.like('T5%')).delete(synchronize_session=False)
            StageControlPoint.query.filter(StageControlPoint.title.like('T5 %')).delete(synchronize_session=False)
            ControlPoint.query.filter(ControlPoint.title.like('T5 %')).delete(synchronize_session=False)
            Machine.query.filter(Machine.name.like('T5 %')).delete(synchronize_session=False)
            ProductionStage.query.filter(ProductionStage.name.like('T5 %')).delete(synchronize_session=False)
            DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T5 %')).delete(
                synchronize_session=False)
            ProductionDepartment.query.filter(ProductionDepartment.name.like('T5 %')).delete(
                synchronize_session=False)
            QARReport.query.filter(QARReport.zo_number.like('T5-%')).delete(synchronize_session=False)
            Alert.query.filter(Alert.message.like('Do kontroli: ZO T5-%')).delete(synchronize_session=False)
            db.session.commit()
    wipe()
    yield
    wipe()


@pytest.fixture
def users(app):
    spec = {'station': ('kp5_station', 'produkcja'), 'operator': ('kp5_operator', 'operator'),
            'order': ('kp5_order', 'order')}
    with app.app_context():
        for _, (name, role) in spec.items():
            if not User.query.filter_by(username=name).first():
                u = User(username=name, email=f'{name}@test.pl', role=role)
                u.set_password('Rola1234!')
                db.session.add(u)
        db.session.commit()
    return {k: v[0] for k, v in spec.items()}


@pytest.fixture
def w(app):
    """A Wycinanie (element, kontrola OBOWIĄZKOWA, 2 punkty), B Gięcie (plan zlecenia, 1 punkt), K Kompletacja
    (bez kontroli), S Składanie (produkt), F Kontrola końcowa (produkt, bez punktów). Zlecenie T5-ZO-1 q=4:
    Drzwi (2 na sztukę, trasa A,B,K), Korpus (1 na sztukę, trasa A,K); plan kontroli zlecenia: B."""
    with app.app_context():
        depts = {k: ProductionDepartment(name=f'T5 Dział {k}', order=800 + i) for i, k in enumerate('ABKSF')}
        db.session.add_all(depts.values())
        db.session.flush()
        spec = {'A': ('Wycinanie', 'element'), 'B': ('Gięcie', 'element'), 'K': ('Kompletacja', 'element'),
                'S': ('Składanie', 'produkt'), 'F': ('Kontrola końcowa', 'produkt')}
        stages = {k: ProductionStage(name=f'T5 {name}', order=830 + i, level=level, department_id=depts[k].id,
                                     control_required=(k == 'A'))
                  for i, (k, (name, level)) in enumerate(spec.items())}
        db.session.add_all(stages.values())
        db.session.flush()
        pts = {'A1': StageControlPoint(stage_id=stages['A'].id, order=1, title='T5 Wymiary'),
               'A2': StageControlPoint(stage_id=stages['A'].id, order=2, title='T5 Krawędzie'),
               'B1': StageControlPoint(stage_id=stages['B'].id, order=1, title='T5 Kąty gięcia')}
        db.session.add_all(pts.values())
        bufs = {'A1': Buffer(code='T5A1', stage_id=stages['A'].id), 'A2': Buffer(code='T5A2', stage_id=stages['A'].id),
                'B1': Buffer(code='T5B1', stage_id=stages['B'].id), 'K1': Buffer(code='T5K1', stage_id=stages['K'].id),
                'S1': Buffer(code='T5S1', stage_id=stages['S'].id), 'F1': Buffer(code='T5F1', stage_id=stages['F'].id)}
        db.session.add_all(bufs.values())
        order = WorkOrder(zo_number='T5-ZO-1', product_name='PSH-T5', client='WEG', quantity=4,
                          created_by_id=User.query.filter_by(username='admin').one().id)
        db.session.add(order)
        db.session.flush()
        comps = {}
        for i, (name, per, req, route) in enumerate([('Drzwi', 2, 8, 'ABK'), ('Korpus', 1, 4, 'AK')], 1):
            c = WorkOrderComponent(order_id=order.id, name=name, per_unit=per, required_qty=req, position=i)
            db.session.add(c)
            db.session.flush()
            for k in route:
                db.session.add(WorkOrderComponentStage(component_id=c.id, stage_id=stages[k].id))
            comps[name] = c
        db.session.add(WorkOrderControl(order_id=order.id, stage_id=stages['B'].id))
        emps = {k: DepartmentEmployee(department_id=depts[k].id, name=f'T5 Pracownik {k}') for k in 'ABKSF'}
        db.session.add_all(emps.values())
        db.session.commit()
        return {'order': order.id, 'stage': {k: v.id for k, v in stages.items()},
                'point': {k: v.id for k, v in pts.items()}, 'buf': {k: v.id for k, v in bufs.items()},
                'comp': {k: v.id for k, v in comps.items()}, 'emp': {k: v.id for k, v in emps.items()},
                'token': {k: v.token for k, v in emps.items()}, 'dept': {k: v.id for k, v in depts.items()}}


def boss(client, user='oper', pw='Oper1234!'):
    client.get('/logout')
    login(client, user, pw)


def worker(client, w, key, account='kp5_station'):
    client.get('/logout')
    login(client, account, 'Rola1234!')
    assert api(client, '/stanowisko/skan', code=badge(w['token'][key])).status_code == 200


def hint(w, buf):
    """Etap, na który ilość czeka na buforze testowym (pole stage_id bufora to tylko wskazówka testów;
    w aplikacji bufor jest zwykłym wózkiem bez etapu)."""
    with flask_app.app_context():
        return db.session.get(Buffer, w['buf'][buf]).stage_id


def put(client, w, comp, buf, qty):
    """Wpis początkowy: ilość czeka na etap z wskazówki bufora."""
    res = api(client, '/stanowisko/wpis', component_id=w['comp'][comp], buffer_id=w['buf'][buf], quantity=qty,
              stage_id=hint(w, buf))
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def take(client, w, comp, buf, qty):
    return api(client, '/stanowisko/przejecie', component_id=w['comp'][comp], buffer_id=w['buf'][buf],
               stage_id=hint(w, buf), quantity=qty)


def produce(client, w, comp, src, qty, dst):
    """Wykonanie etapu: wpis na bufor `src` (czeka na jego etap), przejęcie etapu przez pracownika działu
    i odstawienie na bufor `dst` (ilość czeka na etap z wskazówki `dst`). Po wykonaniu etapu z kontrolą
    ilość trafia do kontroli. Kończy zalogowany jako kontroler (oper)."""
    boss(client)
    put(client, w, comp, src, qty)
    worker(client, w, src[0])
    res = take(client, w, comp, src, qty)
    assert res.status_code == 200, res.get_json()
    wip = max(res.get_json()['wip'], key=lambda r: r['id'])['id']
    out = api(client, '/stanowisko/przenies', wip_id=wip, buffer_id=w['buf'][dst], quantity=qty,
              wait_stage_id=hint(w, dst))
    assert out.status_code == 200, out.get_json()
    body = out.get_json()
    boss(client)
    return body


def states(app, comp=None):
    with app.app_context():
        q = StockPosition.query
        if comp:
            q = q.filter_by(component_id=comp)
        return sorted((p.component.name, p.buffer.code, p.status, p.quantity) for p in q.all())


def controls_of(app):
    with app.app_context():
        return [(c.id, c.component.name, c.buffer.code, c.kind, c.status, c.quantity, c.ok_qty, c.ng_qty)
                for c in StageControl.query.order_by(StageControl.id)]


def rate(client, control_id, expected, ok, ng, results=None, notes='', **extra):
    data = {'expected': expected, 'ok_qty': ok, 'ng_qty': ng, 'notes': notes}
    for pid, (res, pnotes) in (results or {}).items():
        data[f'result_{pid}'] = res
        data[f'pnotes_{pid}'] = pnotes
    data.update(extra)
    return form_post(client, f'{KP}/kontrole/{control_id}/ocen', **data)


# ── Wejście ilości na bufor etapu z kontrolą ──────────────────────────────────

class TestArrival:
    def test_mandatory_stage_puts_quantity_to_control(self, client, app, users, w):
        body = produce(client, w, 'Drzwi', 'A1', 5, 'B1')
        assert 'czeka na kontrolę' in body['msg']
        assert states(app) == [('Drzwi', 'T5B1', 'do_kontroli', 5)]
        c = controls_of(app)
        assert c == [(c[0][0], 'Drzwi', 'T5B1', 'obowiazkowa', 'oczekuje', 5, None, None)]
        with app.app_context():
            ctl = db.session.get(StageControl, c[0][0])
            assert (ctl.stage.name, ctl.wait_stage.name) == ('T5 Wycinanie', 'T5 Gięcie')
            mv = Movement.query.filter_by(kind='przeniesienie').one()
            assert mv.control_id == c[0][0]
        pos = body['order']['components'][0]['positions'][0]
        assert (pos['status'], pos['status_label']) == ('do_kontroli', 'czeka na kontrolę')

    def test_plan_stage_and_uncontrolled_stage(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'B1', 3, 'K1')           # wykonany etap w planie zlecenia
        produce(client, w, 'Drzwi', 'K1', 2, 'S1')           # wykonany etap bez kontroli
        assert states(app) == [('Drzwi', 'T5K1', 'do_kontroli', 3), ('Drzwi', 'T5S1', 'ok', 2)]
        assert [(c[2], c[3]) for c in controls_of(app)] == [('T5K1', 'plan')]

    def test_batches_are_merged_per_component_and_buffer(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 3, 'B1')
        produce(client, w, 'Drzwi', 'A1', 2, 'B1')           # ta sama kontrola, więcej sztuk
        produce(client, w, 'Korpus', 'A1', 1, 'K1')          # inny element: osobna kontrola
        produce(client, w, 'Drzwi', 'A1', 1, 'K1')           # inny bufor i etap oczekiwania: osobna kontrola
        got = controls_of(app)
        assert [(c[1], c[2], c[5]) for c in got] == [('Drzwi', 'T5B1', 5), ('Korpus', 'T5K1', 1), ('Drzwi', 'T5K1', 1)]
        with app.app_context():
            # alert tylko przy utworzeniu kontroli, nie przy scalaniu
            n_admin = Alert.query.join(User, User.id == Alert.recipient_id).filter(
                User.username == 'admin', Alert.message.like('Do kontroli: ZO T5-ZO-1%')).count()
            assert n_admin == 3

    def test_alerts_go_to_controllers_and_admins_only(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 2, 'B1')
        with app.app_context():
            names = {a.recipient.username for a in Alert.query.filter(Alert.message.like('Do kontroli: ZO T5-%'))}
            assert 'admin' in names and 'oper' in names
            assert not names & {'kp5_station', 'kp5_operator', 'kp5_order'}
            msg = Alert.query.filter(Alert.message.like('Do kontroli: ZO T5-%')).first().message
            assert 'Drzwi 2 szt.' in msg and 'T5B1' in msg and 'po etapie T5 Wycinanie' in msg

    def test_transfer_into_controlled_stage_waits_for_control(self, client, app, users, w):
        with app.app_context():
            db.session.add(WorkOrderControl(order_id=w['order'], stage_id=w['stage']['K']))
            db.session.commit()
        out = produce(client, w, 'Drzwi', 'K1', 4, 'S1')
        assert 'czeka na kontrolę' in out['msg']
        assert states(app) == [('Drzwi', 'T5S1', 'do_kontroli', 4)]
        with app.app_context():
            assert Movement.query.filter_by(kind='przeniesienie').one().control_id is not None

    def test_order_plan_is_copied_from_template_and_editable(self, client, app, users, w):
        with app.app_context():
            tpl = ProductTemplate(name='T5SZAB')
            db.session.add(tpl)
            db.session.flush()
            c = ProductComponent(template_id=tpl.id, name='Element', position=1)
            db.session.add(c)
            db.session.flush()
            db.session.add(ProductComponentStage(component_id=c.id, stage_id=w['stage']['K']))
            db.session.add(ProductTemplateControl(template_id=tpl.id, stage_id=w['stage']['K']))
            db.session.commit()
            tpl_id = tpl.id
        boss(client)
        body = client.post(KP + '/z-qr', json={'p': 'T5SZAB 100', 'c': 'X', 'o': 'T5-ZO-PLAN', 'q': 2},
                           headers={'X-CSRF-Token': _csrf(client)}).get_json()
        comp_id = str(body['components'][0]['id'])
        client.post(KP + '/utworz', json={**body['order'], 'per_unit': {comp_id: 1}},
                    headers={'X-CSRF-Token': _csrf(client)})
        with app.app_context():
            order = WorkOrder.query.filter_by(zo_number='T5-ZO-PLAN').one()
            assert [c.stage_id for c in order.control_plan] == [w['stage']['K']]
            oid = order.id
        form_post(client, f'{KP}/{oid}/plan', stage_ids=[w['stage']['B'], w['stage']['S']])
        with app.app_context():
            assert sorted(c.stage_id for c in db.session.get(WorkOrder, oid).control_plan) == sorted(
                [w['stage']['B'], w['stage']['S']])
        client.get('/logout')
        login(client, 'kp5_operator', 'Rola1234!')
        assert form_post(client, f'{KP}/{oid}/plan', stage_ids=[]).status_code == 403

    def test_template_plan_save_and_copy(self, client, app, users, w):
        with app.app_context():
            tpl = ProductTemplate(name='T5SZAB2')
            db.session.add(tpl)
            db.session.flush()
            tpl_id = tpl.id
            db.session.commit()
        boss(client)
        form_post(client, f'{US}/produkty/{tpl_id}/plan', stage_ids=[w['stage']['A'], w['stage']['B']])
        with app.app_context():
            assert sorted(c.stage_id for c in db.session.get(ProductTemplate, tpl_id).control_plan) == sorted(
                [w['stage']['A'], w['stage']['B']])
        form_post(client, f'{US}/produkty/{tpl_id}/kopiuj')
        with app.app_context():
            copy = ProductTemplate.query.filter(ProductTemplate.name.like('T5SZAB2 (kopia)%')).one()
            assert sorted(c.stage_id for c in copy.control_plan) == sorted([w['stage']['A'], w['stage']['B']])
        page = client.get(f'{US}/produkty/{tpl_id}').get_data(as_text=True)
        assert 'Domyślny plan kontroli' in page
        client.get('/logout')
        login(client, 'kp5_order', 'Rola1234!')
        assert form_post(client, f'{US}/produkty/{tpl_id}/plan', stage_ids=[]).status_code == 403


# ── Blokada przejęcia ─────────────────────────────────────────────────────────

class TestBlockedTakeover:
    def test_pending_quantity_cannot_be_taken(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 5, 'B1')
        worker(client, w, 'B')
        res = take(client, w, 'Drzwi', 'B1', 1)
        assert res.status_code == 400
        err = res.get_json()['error']
        assert 'nie ma „Drzwi” gotowych do przejęcia' in err and '5 szt. czeka na kontrolę' in err
        assert states(app) == [('Drzwi', 'T5B1', 'do_kontroli', 5)]

    def test_only_ok_part_is_available_and_message_mentions_rest(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 5, 'B1')
        control_id = controls_of(app)[0][0]
        rate(client, control_id, 5, 3, 2, {w['point']['A1']: ('OK', ''), w['point']['A2']: ('NG', 'zadziory')})
        worker(client, w, 'B')
        res = take(client, w, 'Drzwi', 'B1', 4)
        assert res.status_code == 400 and 'tylko 3 szt.' in res.get_json()['error']
        assert '2 szt. jest wstrzymane (niezgodne, czeka na decyzję kontrolera)' in res.get_json()['error']
        assert take(client, w, 'Drzwi', 'B1', 3).status_code == 200

    def test_assembly_uses_only_ready_elements(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 4, 'S1')           # po etapie z kontrolą obowiązkową
        produce(client, w, 'Korpus', 'A1', 2, 'S1')
        worker(client, w, 'S')
        res = api(client, '/stanowisko/zloz', order_id=w['order'], buffer_id=w['buf']['S1'], quantity=1)
        assert res.status_code == 400 and 'brakuje elementów' in res.get_json()['error']


# ── Ocena kontroli ────────────────────────────────────────────────────────────

class TestEvaluation:
    def _pending(self, client, w, qty=5, comp='Drzwi', buf='A1'):
        produce(client, w, comp, buf, qty, 'B1' if comp == 'Drzwi' else 'K1')

    def test_all_ok_releases_quantity(self, client, app, users, w):
        self._pending(client, w)
        cid = controls_of(app)[0][0]
        res = rate(client, cid, 5, 5, 0, {w['point']['A1']: ('OK', ''), w['point']['A2']: ('OK', '')}, 'wszystko dobrze')
        assert 'Kontrola zakończona: 5 szt. zgodnych, 0 szt. niezgodnych' in res.get_data(as_text=True)
        assert states(app) == [('Drzwi', 'T5B1', 'ok', 5)]
        with app.app_context():
            c = db.session.get(StageControl, cid)
            assert (c.status, c.ok_qty, c.ng_qty, c.result, c.notes) == ('zakonczona', 5, 0, 'OK', 'wszystko dobrze')
            assert c.controller.username == 'oper' and c.completed_at is not None
            assert [(r.title, r.result) for r in c.results] == [('T5 Wymiary', 'OK'), ('T5 Krawędzie', 'OK')]
            assert QARReport.query.filter(QARReport.zo_number == 'T5-ZO-1').count() == 0
            mv = Movement.query.filter_by(kind='kontrola').one()
            assert (mv.quantity, mv.control_id, mv.to_buffer_id) == (5, cid, w['buf']['B1'])
            log = AuditLog.query.filter_by(action='kp_control_done').order_by(AuditLog.id.desc()).first()
            assert 'zgodne=5' in log.detail
        worker(client, w, 'B')
        assert take(client, w, 'Drzwi', 'B1', 5).status_code == 200

    def test_partial_ng_creates_qar_per_ng_point_and_holds_quantity(self, client, app, users, w):
        self._pending(client, w)
        cid = controls_of(app)[0][0]
        res = rate(client, cid, 5, 3, 2, {w['point']['A1']: ('NG', 'za duży wymiar'), w['point']['A2']: ('NG', 'zadziory')})
        text = res.get_data(as_text=True)
        assert 'Utworzono raporty QAR' in text
        assert states(app) == [('Drzwi', 'T5B1', 'ok', 3), ('Drzwi', 'T5B1', 'wstrzymane', 2)]
        with app.app_context():
            c = db.session.get(StageControl, cid)
            assert (c.ok_qty, c.ng_qty, c.result) == (3, 2, 'częściowo')
            qars = QARReport.query.filter(QARReport.zo_number == 'T5-ZO-1').order_by(QARReport.id).all()
            assert len(qars) == 2
            assert 'T5 Wymiary' in qars[0].title and 'za duży wymiar' in qars[0].description
            assert 'niezgodnych 2 z 5 szt.' in qars[0].description and qars[0].location == 'T5 Wycinanie'
            assert {r.qar_report_id for r in c.results} == {q.id for q in qars}

    def test_only_one_ng_point_makes_one_qar(self, client, app, users, w):
        self._pending(client, w)
        cid = controls_of(app)[0][0]
        rate(client, cid, 5, 0, 5, {w['point']['A1']: ('OK', ''), w['point']['A2']: ('NG', '')})
        with app.app_context():
            assert QARReport.query.filter(QARReport.zo_number == 'T5-ZO-1').count() == 1
            assert db.session.get(StageControl, cid).result == 'NG'
        assert states(app) == [('Drzwi', 'T5B1', 'wstrzymane', 5)]

    def test_validation_rules(self, client, app, users, w):
        self._pending(client, w)
        cid = controls_of(app)[0][0]
        ok_pts = {w['point']['A1']: ('OK', ''), w['point']['A2']: ('OK', '')}
        ng_pts = {w['point']['A1']: ('OK', ''), w['point']['A2']: ('NG', '')}
        cases = [
            (5, 3, 3, ok_pts, 'musi być równa'),                         # suma
            (5, 5, 0, {w['point']['A1']: ('OK', '')}, 'Oceń punkt kontrolny: T5 Krawędzie'),
            (5, 3, 2, ok_pts, 'Wskaż, który punkt'),                     # NG bez punktu NG
            (5, 5, 0, ng_pts, 'wymaga niezgodnej ilości'),               # punkt NG bez ilości NG
            (5, -1, 6, ok_pts, 'nieujemnymi'),
            (5, 'x', 5, ok_pts, 'nieujemnymi'),
            (9, 9, 0, ok_pts, 'zmniejszyła się'),                        # widziana ilość większa niż w kontroli
        ]
        for expected, ok, ng, pts, text in cases:
            res = rate(client, cid, expected, ok, ng, pts)
            assert text in res.get_data(as_text=True), (expected, ok, ng, text)
        assert controls_of(app)[0][4] == 'oczekuje' and states(app) == [('Drzwi', 'T5B1', 'do_kontroli', 5)]
        with app.app_context():
            assert StageControlResult.query.count() == 0 and Movement.query.filter_by(kind='kontrola').count() == 0

    def test_no_points_stage_uses_general_qar(self, client, app, users, w):
        with app.app_context():
            db.session.add(WorkOrderControl(order_id=w['order'], stage_id=w['stage']['K']))
            db.session.commit()
        produce(client, w, 'Korpus', 'K1', 4, 'S1')
        cid = controls_of(app)[0][0]
        res = client.get(f'{KP}/kontrole/{cid}').get_data(as_text=True)
        assert 'Ten etap nie ma punktów kontrolnych' in res
        rate(client, cid, 4, 3, 1, notes='pęknięcie')
        with app.app_context():
            qars = QARReport.query.filter(QARReport.zo_number == 'T5-ZO-1').all()
            assert len(qars) == 1 and 'pęknięcie' in qars[0].description and 'T5 Kompletacja' in qars[0].title
        assert states(app) == [('Korpus', 'T5S1', 'ok', 3), ('Korpus', 'T5S1', 'wstrzymane', 1)]

    def test_new_batch_after_seen_quantity_stays_pending(self, client, app, users, w):
        self._pending(client, w, 5)
        cid = controls_of(app)[0][0]
        produce(client, w, 'Drzwi', 'A1', 3, 'B1')                 # po tym jak kontroler zobaczył 5
        rate(client, cid, 5, 5, 0, {w['point']['A1']: ('OK', ''), w['point']['A2']: ('OK', '')})
        got = controls_of(app)
        assert (got[0][0], got[0][4], got[0][5]) == (cid, 'oczekuje', 3)        # reszta czeka w oryginalnej kontroli
        assert got[1][4:8] == ('zakonczona', 5, 5, 0)
        assert states(app) == [('Drzwi', 'T5B1', 'do_kontroli', 3), ('Drzwi', 'T5B1', 'ok', 5)]

    def test_only_controllers_can_rate(self, client, app, users, w):
        self._pending(client, w)
        cid = controls_of(app)[0][0]
        for user in ('kp5_station', 'kp5_operator', 'kp5_order'):
            client.get('/logout')
            login(client, user, 'Rola1234!')
            assert rate(client, cid, 5, 5, 0).status_code == 403
        assert controls_of(app)[0][4] == 'oczekuje'

    def test_rate_twice_second_is_rejected(self, client, app, users, w):
        self._pending(client, w)
        cid = controls_of(app)[0][0]
        pts = {w['point']['A1']: ('OK', ''), w['point']['A2']: ('OK', '')}
        rate(client, cid, 5, 5, 0, pts)
        res = rate(client, cid, 5, 5, 0, pts)
        assert 'nie czeka już na ocenę' in res.get_data(as_text=True)
        with app.app_context():
            assert Movement.query.filter_by(kind='kontrola').count() == 1


# ── Wycofanie wpisu z kontrolą ────────────────────────────────────────────────

class TestRevertWithControl:
    def test_revert_cancels_or_reduces_pending_control(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 3, 'B1')
        produce(client, w, 'Drzwi', 'A1', 2, 'B1')
        with app.app_context():
            ids = [m.id for m in Movement.query.filter_by(kind='przeniesienie').order_by(Movement.id)]
        res = client.post(f'{KP}/ruch/{ids[1]}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert res.status_code == 200
        assert controls_of(app)[0][4:6] == ('oczekuje', 3) and states(app) == [('Drzwi', 'T5B1', 'do_kontroli', 3)]
        res = client.post(f'{KP}/ruch/{ids[0]}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert res.status_code == 200
        assert controls_of(app)[0][4:6] == ('anulowana', 0) and states(app) == []
        assert 'Czekają <strong>0' in client.get(KP + '/kontrole').get_data(as_text=True).replace('\n', '')

    def test_revert_blocked_after_control_evaluated(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 4, 'B1')
        cid = controls_of(app)[0][0]
        rate(client, cid, 4, 4, 0, {w['point']['A1']: ('OK', ''), w['point']['A2']: ('OK', '')})
        with app.app_context():
            mid = Movement.query.filter_by(kind='przeniesienie').one().id
        res = client.post(f'{KP}/ruch/{mid}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert res.status_code == 400 and 'już skontrolowana' in res.get_json()['error']

    def test_revert_transfer_into_controlled_stage_restores_wip(self, client, app, users, w):
        with app.app_context():
            db.session.add(WorkOrderControl(order_id=w['order'], stage_id=w['stage']['K']))
            db.session.commit()
        produce(client, w, 'Drzwi', 'K1', 2, 'S1')
        with app.app_context():
            mid = Movement.query.filter_by(kind='przeniesienie').one().id
        res = client.post(f'{KP}/ruch/{mid}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})
        assert res.status_code == 200
        assert states(app) == [] and controls_of(app)[0][4] == 'anulowana'
        with app.app_context():
            assert [(x.stage.name, x.quantity) for x in WorkInProgress.query.all()] == [('T5 Kompletacja', 2)]


# ── Kolejka, szczegóły i punkty etapów ────────────────────────────────────────

class TestQueueAndPoints:
    def test_queue_visible_to_everyone_with_counts_and_filters(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 5, 'B1')           # kontrola po A: bufor T5B1
        produce(client, w, 'Drzwi', 'B1', 2, 'K1')           # kontrola po B (plan): bufor T5K1
        cid = controls_of(app)[1][0]
        rate(client, cid, 2, 2, 0, {w['point']['B1']: ('OK', '')})
        assert client.get(KP + '/kontrole').status_code == 200
        client.get('/logout')
        assert client.get(KP + '/kontrole').status_code in (302, 401)
        for user, pw in (('oper', 'Oper1234!'), ('admin', 'Admin1234!'), ('kp5_station', 'Rola1234!'),
                         ('kp5_operator', 'Rola1234!'), ('kp5_order', 'Rola1234!')):
            client.get('/logout')
            login(client, user, pw)
            page = client.get(KP + '/kontrole').get_data(as_text=True)
            assert 'T5-ZO-1' in page and 'T5B1' in page and 'T5K1' not in page, user     # domyślnie czekające
        done = client.get(KP + '/kontrole?s=zakonczona').get_data(as_text=True)
        assert 'T5K1' in done and 'T5B1' not in done
        assert 'T5B1' in client.get(KP + '/kontrole?s=all').get_data(as_text=True)
        only_a = client.get(f'{KP}/kontrole?s=all&stage={w["stage"]["A"]}').get_data(as_text=True)
        assert 'T5B1' in only_a and 'T5K1' not in only_a
        assert 'T5B1' in client.get(KP + '/kontrole?q=weg').get_data(as_text=True)
        assert 'T5B1' not in client.get(KP + '/kontrole?q=nieistnieje').get_data(as_text=True)

    def test_detail_form_only_for_controllers_and_shows_results_later(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 5, 'B1')
        cid = controls_of(app)[0][0]
        page = client.get(f'{KP}/kontrole/{cid}').get_data(as_text=True)
        assert 'Zatwierdź kontrolę' in page and 'T5 Wymiary' in page and 'T5 Krawędzie' in page
        client.get('/logout')
        login(client, 'kp5_operator', 'Rola1234!')
        page = client.get(f'{KP}/kontrole/{cid}').get_data(as_text=True)
        assert 'Zatwierdź kontrolę' not in page and 'czeka na ocenę' in page
        boss(client)
        rate(client, cid, 5, 4, 1, {w['point']['A1']: ('OK', ''), w['point']['A2']: ('NG', 'rysa')})
        page = client.get(f'{KP}/kontrole/{cid}').get_data(as_text=True)
        assert 'zgodne 4 szt., niezgodne 1 szt. z 5' in page and 'rysa' in page and 'QAR-' in page

    def test_pending_badge_in_tabs_and_order_page_sections(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 2, 'B1')
        tabs = client.get(KP + '/bufory').get_data(as_text=True)
        compact = re.sub(r'\s+', '', tabs)
        assert 'Kontrole' in tabs and 'badge-warning">1</span>' in compact
        page = client.get(f'{KP}/{w["order"]}').get_data(as_text=True)
        assert 'Plan kontroli' in page and 'czeka' in page and 'T5 Gięcie' in page
        assert 'czeka na kontrolę' in page                                    # status w położeniu elementu
        buf = client.get(f'{KP}/bufory/{w["buf"]["B1"]}').get_data(as_text=True)
        assert 'czeka na kontrolę' in buf

    def test_stage_points_crud_and_access(self, client, app, users, w):
        boss(client)
        sid = w['stage']['K']
        assert 'Brak punktów' in client.get(f'{US}/etapy/{sid}/punkty').get_data(as_text=True)
        form_post(client, f'{US}/etapy/{sid}/punkty/nowy', title='T5 Czystość', description='sprawdź wizualnie')
        form_post(client, f'{US}/etapy/{sid}/punkty/nowy', title='T5 Opis')
        form_post(client, f'{US}/etapy/{sid}/punkty/nowy', title='  ')
        with app.app_context():
            pts = StageControlPoint.query.filter_by(stage_id=sid).order_by(StageControlPoint.order).all()
            assert [p.title for p in pts] == ['T5 Czystość', 'T5 Opis']
            first, second = pts[0].id, pts[1].id
        form_post(client, f'{US}/punkty/{second}/przesun/up')
        form_post(client, f'{US}/punkty/{first}/zapisz', title='T5 Czystość 2', description='', is_active='')
        with app.app_context():
            assert [p.title for p in StageControlPoint.query.filter_by(stage_id=sid).order_by(StageControlPoint.order)] == [
                'T5 Opis', 'T5 Czystość 2']
            assert db.session.get(StageControlPoint, first).is_active is False
        assert controls.active_points(sid)[0].title == 'T5 Opis' and len(controls.active_points(sid)) == 1
        form_post(client, f'{US}/punkty/{second}/usun')
        with app.app_context():
            assert db.session.get(StageControlPoint, second) is None
        assert 'Punkty (' in client.get(US + '/etapy').get_data(as_text=True)
        client.get('/logout')
        login(client, 'kp5_order', 'Rola1234!')
        assert client.get(f'{US}/etapy/{sid}/punkty').status_code == 403
        assert form_post(client, f'{US}/etapy/{sid}/punkty/nowy', title='x').status_code == 403

    def test_deleted_point_keeps_title_in_finished_control(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 2, 'B1')
        cid = controls_of(app)[0][0]
        rate(client, cid, 2, 2, 0, {w['point']['A1']: ('OK', ''), w['point']['A2']: ('OK', '')})
        form_post(client, f'{US}/punkty/{w["point"]["A1"]}/usun')
        assert 'T5 Wymiary' in client.get(f'{KP}/kontrole/{cid}').get_data(as_text=True)

    def test_seed_copies_old_points_to_final_control_stage_once(self, client, app, users, w):
        with app.app_context():
            stage = db.session.get(ProductionStage, w['stage']['F'])
            stage.name = 'Kontrola końcowa'
            ControlPoint.query.update({'is_active': False})
            db.session.add_all([ControlPoint(title='T5 Stary 1', order=1), ControlPoint(title='T5 Stary 2', order=2),
                                ControlPoint(title='T5 Stary wył.', order=3, is_active=False)])
            db.session.commit()
            existing = StageControlPoint.query.count()
            StageControlPoint.query.delete()
            _seed_stage_control_points()
            db.session.commit()
            got = [(p.stage_id, p.title) for p in StageControlPoint.query.order_by(StageControlPoint.order)]
            assert got == [(w['stage']['F'], 'T5 Stary 1'), (w['stage']['F'], 'T5 Stary 2')]
            _seed_stage_control_points()                    # idempotentnie
            db.session.commit()
            assert StageControlPoint.query.count() == 2
            stage.name = 'T5 Kontrola końcowa'
            db.session.commit()

    def test_deleting_order_removes_controls_but_keeps_qar(self, client, app, users, w):
        produce(client, w, 'Drzwi', 'A1', 3, 'B1')
        cid = controls_of(app)[0][0]
        rate(client, cid, 3, 2, 1, {w['point']['A1']: ('NG', ''), w['point']['A2']: ('OK', '')})
        boss(client, 'admin', 'Admin1234!')
        form_post(client, f'{KP}/{w["order"]}/usun')
        with app.app_context():
            assert StageControl.query.count() == 0 and StageControlResult.query.count() == 0
            assert WorkOrderControl.query.count() == 0
            assert QARReport.query.filter(QARReport.zo_number == 'T5-ZO-1').count() == 1
