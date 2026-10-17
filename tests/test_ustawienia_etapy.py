"""Testy etapu 1: etapy produkcji, maszyny, bufory (etykiety) i plakietki pracowników działów."""
import pytest

from app import app as flask_app, db, _seed_stages, _backfill_department_tokens
from models import (User, ProductionDepartment, DepartmentEmployee, ProductionStage,
                    Machine, Buffer)
from test_app import login, _csrf
from ustawienia.lookup import (find_buffer, norm_code, parse_badge, find_department_employee,
                               BADGE_PREFIX)

BASE = '/ustawienia-produkcji'


def post(client, url, **data):
    data['_csrf_token'] = _csrf(client)
    return client.post(BASE + url, data=data, follow_redirects=True)


@pytest.fixture(autouse=True)
def _clean(app):
    def wipe():
        with app.app_context():
            Machine.query.delete()
            Buffer.query.delete()
            ProductionStage.query.delete()
            DepartmentEmployee.query.filter(DepartmentEmployee.name.like('T1 %')).delete(
                synchronize_session=False)
            db.session.commit()
    wipe()
    yield
    wipe()


@pytest.fixture
def order_user(app):
    with app.app_context():
        if not User.query.filter_by(username='zam_etapy').first():
            u = User(username='zam_etapy', email='zam_etapy@test.pl', role='order')
            u.set_password('Order1234!')
            db.session.add(u)
            db.session.commit()
    return 'zam_etapy'


@pytest.fixture
def department(app):
    with app.app_context():
        d = ProductionDepartment(name='T1 Dział testowy', order=90)
        db.session.add(d)
        db.session.commit()
        dept_id = d.id
    yield dept_id
    with app.app_context():
        DepartmentEmployee.query.filter_by(department_id=dept_id).delete()
        ProductionStage.query.filter_by(department_id=dept_id).update({'department_id': None})
        ProductionDepartment.query.filter_by(id=dept_id).delete()
        db.session.commit()


def _stage(app, name='T1 Etap', **kw):
    with app.app_context():
        s = ProductionStage(name=name, order=kw.pop('order', 1), **kw)
        db.session.add(s)
        db.session.commit()
        return s.id


# ── Dane startowe ─────────────────────────────────────────────────────────────

class TestSeed:
    def test_seed_creates_12_stages_machines_and_departments(self, app):
        with app.app_context():
            before = {d.id for d in ProductionDepartment.query.all()}
            created = []
            for name in ('Gięcie', 'Zgrzewanie', 'Spawanie'):
                if not ProductionDepartment.query.filter_by(name=name).first():
                    d = ProductionDepartment(name=name, order=80)
                    db.session.add(d)
                    db.session.flush()
                    created.append(d.id)
            db.session.commit()
            try:
                _seed_stages()
                db.session.commit()
                stages = ProductionStage.query.order_by(ProductionStage.order).all()
                assert len(stages) == 12
                assert [s.level for s in stages][:9] == ['element'] * 9
                assert [s.level for s in stages][9:] == ['produkt'] * 3
                by_name = {s.name: s for s in stages}
                assert by_name['Zgrzewanie'].id != by_name['Spawanie'].id
                assert by_name['Gięcie'].department.name == 'Gięcie'
                assert by_name['Wycinanie (laser, wykrawarki)'].department.name == 'Wycinanie'
                assert by_name['Pakowanie'].department.name == 'Pakowanie'
                assert sorted(m.name for m in stages[0].machines) == [
                    'Laser 1', 'Laser 2', 'Wykrawarka 1', 'Wykrawarka 2']
                # idempotentnie, nie nadpisuje
                by_name['Gięcie'].name = 'Gięcie zmienione'
                db.session.commit()
                _seed_stages()
                db.session.commit()
                assert ProductionStage.query.count() == 12
                assert ProductionStage.query.filter_by(name='Gięcie zmienione').count() == 1
                assert Machine.query.count() == 4
            finally:
                Machine.query.delete()
                ProductionStage.query.delete()
                new_ids = {d.id for d in ProductionDepartment.query.all()} - before
                ProductionDepartment.query.filter(ProductionDepartment.id.in_(new_ids)).delete(
                    synchronize_session=False)
                db.session.commit()


# ── Dostęp i strona ───────────────────────────────────────────────────────────

