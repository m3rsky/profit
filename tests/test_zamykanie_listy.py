"""Reguły zamykania listy kontrolnej z przeglądarki: braki blokujące i ostrzeżenia o zdjęciach."""
import pytest

from app import app as flask_app, db
from models import ChecklistSession, ChecklistTemplate, Photo, Report, ReportItem, Task, User
from test_app import login, _csrf


@pytest.fixture(autouse=True)
def _clean(app):
    yield
    with flask_app.app_context():
        ids = [r.id for r in Report.query.filter(Report.title.like('BRAKI-%')).all()]
        if ids:
            item_ids = [i.id for i in ReportItem.query.filter(ReportItem.report_id.in_(ids)).all()]
            if item_ids:
                Photo.query.filter(Photo.report_item_id.in_(item_ids)).delete(synchronize_session=False)
            ChecklistSession.query.filter(ChecklistSession.report_id.in_(ids)).delete(synchronize_session=False)
            ReportItem.query.filter(ReportItem.report_id.in_(ids)).delete(synchronize_session=False)
            Report.query.filter(Report.id.in_(ids)).delete(synchronize_session=False)
        Task.query.filter(Task.title.like('BRAKI-%')).delete(synchronize_session=False)
        db.session.commit()


def _report(result=None, notes=None, requires_photo=False, photo=False):
    """Lista z jednym punktem w podanym stanie; zwraca (id raportu, id punktu)."""
    with flask_app.app_context():
        user = User.query.filter_by(username='oper').first()
        tmpl = ChecklistTemplate.query.filter_by(name='Test szablon').first()
        task = Task(category_id=tmpl.categories[0].id, title='BRAKI-punkt', requires_photo=requires_photo)
        db.session.add(task)
        report = Report(user_id=user.id, template_id=tmpl.id, title='BRAKI-1', status='in_progress',
                        report_type='kontroler')
        db.session.add(report)
        db.session.flush()
        item = ReportItem(report_id=report.id, task_id=task.id, result=result, notes=notes)
        db.session.add(item)
        db.session.flush()
        if photo:
            db.session.add(Photo(report_item_id=item.id, filename='braki.jpg'))
        db.session.commit()
        return report.id, item.id


def _close(client, rid, confirm=False):
    login(client, 'oper', 'Oper1234!')
    data = {'_csrf_token': _csrf(client)}
    if confirm:
        data['confirm_warnings'] = '1'
    return client.post(f'/checklist/{rid}/complete', data=data)


def _status(rid):
    with flask_app.app_context():
        return db.session.get(Report, rid).status


class TestBlokada:
    @pytest.mark.parametrize('confirm', [False, True])
    def test_nieoceniony_punkt_blokuje_takze_z_potwierdzeniem(self, client, confirm):
        rid, item_id = _report(result=None)
        resp = _close(client, rid, confirm=confirm)
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200 and _status(rid) == 'in_progress'
        assert 'Brak oceny' in html and f'#item-{item_id}' in html
        assert 'Zamknij bez zdjęć' not in html

    @pytest.mark.parametrize('notes', [None, '', '   '])
    def test_ng_bez_adnotacji_blokuje(self, client, notes):
        rid, _ = _report(result='ng', notes=notes)
        resp = _close(client, rid, confirm=True)
        assert 'NG bez adnotacji' in resp.get_data(as_text=True) and _status(rid) == 'in_progress'


class TestOstrzezenia:
    def test_ng_bez_zdjecia_wymaga_tylko_potwierdzenia(self, client):
        rid, _ = _report(result='ng', notes='rysa na drzwiach')
        resp = _close(client, rid)
        html = resp.get_data(as_text=True)
        assert 'NG bez zdjęcia' in html and 'Zamknij bez zdjęć' in html and _status(rid) == 'in_progress'
        assert _close(client, rid, confirm=True).status_code == 302
        assert _status(rid) == 'completed'

    def test_brak_wymaganego_zdjecia_mozna_pominac(self, client):
        rid, _ = _report(result='ok', requires_photo=True)
        assert 'Brak wymaganego zdjęcia' in _close(client, rid).get_data(as_text=True)
        _close(client, rid, confirm=True)
        assert _status(rid) == 'completed'


class TestBezPrzeszkod:
    @pytest.mark.parametrize('kwargs', [
        {'result': 'ok'},
        {'result': 'dw'},                                   # DW nie wymaga komentarza
        {'result': 'na', 'requires_photo': True},           # N/A: zdjęcie nie dotyczy
        {'result': 'ng', 'notes': 'opis', 'photo': True},
        {'result': 'ok', 'requires_photo': True, 'photo': True},
    ])
    def test_zamyka_od_razu(self, client, kwargs):
        rid, _ = _report(**kwargs)
        assert _close(client, rid).status_code == 302
        assert _status(rid) == 'completed'


class TestKreator:
    @pytest.mark.parametrize('result', ['ok', 'ng', 'na', 'dw'])
    def test_kazda_ocena_ma_klase_po_ponownym_otwarciu(self, client, result):
        rid, item_id = _report(result=result, notes='x')
        login(client, 'oper', 'Oper1234!')
        html = client.get(f'/checklist/{rid}').get_data(as_text=True)
        assert f'class="checklist-item result-{result}" id="item-{item_id}"' in html
