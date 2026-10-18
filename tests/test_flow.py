"""Testy etapu 4: przejęcie etapu, wpis po etapie, składanie, pakowanie i wycofanie ruchów."""
import pytest

from app import app as flask_app, db
from models import (User, ProductionDepartment, DepartmentEmployee, ProductionStage, Machine, Buffer,
                    WorkOrder, WorkOrderComponent, WorkOrderComponentStage, StockPosition, Movement,
                    WorkInProgress)
from test_app import login, _csrf
from kp import stock, flow

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
            for m in (Movement, WorkInProgress, StockPosition, WorkOrderComponentStage, WorkOrderComponent):
                m.query.delete()
            WorkOrder.query.filter(WorkOrder.zo_number.like('T4-%')).delete(synchronize_session=False)
            Buffer.query.filter(Buffer.code.like('T4%')).delete(synchronize_session=False)
            Machine.query.filter(Machine.name.like('T4 %')).delete(synchronize_session=False)
            ProductionStage.query.filter(ProductionStage.name.like('T4 %')).delete(synchronize_session=False)
            DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T4 %')).delete(
                synchronize_session=False)
            ProductionDepartment.query.filter(ProductionDepartment.name.like('T4 %')).delete(
                synchronize_session=False)
            db.session.commit()
    wipe()
    yield
    wipe()