class TestAccess:
    def test_requires_login(self, client):
        assert client.get(BASE + '/etapy').status_code in (302, 401)

    def test_forbidden_for_order_role(self, client, order_user):
        login(client, order_user, 'Order1234!')
        assert client.get(BASE + '/etapy').status_code == 403
        assert client.get(BASE + '/bufory/etykiety.pdf').status_code == 403
        assert post(client, '/etapy/nowy', name='X').status_code == 403

    def test_page_shows_stages_and_buffers(self, client, app):
        sid = _stage(app, 'T1 Wycinanie')
        with app.app_context():
            db.session.add(Buffer(code='A001', stage_id=sid, name='Przy laserze'))
            db.session.add(Buffer(code='B777'))
            db.session.commit()
        login(client, 'oper', 'Oper1234!')
        res = client.get(BASE + '/etapy')
        assert res.status_code == 200
        for text in ('T1 Wycinanie', 'A001', 'Przy laserze', 'B777', 'Bufory bez etapu'):
            assert text.encode('utf-8') in res.data


# ── Etapy ─────────────────────────────────────────────────────────────────────

class TestStages:
    def test_add_and_save_stage(self, client, app, department):
        login(client, 'oper', 'Oper1234!')
        post(client, '/etapy/nowy', name='T1 Nowy etap')
        with app.app_context():
            s = ProductionStage.query.filter_by(name='T1 Nowy etap').one()
            sid = s.id
            assert s.is_active and s.level == 'element' and not s.control_required
        post(client, f'/etapy/{sid}/zapisz', name='T1 Zmieniony', department_id=department,
             level='produkt', control_required='1', is_active='1')
        with app.app_context():
            s = db.session.get(ProductionStage, sid)
            assert (s.name, s.department_id, s.level, s.control_required, s.is_active) == (
                'T1 Zmieniony', department, 'produkt', True, True)
        post(client, f'/etapy/{sid}/zapisz', name='T1 Zmieniony', level='zly')
        with app.app_context():
            s = db.session.get(ProductionStage, sid)
            assert s.level == 'element' and not s.is_active and s.department_id is None

    def test_empty_name_rejected(self, client, app):
        login(client, 'oper', 'Oper1234!')
        post(client, '/etapy/nowy', name='  ')
        with app.app_context():
            assert ProductionStage.query.count() == 0

    def test_move_changes_order(self, client, app):
        a = _stage(app, 'T1 A', order=1)
        b = _stage(app, 'T1 B', order=2)
        login(client, 'oper', 'Oper1234!')
        post(client, f'/etapy/{b}/przesun/up')
        with app.app_context():
            names = [s.name for s in ProductionStage.query.order_by(ProductionStage.order)]
            assert names == ['T1 B', 'T1 A']
        post(client, f'/etapy/{b}/przesun/up')   # już pierwszy, nic się nie zmienia
        with app.app_context():
            assert [s.name for s in ProductionStage.query.order_by(ProductionStage.order)] == ['T1 B', 'T1 A']

    def test_delete_blocked_with_buffers_or_machines(self, client, app):
        sid = _stage(app)
        with app.app_context():
            db.session.add(Buffer(code='A001', stage_id=sid))
            db.session.commit()
        login(client, 'oper', 'Oper1234!')
        post(client, f'/etapy/{sid}/usun')
        with app.app_context():
            assert db.session.get(ProductionStage, sid) is not None
            Buffer.query.delete()
            db.session.commit()
        post(client, f'/etapy/{sid}/usun')
        with app.app_context():
            assert db.session.get(ProductionStage, sid) is None


# ── Maszyny ───────────────────────────────────────────────────────────────────

class TestMachines:
    def test_add_edit_delete_machine(self, client, app):
        sid = _stage(app)
        login(client, 'oper', 'Oper1234!')
        post(client, '/maszyny/nowa', stage_id=sid, name='Laser 9')
        post(client, '/maszyny/nowa', stage_id=sid, name='laser 9')   # duplikat w etapie
        with app.app_context():
            assert Machine.query.filter_by(stage_id=sid).count() == 1
            mid = Machine.query.filter_by(stage_id=sid).one().id
        post(client, f'/maszyny/{mid}/zapisz', name='Laser 10')
        with app.app_context():
            m = db.session.get(Machine, mid)
            assert m.name == 'Laser 10' and m.is_active is False
        post(client, f'/maszyny/{mid}/usun')
        with app.app_context():
            assert db.session.get(Machine, mid) is None


# ── Bufory ────────────────────────────────────────────────────────────────────

