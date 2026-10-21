"""Testy jednego zapisu wszystkich zmian na stronach Punkty kontrolne i Elementy kontroli."""
from app import db
from models import ControlPoint, ControlElement
from test_app import login, _csrf
from test_kontrola_elementy import _clean, setup, operator_user  # noqa: F401 (fixtures)


def _points(app):
    with app.app_context():
        pts = ControlPoint.query.order_by(ControlPoint.order, ControlPoint.id).all()
        for p in pts:
            db.session.expunge(p)
        return pts


def _form(pts, **extra):
    data = {'save_first': '1'}
    for p in pts:
        data[f'title_{p.id}'] = p.title
        data[f'description_{p.id}'] = p.description or ''
        data[f'department_{p.id}'] = str(p.department_id or '')
        data[f'element_{p.id}'] = str(p.element_id or '')
        if p.is_active:
            data[f'active_{p.id}'] = '1'
    data.update(extra)
    return data


def _h(client):
    return {'X-CSRF-Token': _csrf(client)}


class TestPointsSaveAll:
    def test_page_is_one_form_with_single_save(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        page = client.get('/kontrola/points').get_data(as_text=True)
        assert page.count('Zapisz wszystko</button>') == 1 and 'name="title_' in page and 'name="new_title"' in page
        assert 'formaction=' in page and 'action="/kontrola/points/zapisz-wszystko"' in page
        assert page.count('<form') == 2                                   # filtr (GET) i jeden formularz zapisu
        el = client.get('/kontrola/elementy').get_data(as_text=True)
        assert el.count('Zapisz wszystko</button>') == 1 and 'name="name_' in el and el.count('<form') == 1

    def test_edits_many_points_and_adds_new_in_one_post(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        pts = _points(app)
        data = _form(pts, new_title='T10 Nowy punkt', new_element=str(setup['k']))
        data[f'title_{pts[0].id}'] = 'g Wygląd zmieniony'
        data[f'title_{pts[1].id}'] = 'dA Spoiny zmienione'
        data[f'department_{pts[1].id}'] = str(setup['b'])
        data.pop(f'active_{pts[2].id}')                                    # odznaczony Aktywny
        res = client.post('/kontrola/points/zapisz-wszystko', data=data, headers=_h(client), follow_redirects=True)
        text = res.get_data(as_text=True).lower()
        assert 'zapisano zmiany w punktach: 3' in text and 'dodano nowy punkt' in text
        with app.app_context():
            by_id = {p.id: p for p in ControlPoint.query.all()}
            assert by_id[pts[0].id].title == 'g Wygląd zmieniony'
            assert (by_id[pts[1].id].title, by_id[pts[1].id].department_id) == ('dA Spoiny zmienione', setup['b'])
            assert by_id[pts[2].id].is_active is False
            new = ControlPoint.query.filter_by(title='T10 Nowy punkt').one()
            assert new.element_id == setup['k'] and new.order > max(p.order for p in pts)

    def test_blank_title_skips_only_that_point_and_no_changes_message(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        pts = _points(app)
        data = _form(pts)
        res = client.post('/kontrola/points/zapisz-wszystko', data=data, headers=_h(client), follow_redirects=True)
        assert 'Brak zmian do zapisania' in res.get_data(as_text=True)
        data[f'title_{pts[0].id}'] = '   '
        data[f'title_{pts[1].id}'] = 'dA zmienione obok pustego'
        res = client.post('/kontrola/points/zapisz-wszystko', data=data, headers=_h(client), follow_redirects=True)
        assert 'nazwa jest wymagana' in res.get_data(as_text=True)
        with app.app_context():
            assert db.session.get(ControlPoint, pts[0].id).title == pts[0].title
            assert db.session.get(ControlPoint, pts[1].id).title == 'dA zmienione obok pustego'

    def test_move_and_delete_keep_pending_edits(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        pts = _points(app)
        data = _form(pts)
        data[f'title_{pts[3].id}'] = 'eD zmienione przed przesunięciem'
        client.post(f'/kontrola/points/{pts[1].id}/move/down', data=data, headers=_h(client))
        with app.app_context():
            assert db.session.get(ControlPoint, pts[3].id).title == 'eD zmienione przed przesunięciem'
            ordered = [p.id for p in ControlPoint.query.order_by(ControlPoint.order, ControlPoint.id)]
            assert ordered.index(pts[1].id) == 2                           # przesunięty o jedno miejsce w dół
        data = _form(pts)
        data[f'title_{pts[0].id}'] = 'g zmienione przed usunięciem'
        client.post(f'/kontrola/points/{pts[4].id}/delete', data=data, headers=_h(client))
        with app.app_context():
            assert db.session.get(ControlPoint, pts[4].id) is None
            assert db.session.get(ControlPoint, pts[0].id).title == 'g zmienione przed usunięciem'

    def test_requires_admin(self, client, app, setup, operator_user):
        for user, pw in (('oper', 'Oper1234!'), (operator_user, 'Operator123!')):
            client.get('/logout')
            login(client, user, pw)
            res = client.post('/kontrola/points/zapisz-wszystko', data={'new_title': 'X'}, headers=_h(client))
            assert res.status_code == 403
        with app.app_context():
            assert ControlPoint.query.filter_by(title='X').count() == 0


class TestElementsSaveAll:
    def test_edits_many_adds_new_and_rejects_duplicates(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        data = {'save_first': '1', 'new_name': 'T10 Nowy element',
                f'name_{setup["d"]}': 'T10 Drzwi lewe', f'active_{setup["d"]}': '1',
                f'name_{setup["k"]}': 'T10 Drzwi lewe', f'active_{setup["k"]}': '1',      # duplikat nazwy
                f'name_{setup["p"]}': 'T10 Płyta'}                                          # nieaktywny
        res = client.post('/kontrola/elementy/zapisz-wszystko', data=data, headers=_h(client), follow_redirects=True)
        text = res.get_data(as_text=True)
        assert 'już istnieje' in text and 'dodano nowy element' in text.lower()
        with app.app_context():
            assert db.session.get(ControlElement, setup['d']).name == 'T10 Drzwi lewe'
            assert db.session.get(ControlElement, setup['k']).name == 'T10 Korpus'          # duplikat odrzucony
            assert ControlElement.query.filter_by(name='T10 Nowy element').count() == 1
            assert db.session.get(ControlElement, setup['p']).is_active is False
        again = client.post('/kontrola/elementy/zapisz-wszystko', data={'new_name': 't10 nowy element'},
                            headers=_h(client), follow_redirects=True)
        assert 'już istnieje' in again.get_data(as_text=True)

    def test_move_and_delete_keep_pending_edits(self, client, app, setup):
        login(client, 'admin', 'Admin1234!')
        data = {'save_first': '1', f'name_{setup["d"]}': 'T10 Drzwi zmienione', f'active_{setup["d"]}': '1',
                f'name_{setup["k"]}': 'T10 Korpus zmieniony', f'active_{setup["k"]}': '1',
                f'name_{setup["p"]}': 'T10 Płyta'}
        client.post(f'/kontrola/elementy/{setup["d"]}/przesun/down', data=data, headers=_h(client))
        with app.app_context():
            assert db.session.get(ControlElement, setup['k']).name == 'T10 Korpus zmieniony'
            assert db.session.get(ControlElement, setup['d']).order > db.session.get(ControlElement, setup['k']).order
        # usuwanie zablokowane (ma punkty), ale pozostałe zmiany zostają zapisane
        data[f'name_{setup["p"]}'] = 'T10 Płyta zmieniona'
        client.post(f'/kontrola/elementy/{setup["d"]}/usun', data=data, headers=_h(client))
        with app.app_context():
            assert db.session.get(ControlElement, setup['d']) is not None
            assert db.session.get(ControlElement, setup['p']).name == 'T10 Płyta zmieniona'
        # usuwanie dozwolone (brak punktów) razem z zapisem pozostałych
        data[f'name_{setup["k"]}'] = 'T10 Korpus po usunięciu płyty'
        client.post(f'/kontrola/elementy/{setup["p"]}/usun', data=data, headers=_h(client))
        with app.app_context():
            assert db.session.get(ControlElement, setup['p']) is None
            assert db.session.get(ControlElement, setup['k']).name == 'T10 Korpus po usunięciu płyty'

    def test_requires_admin(self, client, app, setup, operator_user):
        for user, pw in (('oper', 'Oper1234!'), (operator_user, 'Operator123!')):
            client.get('/logout')
            login(client, user, pw)
            res = client.post('/kontrola/elementy/zapisz-wszystko', data={'new_name': 'X'}, headers=_h(client))
            assert res.status_code == 403
