"""Testy menu: kolejność pozycji, przestawianie (zapis na koncie), ukryte i usunięte moduły."""
import json
import re

import pytest

from app import app as flask_app, db, NAV_DEFAULT
from models import User
from test_app import login, _csrf


def _order_of(html):
    """Klucze menu w kolejności wizualnej (CSS order) z kodu strony."""
    pairs = re.findall(r'data-nav-key="(\w+)" style="order:(\d+)"', html)
    return [k for k, _ in sorted(pairs, key=lambda p: int(p[1]))]


def _save(client, payload):
    return client.post('/menu/kolejnosc', json=payload, headers={'X-CSRF-Token': _csrf(client)})


@pytest.fixture(autouse=True)
def _reset(app):
    def wipe():
        with app.app_context():
            User.query.update({'menu_order': None})
            db.session.commit()
    wipe()
    yield
    wipe()


class TestMenuOrder:
    def test_admin_default_order_and_labels(self, client):
        login(client, 'admin', 'Admin1234!')
        html = client.get('/').get_data(as_text=True)
        keys = _order_of(html)
        assert keys[:7] == ['dashboard', 'kontrola_etapowa', 'kontrola_koncowa', 'qar', 'lista_produkcyjna',
                            'stanowisko', 'ustawienia']
        assert 'Kontrola etapowa' in html and 'Kontrola zamówień' not in html
        assert html.index('>Dashboard<') < html.index('>Kontrola etapowa<') < html.index('>Raporty QAR<')

    def test_removed_and_hidden_items(self, client):
        login(client, 'admin', 'Admin1234!')
        html = client.get('/').get_data(as_text=True)
        for text in ('Zadania QA', 'Kosztorysy', 'Ceny materiałów', 'Stawki robocizny', 'Typy szaf',
                     'Katalog produktów', 'Import CSV'):
            assert text not in html, text
        # Użytkownicy widzi tylko super admin
        assert 'Użytkownicy' not in html
        assert 'Statystyki' in html and 'Dziennik zdarzeń' in html
        client.get('/logout')
        login(client, 'super', 'Super1234!')
        assert 'Użytkownicy' in client.get('/').get_data(as_text=True)
        assert '>Zamówienia<' not in html
        assert client.get('/zadania-qa/').status_code == 404

    def test_save_order_is_per_user_and_applies(self, client, app):
        login(client, 'admin', 'Admin1234!')
        new = ['qar', 'dashboard', 'kontrola_koncowa']
        assert _save(client, {'order': new}).get_json() == {'ok': True}
        keys = _order_of(client.get('/').get_data(as_text=True))
        rest = [k for k in NAV_DEFAULT if k not in new and k not in ('nowa_kontrola', 'zlecenia_qa')]   # tylko pozycje panelu admina
        assert keys[:3] == new and keys[3:] == rest
        with app.app_context():
            assert json.loads(User.query.filter_by(username='admin').one().menu_order) == new
            assert User.query.filter_by(username='oper').one().menu_order is None
        client.get('/logout')
        login(client, 'oper', 'Oper1234!')                                  # inny użytkownik: bez zmian
        other = _order_of(client.get('/').get_data(as_text=True))
        assert other[0] != 'qar'

    def test_reset_restores_default(self, client):
        login(client, 'admin', 'Admin1234!')
        _save(client, {'order': ['qar']})
        assert _save(client, {'reset': True}).get_json() == {'ok': True}
        keys = _order_of(client.get('/').get_data(as_text=True))
        assert keys[:3] == ['dashboard', 'kontrola_etapowa', 'kontrola_koncowa']

    def test_validation_and_auth(self, client):
        assert _save_anon(client).status_code in (302, 401, 403)
        login(client, 'admin', 'Admin1234!')
        for bad in ({'order': ['nieznany']}, {'order': 'qar'}, {'order': [1, 2]}, {}):
            assert _save(client, bad).status_code == 400, bad
        assert _save(client, {'order': ['qar', 'qar', 'dashboard']}).get_json()['ok']       # duplikaty scalone

    def test_controller_nav_sortable_and_renamed(self, client):
        login(client, 'oper', 'Oper1234!')
        html = client.get('/').get_data(as_text=True)
        assert 'data-nav-sortable' in html and 'data-nav-edit' in html and 'Kontrola etapowa' in html
        assert 'Zadania QA' not in html and 'Kosztorysy' not in html
        _save(client, {'order': ['qar', 'kontrola_koncowa']})
        keys = _order_of(client.get('/').get_data(as_text=True))
        assert keys[:2] == ['qar', 'kontrola_koncowa']

    def test_other_roles_have_no_reordering(self, client, app):
        with app.app_context():
            if not User.query.filter_by(username='menu_planowanie').first():
                u = User(username='menu_planowanie', email='menu_planowanie@test.pl', role='planowanie')
                u.set_password('Rola1234!')
                db.session.add(u)
                db.session.commit()
        login(client, 'menu_planowanie', 'Rola1234!')
        html = client.get('/produkcja/', follow_redirects=True).get_data(as_text=True)
        assert 'data-nav-sortable' not in html and 'data-nav-edit' not in html

    def test_assets_present(self, client):
        login(client, 'admin', 'Admin1234!')
        assert 'js/nav-sort.js' in client.get('/').get_data(as_text=True)
        assert client.get('/static/js/nav-sort.js').status_code == 200


def _save_anon(client):
    client.get('/logout')
    return client.post('/menu/kolejnosc', json={'order': ['qar']})