class TestBuffers:
    def test_add_normalizes_code_and_rejects_duplicates(self, client, app):
        sid = _stage(app)
        login(client, 'oper', 'Oper1234!')
        post(client, '/bufory/nowy', code=' a 001 ', name='Przy laserze', number='7', stage_id=sid)
        post(client, '/bufory/nowy', code='A001')       # duplikat
        post(client, '/bufory/nowy', code='   ')        # pusty
        with app.app_context():
            assert Buffer.query.count() == 1
            b = Buffer.query.one()
            assert (b.code, b.name, b.number, b.stage_id) == ('A001', 'Przy laserze', '7', sid)

    def test_range_creates_and_skips_existing(self, client, app):
        login(client, 'oper', 'Oper1234!')
        post(client, '/bufory/zakres', from_code='A001', to_code='A010')
        with app.app_context():
            assert Buffer.query.count() == 10
            assert Buffer.query.filter_by(code='A010').count() == 1
        post(client, '/bufory/zakres', from_code='A008', to_code='A012')
        with app.app_context():
            assert Buffer.query.count() == 12

    def test_range_without_prefix_keeps_width(self, client, app):
        login(client, 'oper', 'Oper1234!')
        post(client, '/bufory/zakres', from_code='0001', to_code='0003')
        with app.app_context():
            assert sorted(b.code for b in Buffer.query) == ['0001', '0002', '0003']

    @pytest.mark.parametrize('from_code,to_code', [
        ('A001', 'B010'), ('A010', 'A001'), ('A001', 'A999999'), ('AAA', 'AAB'), ('', ''),
    ])
    def test_bad_ranges_create_nothing(self, client, app, from_code, to_code):
        login(client, 'oper', 'Oper1234!')
        post(client, '/bufory/zakres', from_code=from_code, to_code=to_code)
        with app.app_context():
            assert Buffer.query.count() == 0

    def test_edit_code_warns_about_label(self, client, app):
        sid = _stage(app)
        with app.app_context():
            db.session.add(Buffer(code='A001'))
            db.session.add(Buffer(code='A002'))
            db.session.commit()
            bid = Buffer.query.filter_by(code='A001').one().id
        login(client, 'oper', 'Oper1234!')
        res = post(client, f'/bufory/{bid}/zapisz', code='z9', name='Nowa', number='3',
                   stage_id=sid, is_active='1')
        assert 'przestała pasować'.encode('utf-8') in res.data
        with app.app_context():
            b = db.session.get(Buffer, bid)
            assert (b.code, b.name, b.number, b.stage_id, b.is_active) == ('Z9', 'Nowa', '3', sid, True)
        # kod zajęty przez inny bufor
        post(client, f'/bufory/{bid}/zapisz', code='A002')
        with app.app_context():
            assert db.session.get(Buffer, bid).code == 'Z9'

    def test_delete_buffer(self, client, app):
        with app.app_context():
            b = Buffer(code='A001')
            db.session.add(b)
            db.session.commit()
            bid = b.id
        login(client, 'oper', 'Oper1234!')
        post(client, f'/bufory/{bid}/usun')
        with app.app_context():
            assert db.session.get(Buffer, bid) is None

    def test_find_buffer_normalizes_code(self, app):
        with app.app_context():
            db.session.add(Buffer(code='A001'))
            db.session.commit()
            assert find_buffer('a001').code == 'A001'
            assert find_buffer(' A 001 ').code == 'A001'
            assert find_buffer('A002') is None
            assert find_buffer('') is None
            assert norm_code(' e 010') == 'E010'

    def test_lookup_endpoint(self, client, app, order_user):
        sid = _stage(app, 'T1 Gięcie')
        with app.app_context():
            db.session.add(Buffer(code='A001', stage_id=sid))
            db.session.commit()
        assert client.get(BASE + '/bufory/znajdz?code=A001').status_code in (302, 401)
        login(client, order_user, 'Order1234!')   # odczyt dla każdego zalogowanego
        res = client.get(BASE + '/bufory/znajdz?code=a001')
        assert res.status_code == 200
        data = res.get_json()
        assert data['code'] == 'A001' and data['stage']['name'] == 'T1 Gięcie'
        assert client.get(BASE + '/bufory/znajdz?code=NIE').status_code == 404


# ── Etykiety PDF ──────────────────────────────────────────────────────────────

class TestLabels:
    def test_labels_pdf_one_page_per_buffer(self, client, app):
        sid = _stage(app)
        with app.app_context():
            for code in ('A001', 'A002', 'A003'):
                db.session.add(Buffer(code=code, stage_id=sid))
            db.session.add(Buffer(code='B001'))
            db.session.commit()
            ids = ','.join(str(b.id) for b in Buffer.query.filter(Buffer.code.like('A%')))
        login(client, 'oper', 'Oper1234!')
        res = client.get(f'{BASE}/bufory/etykiety.pdf?ids={ids}')
        assert res.status_code == 200 and res.mimetype == 'application/pdf'
        assert res.data.startswith(b'%PDF')
        assert res.data.count(b'/Type /Page\n') + res.data.count(b'/Type /Page ') == 3
        assert client.get(f'{BASE}/bufory/etykiety.pdf?stage_id={sid}').status_code == 200
        assert client.get(f'{BASE}/bufory/etykiety.pdf').status_code == 200

    def test_labels_pdf_404_when_nothing(self, client):
        login(client, 'oper', 'Oper1234!')
        assert client.get(f'{BASE}/bufory/etykiety.pdf').status_code == 404

    def test_long_code_still_renders(self, client, app):
        with app.app_context():
            db.session.add(Buffer(code='REGAL-HALA-2-POLKA-17-MIEJSCE-44'))
            db.session.commit()
        login(client, 'oper', 'Oper1234!')
        assert client.get(f'{BASE}/bufory/etykiety.pdf').data.startswith(b'%PDF')