@pytest.fixture
def users(app):
    spec = {'station': ('kp4_station', 'produkcja'), 'operator': ('kp4_operator', 'operator')}
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
    """Etapy: A Wycinanie (element, 2 maszyny), B Gięcie, K Kompletacja (element), S Składanie, F Kontrola
    końcowa, P Pakowanie (produkt). Zlecenie T4-ZO-1 q=4: Drzwi (2 na sztukę, trasa A,B,K), Korpus
    (1 na sztukę, trasa A,K). Pracownicy po jednym w dziale każdego etapu oraz z obcego działu."""
    with app.app_context():
        depts = {k: ProductionDepartment(name=f'T4 Dział {k}', order=900 + i)
                 for i, k in enumerate('ABKSFPX')}
        db.session.add_all(depts.values())
        db.session.flush()
        spec = {'A': ('Wycinanie', 'element'), 'B': ('Gięcie', 'element'), 'K': ('Kompletacja', 'element'),
                'S': ('Składanie', 'produkt'), 'F': ('Kontrola końcowa', 'produkt'),
                'P': ('Pakowanie', 'produkt')}
        stages = {}
        for i, (k, (name, level)) in enumerate(spec.items()):
            stages[k] = ProductionStage(name=f'T4 {name}', order=920 + i, level=level,
                                        department_id=depts[k].id)
        db.session.add_all(stages.values())
        db.session.flush()
        machines = {'L1': Machine(stage_id=stages['A'].id, name='T4 Laser 1'),
                    'L2': Machine(stage_id=stages['A'].id, name='T4 Laser 2')}
        db.session.add_all(machines.values())
        bufs = {'A1': Buffer(code='T4A1', stage_id=stages['A'].id), 'A2': Buffer(code='T4A2', stage_id=stages['A'].id),
                'B1': Buffer(code='T4B1', stage_id=stages['B'].id), 'K1': Buffer(code='T4K1', stage_id=stages['K'].id),
                'S1': Buffer(code='T4S1', stage_id=stages['S'].id), 'S2': Buffer(code='T4S2', stage_id=stages['S'].id),
                'F1': Buffer(code='T4F1', stage_id=stages['F'].id), 'P1': Buffer(code='T4P1', stage_id=stages['P'].id)}
        db.session.add_all(bufs.values())
        order = WorkOrder(zo_number='T4-ZO-1', product_name='PSH-T4', client='WEG', quantity=4,
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
        emps = {k: DepartmentEmployee(department_id=depts[k].id, name=f'T4 Pracownik {k}') for k in 'ABKSFPX'}
        db.session.add_all(emps.values())
        db.session.commit()
        return {'order': order.id,
                'stage': {k: v.id for k, v in stages.items()}, 'dept': {k: v.id for k, v in depts.items()},
                'buf': {k: v.id for k, v in bufs.items()}, 'comp': {k: v.id for k, v in comps.items()},
                'machine': {k: v.id for k, v in machines.items()},
                'emp': {k: v.id for k, v in emps.items()}, 'token': {k: v.token for k, v in emps.items()}}


def who(client, w, key):
    """Zalogowane konto stanowiska z plakietką pracownika działu `key`."""
    client.get('/logout')
    login(client, 'kp4_station', 'Rola1234!')
    assert api(client, '/stanowisko/skan', code=badge(w['token'][key])).status_code == 200


def switch(client, w, key):
    assert api(client, '/stanowisko/skan', code=badge(w['token'][key])).status_code == 200


def boss(client, user='oper', pw='Oper1234!'):
    client.get('/logout')
    login(client, user, pw)


def hint(w, buf):
    """Etap, na który ilość czeka na buforze testowym (pole stage_id bufora to tylko wskazówka testów;
    w aplikacji bufor jest zwykłym wózkiem bez etapu)."""
    with flask_app.app_context():
        return db.session.get(Buffer, w['buf'][buf]).stage_id


def put(client, w, comp, buf, qty):
    """Wpis początkowy na bufor."""
    res = api(client, '/stanowisko/wpis', component_id=w['comp'][comp], buffer_id=w['buf'][buf], quantity=qty,
              stage_id=hint(w, buf))
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def take(client, w, comp, buf, qty, machine=None):
    return api(client, '/stanowisko/przejecie', component_id=w['comp'][comp], buffer_id=w['buf'][buf],
               stage_id=hint(w, buf), quantity=qty, machine_id=w['machine'][machine] if machine else None)


def move(client, wip_id, w, buf, qty):
    return api(client, '/stanowisko/przenies', wip_id=wip_id, buffer_id=w['buf'][buf], quantity=qty,
               wait_stage_id=hint(w, buf))


def assemble(client, w, buf, qty, machine=None):
    return api(client, '/stanowisko/zloz', order_id=w['order'], buffer_id=w['buf'][buf], quantity=qty)


def revert(client, movement_id):
    return client.post(f'{KP}/ruch/{movement_id}/cofnij', json={}, headers={'X-CSRF-Token': _csrf(client)})


def newest(res):
    """Id najnowszej pozycji w realizacji z odpowiedzi (lista zawiera też starsze pozycje)."""
    return max(res.get_json()['wip'], key=lambda r: r['id'])['id']


def wips(app):
    with app.app_context():
        return [(x.component.name, x.stage.name, x.quantity) for x in WorkInProgress.query.order_by(WorkInProgress.id)]


def stock_of(app, comp_id=None):
    with app.app_context():
        q = StockPosition.query
        if comp_id:
            q = q.filter_by(component_id=comp_id)
        return sorted((p.component.name, p.buffer.code, p.quantity) for p in q.all())


# ── Trasy i etapy docelowe ────────────────────────────────────────────────────

class TestRoutes:
    def test_target_stages_for_elements_and_product(self, app, w):
        with app.app_context():
            names = lambda lst: [s.name for s in lst]
            drzwi = db.session.get(WorkOrderComponent, w['comp']['Drzwi'])
            korpus = db.session.get(WorkOrderComponent, w['comp']['Korpus'])
            st = lambda k: db.session.get(ProductionStage, w['stage'][k])
            assert names(stock.target_stages(drzwi, st('A'))) == ['T4 Gięcie', 'T4 Kompletacja', 'T4 Składanie']
            assert names(stock.target_stages(drzwi, st('K'))) == ['T4 Składanie']          # po trasie składanie
            assert names(stock.target_stages(korpus, st('A'))) == ['T4 Kompletacja', 'T4 Składanie']
            assert stock.target_stages(drzwi, st('S')) == []                                  # element nie idzie dalej
            product = flow._ensure_product_component(db.session.get(WorkOrder, w['order']))
            assert names(stock.target_stages(product, st('S'))) == ['T4 Kontrola końcowa', 'T4 Pakowanie']
            assert names(stock.target_stages(product, st('F'))) == ['T4 Pakowanie']
            assert stock.target_stages(product, st('P')) == []
            assert stock.first_product_stage().name == 'T4 Składanie'
            assert stock.last_product_stage().name == 'T4 Pakowanie'
            assert w['stage']['S'] in stock.entry_stage_ids(drzwi)
            assert stock.entry_stage_ids(product) == set()
            db.session.rollback()


# ── Przejęcie etapu ───────────────────────────────────────────────────────────

class TestTakeover:
    def test_takeover_moves_quantity_to_work_in_progress(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 5)
        res = take(client, w, 'Drzwi', 'A1', 3, machine='L1')
        body = res.get_json()
        assert res.status_code == 200 and body['ok'] and 'T4 Laser 1' in body['msg']
        assert stock_of(app) == [('Drzwi', 'T4A1', 2)]
        assert wips(app) == [('Drzwi', 'T4 Wycinanie', 3)]
        row = body['wip'][0]
        assert (row['quantity'], row['machine'], row['employee']) == (3, 'T4 Laser 1', 'T4 Pracownik A')
        assert [t['name'] for t in row['targets']] == ['T4 Gięcie', 'T4 Kompletacja', 'T4 Składanie']
        with app.app_context():
            mv = Movement.query.filter_by(kind='przejecie').one()
            assert (mv.quantity, mv.machine_id, mv.stage_id, mv.from_buffer_id) == (
                3, w['machine']['L1'], w['stage']['A'], w['buf']['A1'])
        full = take(client, w, 'Drzwi', 'A1', 2, machine='L2')                   # reszta z bufora, druga maszyna
        assert full.status_code == 200 and stock_of(app) == []
        assert sorted(x[2] for x in wips(app)) == [2, 3]

    def test_machine_required_only_when_stage_has_machines(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 5)
        none = take(client, w, 'Drzwi', 'A1', 1)
        assert none.status_code == 400 and 'Wybierz maszynę' in none.get_json()['error']
        foreign = api(client, '/stanowisko/przejecie', component_id=w['comp']['Drzwi'], buffer_id=w['buf']['A1'],
                      stage_id=hint(w, 'A1'), quantity=1, machine_id=999999)
        assert foreign.status_code == 400
        with app.app_context():
            other = Machine(stage_id=w['stage']['B'], name='T4 Giętarka')
            db.session.add(other)
            db.session.commit()
            other_id = other.id
        wrong = api(client, '/stanowisko/przejecie', component_id=w['comp']['Drzwi'], buffer_id=w['buf']['A1'],
                    stage_id=hint(w, 'A1'), quantity=1, machine_id=other_id)
        assert wrong.status_code == 400 and 'nie należy do tego etapu' in wrong.get_json()['error']
        with app.app_context():
            db.session.get(Machine, w['machine']['L1']).is_active = False
            db.session.get(Machine, w['machine']['L2']).is_active = False   # etap bez aktywnych maszyn
            db.session.commit()
        assert take(client, w, 'Drzwi', 'A1', 1).status_code == 200

    def test_employee_must_belong_to_stage_department(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 5)
        switch(client, w, 'B')
        res = take(client, w, 'Drzwi', 'A1', 1, machine='L1')
        assert res.status_code == 400 and 'tylko pracownicy działu' in res.get_json()['error']
        switch(client, w, 'A')
        assert take(client, w, 'Drzwi', 'A1', 1, machine='L1').status_code == 200

    def test_no_badge_for_worker_but_kontroler_and_admin_may_take_over(self, client, app, users, w):
        boss(client)
        put(client, w, 'Drzwi', 'A1', 6)
        assert take(client, w, 'Drzwi', 'A1', 1, machine='L1').status_code == 200   # kontroler bez plakietki
        assert take(client, w, 'Drzwi', 'A1', 1).status_code == 400                  # maszyna nadal wymagana
        boss(client, 'admin', 'Admin1234!')
        assert take(client, w, 'Drzwi', 'A1', 1, machine='L2').status_code == 200
        client.get('/logout')
        login(client, 'kp4_operator', 'Rola1234!')
        res = take(client, w, 'Drzwi', 'A1', 1, machine='L1')
        assert res.status_code == 401 and res.get_json()['need_employee']
        with app.app_context():
            assert Movement.query.filter_by(kind='przejecie', employee_id=None).count() == 2

    def test_quantity_rules(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 3)
        too_much = take(client, w, 'Drzwi', 'A1', 4, machine='L1')
        assert too_much.status_code == 400 and 'tylko 3 szt.' in too_much.get_json()['error']
        for bad in (0, -1, 'abc', 1.5, None):
            assert take(client, w, 'Drzwi', 'A1', bad, machine='L1').status_code == 400, bad
        assert take(client, w, 'Drzwi', 'A2', 1, machine='L1').status_code == 400     # pusty bufor
        assert stock_of(app) == [('Drzwi', 'T4A1', 3)] and wips(app) == []

    def test_blocked_cases(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Korpus', 'S1', 2)                                            # element na buforze składania
        res = take(client, w, 'Korpus', 'S1', 1)
        assert res.status_code == 400 and 'Złóż produkt' in res.get_json()['error']
        with app.app_context():                                                       # pozycja wstrzymana nie jest dostępna
            db.session.add(StockPosition(component_id=w['comp']['Drzwi'], buffer_id=w['buf']['A1'],
                                         quantity=3, status='wstrzymane'))
            db.session.commit()
        assert take(client, w, 'Drzwi', 'A1', 1, machine='L1').status_code == 400
        with app.app_context():                                                       # etap spoza trasy elementu
            db.session.add(StockPosition(component_id=w['comp']['Korpus'], buffer_id=w['buf']['B1'], quantity=2))
            db.session.commit()
        res = take(client, w, 'Korpus', 'B1', 1)
        assert res.status_code == 400 and 'nie jest w trasie' in res.get_json()['error']
        with app.app_context():
            db.session.get(WorkOrder, w['order']).status = 'cancelled'
            db.session.commit()
        assert 'nie jest w toku' in take(client, w, 'Korpus', 'S1', 1).get_json()['error']


# ── Wpis po etapie na bufor następnego etapu ──────────────────────────────────

class TestTransfer:
    def _wip(self, client, w, qty=3, comp='Drzwi'):
        put(client, w, comp, 'A1', qty)
        res = take(client, w, comp, 'A1', qty, machine='L1')
        return newest(res)

    def test_transfer_to_next_and_later_stages(self, client, app, users, w):
        who(client, w, 'A')
        wip = self._wip(client, w, 4)
        res = move(client, wip, w, 'B1', 1)                                           # następny etap trasy
        assert res.status_code == 200 and 'T4B1' in res.get_json()['msg']
        assert wips(app) == [('Drzwi', 'T4 Wycinanie', 3)] and stock_of(app) == [('Drzwi', 'T4B1', 1)]
        assert move(client, wip, w, 'K1', 1).status_code == 200                       # przeskok etapu (Gięcie pominięte)
        assert move(client, wip, w, 'S1', 2).status_code == 200                       # po trasie: składanie
        assert wips(app) == []
        assert stock_of(app) == [('Drzwi', 'T4B1', 1), ('Drzwi', 'T4K1', 1), ('Drzwi', 'T4S1', 2)]
        with app.app_context():
            mv = Movement.query.filter_by(kind='przeniesienie').first()
            assert (mv.stage_id, mv.machine_id, mv.wip_id) == (w['stage']['A'], w['machine']['L1'], wip)
        again = move(client, wip, w, 'B1', 1)
        assert again.status_code == 404                                               # pozycja już zużyta

    def test_wrong_targets_rejected(self, client, app, users, w):
        who(client, w, 'A')
        wip = self._wip(client, w, 3)
        for key in ('A2', 'F1', 'P1'):                      # ten sam etap albo etap spoza trasy
            res = move(client, wip, w, key, 1)
            assert res.status_code == 400 and 'może czekać na etap' in res.get_json()['error'], key
        korpus = self._wip(client, w, 2, 'Korpus')
        res = move(client, korpus, w, 'B1', 1)                                         # Korpus nie ma etapu Gięcie
        assert res.status_code == 400 and 'T4 Kompletacja' in res.get_json()['error']
        assert move(client, korpus, w, 'K1', 5).status_code == 400                     # za duża ilość
        for bad in (0, -1, 'x', 1.2):
            assert move(client, wip, w, 'B1', bad).status_code == 400, bad
        with app.app_context():
            db.session.get(Buffer, w['buf']['B1']).is_active = False
            db.session.commit()
        assert move(client, wip, w, 'B1', 1).status_code == 400

    def test_any_identified_employee_may_complete_but_not_anonymous_worker(self, client, app, users, w):
        who(client, w, 'A')
        wip = self._wip(client, w, 3)
        switch(client, w, 'B')                                                          # inny pracownik kończy ruch
        assert move(client, wip, w, 'B1', 1).status_code == 200
        api(client, '/stanowisko/wyloguj')
        res = move(client, wip, w, 'B1', 1)
        assert res.status_code == 401 and res.get_json()['need_employee']
        boss(client)
        assert move(client, wip, w, 'B1', 1).status_code == 200                         # kontroler bez plakietki

    def test_scan_returns_wip_for_employee_and_order(self, client, app, users, w):
        who(client, w, 'A')
        self._wip(client, w, 3)
        body = api(client, '/stanowisko/skan', code=badge(w['token']['A'])).get_json()
        assert [(r['component'], r['quantity']) for r in body['wip']] == [('Drzwi', 3)]
        switch(client, w, 'B')
        body = api(client, '/stanowisko/skan', code=badge(w['token']['B'])).get_json()
        assert body['wip'] == []                                                        # cudzych nie pokazuje bez zlecenia
        body = api(client, '/stanowisko/skan', code='T4A1', order_id=w['order']).get_json()
        assert len(body['wip']) == 1 and body['order']['wip'][0]['quantity'] == 3


# ── Składanie ─────────────────────────────────────────────────────────────────

class TestAssembly:
    def _stock(self, client, w, drzwi=4, korpus=2, buf='S1'):
        put(client, w, 'Drzwi', buf, drzwi)
        put(client, w, 'Korpus', buf, korpus)

    def test_limit_uses_minimum_over_elements(self, client, app, users, w):
        who(client, w, 'S')
        self._stock(client, w, 5, 3)                                  # 5/2=2 kompletów drzwi, 3 korpusy
        with app.app_context():
            info = flow.assembly_info(db.session.get(WorkOrder, w['order']),
                                      db.session.get(Buffer, w['buf']['S1']))
            assert info['limit'] == 2 and info['left'] == 4 and info['missing'] == []
        body = api(client, '/stanowisko/skan', code='T4S1', order_id=w['order']).get_json()
        assert body['order']['assembly']['limit'] == 2
        assert body['order']['assembly']['stage']['name'] == 'T4 Składanie'
        assert 'stage' not in body['buffer']

    def test_assembly_consumes_elements_and_creates_product_wip(self, client, app, users, w):
        who(client, w, 'S')
        self._stock(client, w, 4, 2)
        over = assemble(client, w, 'S1', 3)
        assert over.status_code == 400 and 'najwyżej 2 szt.' in over.get_json()['error']
        res = assemble(client, w, 'S1', 2)
        body = res.get_json()
        assert res.status_code == 200 and 'Złożono 2 szt.' in body['msg']
        assert stock_of(app) == []                                                       # elementy zużyte
        assert wips(app) == [('Gotowy produkt', 'T4 Składanie', 2)]
        assert [c['name'] for c in body['order']['components']] == ['Drzwi', 'Korpus']   # produkt poza elementami
        assert body['order']['product_unit']['produced'] == 2 and body['order']['product_unit']['in_progress'] == 2
        with app.app_context():
            order = db.session.get(WorkOrder, w['order'])
            assert order.product_component.is_product and order.product_component.required_qty == 4
            assert [c.name for c in order.components] == ['Drzwi', 'Korpus']
            kinds = sorted(m.kind for m in Movement.query.filter(Movement.kind.in_(('zuzycie', 'zlozenie'))))
            assert kinds == ['zlozenie', 'zuzycie', 'zuzycie']
            used = {m.component.name: m.quantity for m in Movement.query.filter_by(kind='zuzycie')}
            assert used == {'Drzwi': 4, 'Korpus': 2}
            assert len({m.group_id for m in Movement.query.filter(Movement.kind.in_(('zuzycie', 'zlozenie')))}) == 1

    def test_missing_elements_and_only_scanned_buffer_counts(self, client, app, users, w):
        who(client, w, 'S')
        put(client, w, 'Drzwi', 'S1', 4)
        res = assemble(client, w, 'S1', 1)
        assert res.status_code == 400 and 'brakuje elementów' in res.get_json()['error'] and 'Korpus' in res.get_json()['error']
        put(client, w, 'Korpus', 'S2', 2)                                                # drugi element na innym buforze
        assert assemble(client, w, 'S1', 1).status_code == 400
        assert assemble(client, w, 'S2', 1).status_code == 400
        assert stock_of(app) == [('Drzwi', 'T4S1', 4), ('Korpus', 'T4S2', 2)]

    def test_partial_assembly_up_to_order_quantity(self, client, app, users, w):
        who(client, w, 'S')
        self._stock(client, w, 8, 4)
        assert assemble(client, w, 'S1', 1).status_code == 200
        assert assemble(client, w, 'S1', 2).status_code == 200
        res = assemble(client, w, 'S1', 2)
        assert res.status_code == 400 and 'najwyżej 1 szt.' in res.get_json()['error']
        assert assemble(client, w, 'S1', 1).status_code == 200
        done = assemble(client, w, 'S1', 1)
        assert done.status_code == 400 and 'już złożone' in done.get_json()['error']

    def test_assembly_rules_stage_department_and_roles(self, client, app, users, w):
        who(client, w, 'S')
        self._stock(client, w, 4, 2)
        put(client, w, 'Drzwi', 'K1', 2)
        res = assemble(client, w, 'K1', 1)                                               # tylko etap składania
        assert res.status_code == 400 and 'czekających na składanie' in res.get_json()['error']
        switch(client, w, 'X')                                                           # obcy dział
        res = assemble(client, w, 'S1', 1)
        assert res.status_code == 400 and 'tylko pracownicy działu' in res.get_json()['error']
        api(client, '/stanowisko/wyloguj')
        assert assemble(client, w, 'S1', 1).status_code == 401
        boss(client, 'admin', 'Admin1234!')                                              # admin bez ograniczeń
        assert assemble(client, w, 'S1', 1).status_code == 200
        for bad in (0, -2, 'a', 0.5):
            assert assemble(client, w, 'S1', bad).status_code == 400, bad

    def test_assembly_machine_when_stage_has_machines(self, client, app, users, w):
        with app.app_context():
            db.session.add(Machine(stage_id=w['stage']['S'], name='T4 Stół montażowy'))
            db.session.commit()
        who(client, w, 'S')
        self._stock(client, w, 2, 1)
        res = assemble(client, w, 'S1', 1)
        assert res.status_code == 400 and 'Wybierz maszynę' in res.get_json()['error']
        with app.app_context():
            mid = Machine.query.filter_by(name='T4 Stół montażowy').one().id
        ok = api(client, '/stanowisko/zloz', order_id=w['order'], buffer_id=w['buf']['S1'], quantity=1, machine_id=mid)
        assert ok.status_code == 200
        with app.app_context():
            assert Movement.query.filter_by(kind='zlozenie').one().machine_id == mid


# ── Dalej: produkt przez etapy i pakowanie ────────────────────────────────────

class TestProductFlow:
    def _assembled(self, client, app, w, units=2):
        who(client, w, 'S')
        put(client, w, 'Drzwi', 'S1', 2 * units)
        put(client, w, 'Korpus', 'S1', units)
        res = assemble(client, w, 'S1', units)
        assert res.status_code == 200
        return newest(res)

    def test_product_moves_through_quality_to_packing(self, client, app, users, w):
        wip = self._assembled(client, app, w, 4)
        row = api(client, '/stanowisko/skan', code='T4S1', order_id=w['order']).get_json()['wip'][0]
        assert [t['name'] for t in row['targets']] == ['T4 Kontrola końcowa', 'T4 Pakowanie'] and not row['can_pack']
        assert move(client, wip, w, 'S2', 1).status_code == 400                           # ten sam etap
        assert move(client, wip, w, 'F1', 3).status_code == 200
        assert move(client, wip, w, 'P1', 1).status_code == 200                           # przeskok kontroli
        with app.app_context():
            product_id = db.session.get(WorkOrder, w['order']).product_component.id
        switch(client, w, 'F')
        res = api(client, '/stanowisko/przejecie', component_id=product_id, buffer_id=w['buf']['F1'], stage_id=hint(w, 'F1'), quantity=3)
        assert res.status_code == 200
        wip_f = newest(res)
        assert move(client, wip_f, w, 'P1', 3).status_code == 200
        assert stock_of(app) == [('Gotowy produkt', 'T4P1', 4)]
        switch(client, w, 'P')
        res = api(client, '/stanowisko/przejecie', component_id=product_id, buffer_id=w['buf']['P1'], stage_id=hint(w, 'P1'), quantity=4)
        assert res.status_code == 200
        pack_wip = res.get_json()['wip'][0]
        assert pack_wip['can_pack'] and pack_wip['targets'] == []
        assert api(client, '/stanowisko/spakowane', wip_id=pack_wip['id'], quantity=5).status_code == 400
        res = api(client, '/stanowisko/spakowane', wip_id=pack_wip['id'], quantity=3)
        assert res.status_code == 200 and 'Zlecenie zakończone' not in res.get_json()['msg']
        with app.app_context():
            assert db.session.get(WorkOrder, w['order']).status == 'active'
        res = api(client, '/stanowisko/spakowane', wip_id=pack_wip['id'], quantity=1)
        assert res.status_code == 200 and 'Zlecenie zakończone' in res.get_json()['msg']
        with app.app_context():
            order = db.session.get(WorkOrder, w['order'])
            assert order.status == 'done' and order.completed_at is not None and order.status_label == 'Zakończone'
        # zakończone zlecenie nie przyjmuje nowych wpisów
        assert api(client, '/stanowisko/wpis', component_id=w['comp']['Drzwi'], buffer_id=w['buf']['A1'],
                   quantity=1).status_code == 400

    def test_pack_errors(self, client, app, users, w):
        wip = self._assembled(client, app, w, 2)
        res = api(client, '/stanowisko/spakowane', wip_id=wip, quantity=1)               # produkt jeszcze na składaniu
        assert res.status_code == 400 and 'Spakować można dopiero po przejęciu etapu' in res.get_json()['error']
        put(client, w, 'Korpus', 'A1', 1)
        switch(client, w, 'A')
        wip_el = newest(take(client, w, 'Korpus', 'A1', 1, machine='L1'))
        res = api(client, '/stanowisko/spakowane', wip_id=wip_el, quantity=1)
        assert res.status_code == 400 and 'tylko gotowy produkt' in res.get_json()['error']
        api(client, '/stanowisko/wyloguj')
        assert api(client, '/stanowisko/spakowane', wip_id=wip, quantity=1).status_code == 401
        assert api(client, '/stanowisko/spakowane', wip_id=999999, quantity=1).status_code in (401, 404)


# ── Wycofanie ruchów ──────────────────────────────────────────────────────────

class TestRevert:
    def test_revert_takeover_restores_buffer(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 5)
        mid = take(client, w, 'Drzwi', 'A1', 3, machine='L1').get_json()['recent'][0]['id']
        res = revert(client, mid)
        assert res.status_code == 200 and 'Wycofano' in res.get_json()['msg']
        assert wips(app) == [] and stock_of(app) == [('Drzwi', 'T4A1', 5)]
        assert 'już wycofany' in revert(client, mid).get_json()['error']

    def test_revert_takeover_blocked_after_partial_transfer_then_chain(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 4)
        take_id = take(client, w, 'Drzwi', 'A1', 4, machine='L1').get_json()['recent'][0]['id']
        with app.app_context():
            wip_id = WorkInProgress.query.one().id
        assert move(client, wip_id, w, 'B1', 1).status_code == 200
        res = revert(client, take_id)
        assert res.status_code == 400 and 'została już przeniesiona' in res.get_json()['error']
        with app.app_context():
            transfer_id = Movement.query.filter_by(kind='przeniesienie').one().id
        assert revert(client, transfer_id).status_code == 200                                # najpierw późniejszy ruch
        assert wips(app) == [('Drzwi', 'T4 Wycinanie', 4)] and stock_of(app) == []
        assert revert(client, take_id).status_code == 200
        assert stock_of(app) == [('Drzwi', 'T4A1', 4)] and wips(app) == []

    def test_revert_transfer_recreates_consumed_wip(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 3)
        take(client, w, 'Drzwi', 'A1', 3, machine='L2')
        with app.app_context():
            wip_id = WorkInProgress.query.one().id
        assert move(client, wip_id, w, 'B1', 3).status_code == 200
        assert wips(app) == []                                                                # pozycja zużyta w całości
        with app.app_context():
            transfer_id = Movement.query.filter_by(kind='przeniesienie').one().id
        assert revert(client, transfer_id).status_code == 200
        assert stock_of(app) == []
        with app.app_context():
            wip = WorkInProgress.query.one()
            assert (wip.quantity, wip.stage_id, wip.machine_id, wip.employee_id) == (
                3, w['stage']['A'], w['machine']['L2'], w['emp']['A'])
            assert wip.source_buffer_id == w['buf']['A1']

    def test_revert_transfer_blocked_when_quantity_moved_on(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 2)
        take(client, w, 'Drzwi', 'A1', 2, machine='L1')
        with app.app_context():
            wip_id = WorkInProgress.query.one().id
        move(client, wip_id, w, 'B1', 2)
        switch(client, w, 'B')
        res = api(client, '/stanowisko/przejecie', component_id=w['comp']['Drzwi'], buffer_id=w['buf']['B1'], stage_id=hint(w, 'B1'), quantity=2)
        assert res.status_code == 200
        with app.app_context():
            transfer_id = Movement.query.filter_by(kind='przeniesienie').one().id
        boss(client)
        res = revert(client, transfer_id)
        assert res.status_code == 400 and 'nie leży już tam' in res.get_json()['error']

    def test_only_own_movements_for_workers_any_for_kontroler(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 4)
        mid = take(client, w, 'Drzwi', 'A1', 2, machine='L1').get_json()['recent'][0]['id']
        switch(client, w, 'B')
        res = revert(client, mid)
        assert res.status_code == 400 and 'tylko własny' in res.get_json()['error']
        api(client, '/stanowisko/wyloguj')
        assert 'plakietk' in revert(client, mid).get_json()['error']
        boss(client)
        assert revert(client, mid).status_code == 200

    def test_revert_assembly_restores_elements_and_removes_product(self, client, app, users, w):
        who(client, w, 'S')
        put(client, w, 'Drzwi', 'S1', 4)
        put(client, w, 'Korpus', 'S1', 2)
        zlozenie_id = assemble(client, w, 'S1', 2).get_json()['recent'][0]['id']
        res = revert(client, zlozenie_id)
        assert res.status_code == 200
        assert wips(app) == [] and stock_of(app) == [('Drzwi', 'T4S1', 4), ('Korpus', 'T4S1', 2)]
        with app.app_context():
            assert Movement.query.filter(Movement.kind.in_(('zuzycie', 'zlozenie')),
                                         Movement.reverted_at.is_(None)).count() == 0
            assert flow.product_produced(db.session.get(WorkOrder, w['order'])) == 0
        assert assemble(client, w, 'S1', 2).status_code == 200                                # można złożyć ponownie

    def test_revert_assembly_blocked_after_product_moved_on(self, client, app, users, w):
        who(client, w, 'S')
        put(client, w, 'Drzwi', 'S1', 2)
        put(client, w, 'Korpus', 'S1', 1)
        res = assemble(client, w, 'S1', 1).get_json()
        wip_id, zlozenie_id = res['wip'][0]['id'], res['recent'][0]['id']
        move(client, wip_id, w, 'F1', 1)
        out = revert(client, zlozenie_id)
        assert out.status_code == 400 and 'została już przeniesiona' in out.get_json()['error']

    def test_zuzycie_not_individually_revertible(self, client, app, users, w):
        who(client, w, 'S')
        put(client, w, 'Drzwi', 'S1', 2)
        put(client, w, 'Korpus', 'S1', 1)
        assemble(client, w, 'S1', 1)
        with app.app_context():
            used_id = Movement.query.filter_by(kind='zuzycie').first().id
        boss(client)
        res = revert(client, used_id)
        assert res.status_code == 400 and 'razem ze złożeniem' in res.get_json()['error']

    def test_revert_pack_reopens_order(self, client, app, users, w):
        who(client, w, 'S')
        put(client, w, 'Drzwi', 'S1', 2)
        put(client, w, 'Korpus', 'S1', 1)
        with app.app_context():
            db.session.get(WorkOrder, w['order']).quantity = 1
            for c in db.session.get(WorkOrder, w['order']).components:
                c.required_qty = c.per_unit
            db.session.commit()
        wip_id = newest(assemble(client, w, 'S1', 1))
        move(client, wip_id, w, 'P1', 1)
        with app.app_context():
            product_id = db.session.get(WorkOrder, w['order']).product_component.id
        switch(client, w, 'P')
        pack_wip = newest(api(client, '/stanowisko/przejecie', component_id=product_id,
                              buffer_id=w['buf']['P1'], stage_id=hint(w, 'P1'), quantity=1))
        res = api(client, '/stanowisko/spakowane', wip_id=pack_wip, quantity=1)
        pack_move = res.get_json()['recent'][0]['id']
        with app.app_context():
            assert db.session.get(WorkOrder, w['order']).status == 'done'
        assert revert(client, pack_move).status_code == 200
        with app.app_context():
            order = db.session.get(WorkOrder, w['order'])
            assert order.status == 'active' and order.completed_at is None
        assert wips(app) == [('Gotowy produkt', 'T4 Pakowanie', 1)]

    def test_revert_buttons_on_order_page_for_all_kinds(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 3)
        mid = take(client, w, 'Drzwi', 'A1', 1, machine='L1').get_json()['recent'][0]['id']
        boss(client)
        page = client.get(f'{KP}/{w["order"]}').get_data(as_text=True)
        assert f'/ruch/{mid}/cofnij' in page and 'Przejęcie etapu' in page
        res = form_post(client, f'{KP}/ruch/{mid}/cofnij')
        assert 'Wycofano:' in res.get_data(as_text=True)


# ── Zabezpieczenia i widoki ───────────────────────────────────────────────────

class TestProtections:
    def test_order_quantity_not_below_produced(self, client, app, users, w):
        who(client, w, 'S')
        put(client, w, 'Drzwi', 'S1', 6)
        put(client, w, 'Korpus', 'S1', 3)
        assemble(client, w, 'S1', 3)
        boss(client, 'admin', 'Admin1234!')
        res = form_post(client, f'{KP}/{w["order"]}/ilosc', quantity='2')
        assert 'poniżej już' in res.get_data(as_text=True)
        form_post(client, f'{KP}/{w["order"]}/ilosc', quantity='6')
        with app.app_context():
            order = db.session.get(WorkOrder, w['order'])
            assert order.quantity == 6 and order.product_component.required_qty == 6

    def test_stage_machine_employee_with_history_cannot_be_deleted(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 3)
        take(client, w, 'Drzwi', 'A1', 2, machine='L1')
        boss(client, 'admin', 'Admin1234!')
        res = form_post(client, f'{US}/etapy/{w["stage"]["A"]}/usun')
        assert 'Nie można usunąć etapu' in res.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(ProductionStage, w['stage']['A']) is not None
        res = form_post(client, f'{US}/maszyny/{w["machine"]["L1"]}/usun')
        assert 'zapisana w historii' in res.get_data(as_text=True)
        res = form_post(client, f'{US}/admin/departments/{w["dept"]["A"]}/employees', action='delete',
                        emp_id=w['emp']['A'])
        assert 'wpisy na bufory' in res.get_data(as_text=True)
        res = form_post(client, f'{US}/bufory/{w["buf"]["A1"]}/usun')
        assert 'Dezaktywuj' in res.get_data(as_text=True)
        form_post(client, f'{US}/maszyny/{w["machine"]["L2"]}/usun')               # nieużywana maszyna: można
        with app.app_context():
            assert db.session.get(Machine, w['machine']['L2']) is None

    def test_component_route_cannot_drop_stage_with_work_in_progress(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 3)
        take(client, w, 'Drzwi', 'A1', 3, machine='L1')
        boss(client, 'admin', 'Admin1234!')
        res = form_post(client, f'{KP}/element/{w["comp"]["Drzwi"]}/zapisz', name='Drzwi', per_unit='2',
                        required_qty='8', stage_ids=[w['stage']['B'], w['stage']['K']])
        assert 'Nie można usunąć z trasy etapu' in res.get_data(as_text=True)

    def test_order_list_status_chips_and_done_filter(self, client, app, users, w):
        with app.app_context():
            db.session.get(WorkOrder, w['order']).status = 'done'
            db.session.commit()
        boss(client)
        page = client.get(KP + '/?s=done').get_data(as_text=True)
        assert 'T4-ZO-1' in page and 'Zakończone' in page
        assert 'T4-ZO-1' not in client.get(KP + '/?s=active').get_data(as_text=True)

    def test_station_scan_payload_shape_and_order_summary(self, client, app, users, w):
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 3)
        take(client, w, 'Drzwi', 'A1', 1, machine='L1')
        body = api(client, '/stanowisko/skan', code='T4A1').get_json()
        assert 'stage' not in body['buffer'] and body['buffer']['contents'][0]['stage'] == 'T4 Wycinanie'
        body = api(client, '/stanowisko/skan', code='T4A1', order_id=w['order']).get_json()
        pos = next(c for c in body['order']['components'] if c['name'] == 'Drzwi')['positions'][0]
        assert pos['stage'] == 'T4 Wycinanie'
        assert pos['machines'] == [{'id': w['machine']['L1'], 'name': 'T4 Laser 1'},
                                   {'id': w['machine']['L2'], 'name': 'T4 Laser 2'}]
        with app.app_context():
            summary = stock.order_summary(db.session.get(WorkOrder, w['order']))
        drzwi = next(c for c in summary['components'] if c['name'] == 'Drzwi')
        assert drzwi['in_progress'] == 1 and drzwi['entered'] == 3
        assert summary['product_unit'] == {'required': 4, 'produced': 0, 'packed': 0, 'in_progress': 0,
                                           'positions': [], 'component_id': None}


class TestOrderPage:
    def test_order_page_shows_product_wip_and_done_status(self, client, app, users, w):
        who(client, w, 'S')
        put(client, w, 'Drzwi', 'S1', 2)
        put(client, w, 'Korpus', 'S1', 1)
        assemble(client, w, 'S1', 1)
        put(client, w, 'Drzwi', 'A1', 2)
        switch(client, w, 'A')
        take(client, w, 'Drzwi', 'A1', 1, machine='L1')
        boss(client)
        page = client.get(f'{KP}/{w["order"]}').get_data(as_text=True)
        assert 'Gotowy produkt i ilości w realizacji' in page
        assert 'Złożone: <strong>1</strong>' in page and 'T4 Laser 1' in page and 'Gotowy produkt' in page
        assert 'Zużycie elementu przy składaniu' in page and 'Złożenie produktu' in page

    def test_done_order_header(self, client, app, users, w):
        with app.app_context():
            order = db.session.get(WorkOrder, w['order'])
            order.status = 'done'
            db.session.commit()
        boss(client)
        assert 'Zakończone' in client.get(f'{KP}/{w["order"]}').get_data(as_text=True)


class TestDeleteOrders:
    def _busy_order(self, client, w):
        """Zlecenie z wpisem, przejęciem (w realizacji) i złożonym produktem."""
        who(client, w, 'A')
        put(client, w, 'Drzwi', 'A1', 4)
        take(client, w, 'Drzwi', 'A1', 1, machine='L1')
        switch(client, w, 'S')
        put(client, w, 'Drzwi', 'S1', 2)
        put(client, w, 'Korpus', 'S1', 2)
        assemble(client, w, 'S1', 1)

    def _second_order(self, app, w):
        with app.app_context():
            o = WorkOrder(zo_number='T4-ZO-2', product_name='INNY', quantity=2,
                          created_by_id=User.query.filter_by(username='admin').one().id)
            db.session.add(o)
            db.session.flush()
            c = WorkOrderComponent(order_id=o.id, name='Plecy', per_unit=1, required_qty=2, position=1)
            db.session.add(c)
            db.session.flush()
            db.session.add(WorkOrderComponentStage(component_id=c.id, stage_id=w['stage']['A']))
            db.session.add(StockPosition(component_id=c.id, buffer_id=w['buf']['A1'], quantity=2))
            db.session.commit()
            return o.id

    def test_admin_deletes_order_with_all_history(self, client, app, users, w):
        other = self._second_order(app, w)
        self._busy_order(client, w)
        boss(client, 'admin', 'Admin1234!')
        res = form_post(client, f'{KP}/{w["order"]}/usun')
        assert 'Usunięto zlecenie ZO T4-ZO-1' in res.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(WorkOrder, w['order']) is None
            assert WorkOrderComponent.query.filter(WorkOrderComponent.order_id == w['order']).count() == 0
            assert Movement.query.filter_by(order_id=w['order']).count() == 0
            assert WorkInProgress.query.count() == 0
            assert [p.quantity for p in StockPosition.query.all()] == [2]          # tylko drugie zlecenie
            assert db.session.get(WorkOrder, other) is not None
            # bufory, pracownicy, etapy i maszyny zostają
            assert Buffer.query.filter(Buffer.code.like('T4%')).count() == 8
            assert DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T4 %')).count() == 7
            from models import AuditLog
            log = AuditLog.query.filter_by(action='kp_delete').order_by(AuditLog.id.desc()).first()
            assert 'ZO=T4-ZO-1' in log.detail and 'ruchy=' in log.detail
        # pobyt na liście i ponowne zeskanowanie: zlecenie nie istnieje
        page = client.get(KP + '/?s=all').get_data(as_text=True)
        assert 'T4-ZO-1' not in page and 'T4-ZO-2' in page
        assert api(client, '/stanowisko/skan', code='{"p": "PSH-T4", "o": "T4-ZO-1"}').status_code == 404

    def test_history_is_gone_so_buffer_can_be_removed_afterwards(self, client, app, users, w):
        self._busy_order(client, w)
        boss(client, 'admin', 'Admin1234!')
        assert 'Dezaktywuj' in form_post(client, f'{US}/bufory/{w["buf"]["A1"]}/usun').get_data(as_text=True)
        form_post(client, f'{KP}/{w["order"]}/usun')
        form_post(client, f'{US}/bufory/{w["buf"]["A1"]}/usun')
        with app.app_context():
            assert db.session.get(Buffer, w['buf']['A1']) is None

    def test_only_admin_may_delete(self, client, app, users, w):
        for user, pw in (('oper', 'Oper1234!'), ('kp4_operator', 'Rola1234!'), ('kp4_station', 'Rola1234!')):
            client.get('/logout')
            login(client, user, pw)
            assert form_post(client, f'{KP}/{w["order"]}/usun').status_code == 403, user
            assert form_post(client, f'{KP}/usun-zaznaczone', order_ids=[w['order']]).status_code == 403, user
        with app.app_context():
            assert db.session.get(WorkOrder, w['order']) is not None

    def test_bulk_delete_and_empty_selection(self, client, app, users, w):
        other = self._second_order(app, w)
        boss(client, 'admin', 'Admin1234!')
        res = form_post(client, f'{KP}/usun-zaznaczone')
        assert 'Nie zaznaczono żadnych zleceń' in res.get_data(as_text=True)
        res = form_post(client, f'{KP}/usun-zaznaczone', order_ids=[w['order'], other])
        assert 'Usunięto zlecenia: 2' in res.get_data(as_text=True)
        with app.app_context():
            assert WorkOrder.query.filter(WorkOrder.zo_number.like('T4-%')).count() == 0
            assert StockPosition.query.count() == 0

    def test_controls_and_confirmation_only_for_admin(self, client, app, users, w):
        self._busy_order(client, w)
        boss(client)
        assert 'Usuń zlecenie' not in client.get(f'{KP}/{w["order"]}').get_data(as_text=True)
        assert 'kp-bulk-form' not in client.get(KP + '/').get_data(as_text=True)
        boss(client, 'admin', 'Admin1234!')
        page = client.get(f'{KP}/{w["order"]}').get_data(as_text=True)
        assert 'Usuń zlecenie' in page and 'Tego nie można cofnąć' in page
        assert 'ilością na buforach' in page and 'w realizacji' in page
        lst = client.get(KP + '/').get_data(as_text=True)
        assert 'kp-bulk-form' in lst and 'name="order_ids"' in lst and 'Usuń zaznaczone' in lst

    def test_delete_works_for_cancelled_and_done_orders(self, client, app, users, w):
        other = self._second_order(app, w)
        with app.app_context():
            db.session.get(WorkOrder, w['order']).status = 'cancelled'
            db.session.get(WorkOrder, other).status = 'done'
            db.session.commit()
        boss(client, 'admin', 'Admin1234!')
        form_post(client, f'{KP}/{w["order"]}/usun')
        form_post(client, f'{KP}/{other}/usun')
        with app.app_context():
            assert WorkOrder.query.filter(WorkOrder.zo_number.like('T4-%')).count() == 0