# ── Plakietki pracowników działów ─────────────────────────────────────────────

class TestBadges:
    def _emp(self, app, dept_id, name='T1 Jan Kowalski', active=True):
        with app.app_context():
            e = DepartmentEmployee(department_id=dept_id, name=name, is_active=active)
            db.session.add(e)
            db.session.commit()
            return e.id, e.token

    def test_new_employee_gets_token(self, app, department):
        _, token = self._emp(app, department)
        assert token and len(token) == 32

    def test_backfill_fills_missing_tokens(self, app, department):
        eid, _ = self._emp(app, department)
        with app.app_context():
            DepartmentEmployee.query.filter_by(id=eid).update({'token': None})
            db.session.commit()
            _backfill_department_tokens()
            db.session.commit()
            assert db.session.get(DepartmentEmployee, eid).token

    def test_badge_scan_recognition(self, app, department):
        eid, token = self._emp(app, department)
        _, token2 = self._emp(app, department, name='T1 Anna Nowak', active=False)
        with app.app_context():
            assert parse_badge(f'{BADGE_PREFIX}{token}') == token
            assert parse_badge('PSH-EMP:abc') is None
            assert parse_badge('A001') is None
            assert find_department_employee(f'{BADGE_PREFIX}{token}').id == eid
            assert find_department_employee(f' psh-dep:{token} ').id == eid
            assert find_department_employee(f'{BADGE_PREFIX}{token2}') is None   # nieaktywny
            assert find_department_employee(f'{BADGE_PREFIX}nieznany') is None

    def test_new_token_invalidates_old_badge(self, client, app, department):
        eid, token = self._emp(app, department)
        login(client, 'oper', 'Oper1234!')
        post(client, f'/admin/employees/{eid}/token')
        with app.app_context():
            assert db.session.get(DepartmentEmployee, eid).token != token
            assert find_department_employee(f'{BADGE_PREFIX}{token}') is None

    def test_badge_pdfs(self, client, app, department, order_user):
        eid, _ = self._emp(app, department)
        self._emp(app, department, name='T1 Anna Nowak')
        login(client, 'oper', 'Oper1234!')
        one = client.get(f'{BASE}/admin/employees/{eid}/plakietka.pdf')
        assert one.status_code == 200 and one.data.startswith(b'%PDF')
        dept = client.get(f'{BASE}/admin/employees/plakietki.pdf?dept_id={department}')
        assert dept.status_code == 200 and dept.data.startswith(b'%PDF')
        assert client.get(f'{BASE}/admin/employees/plakietki.pdf?dept_id=999999').status_code == 404
        client.get('/logout')
        login(client, order_user, 'Order1234!')
        assert client.get(f'{BASE}/admin/employees/{eid}/plakietka.pdf').status_code == 403

    def test_move_employee_to_other_department(self, client, app, department):
        eid, _ = self._emp(app, department)
        with app.app_context():
            other = ProductionDepartment(name='T1 Drugi dział', order=91)
            db.session.add(other)
            db.session.commit()
            other_id = other.id
        try:
            login(client, 'oper', 'Oper1234!')
            res = post(client, f'/admin/employees/{eid}/przenies', dept_id=other_id)
            assert 'Przeniesiono'.encode('utf-8') in res.data
            with app.app_context():
                assert db.session.get(DepartmentEmployee, eid).department_id == other_id
            res = post(client, f'/admin/employees/{eid}/przenies', dept_id='')
            assert 'Wybierz dział'.encode('utf-8') in res.data
        finally:
            with app.app_context():
                DepartmentEmployee.query.filter_by(id=eid).delete()
                ProductionDepartment.query.filter_by(id=other_id).delete()
                db.session.commit()

    def test_employees_page_has_badge_actions(self, client, app, department):
        self._emp(app, department)
        login(client, 'oper', 'Oper1234!')
        res = client.get(f'{BASE}/admin/departments/{department}/employees')
        assert res.status_code == 200
        for text in ('Plakietki działu', 'Nowy kod plakietki', 'przenieś do'):
            assert text.encode('utf-8') in res.data
