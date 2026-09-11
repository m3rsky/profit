"""
Testy REST API /api/v1/* (integracja zewnętrzna, np. Streamsoft ERP).
Uruchomienie: pytest tests/test_api_v1.py -v

Fixtures `app`/`client` i dane startowe pochodzą z tests/conftest.py
(współdzielone z tests/test_app.py).
"""
from conftest import API_KEY


def auth(client, method, path, **kwargs):
    headers = kwargs.pop('headers', {})
    headers['X-API-Key'] = API_KEY
    return getattr(client, method)(path, headers=headers, **kwargs)


# ── Autoryzacja ────────────────────────────────────────────────────────────────

class TestApiKeyAuth:
    def test_no_key_rejected(self, client):
        resp = client.get('/api/v1/templates')
        assert resp.status_code == 401

    def test_wrong_key_rejected(self, client):
        resp = client.get('/api/v1/templates', headers={'X-API-Key': 'wrong'})
        assert resp.status_code == 401

    def test_correct_key_accepted(self, client):
        resp = auth(client, 'get', '/api/v1/templates')
        assert resp.status_code == 200

    def test_post_without_csrf_token_still_works_with_key(self, client):
        """/api/v1/* jest zwolnione z CSRF sesyjnego, bo chroni je klucz API."""
        resp = auth(client, 'post', '/api/v1/qar', json={
            'title': 'CSRF-free test', 'description': 'opis',
        })
        assert resp.status_code == 201


# ── Zamówienia ─────────────────────────────────────────────────────────────────

class TestOrdersApi:
    def test_list_orders(self, client):
        resp = auth(client, 'get', '/api/v1/orders')
        assert resp.status_code == 200
        numbers = [o['number'] for o in resp.get_json()]
        assert 'ZAM-API-001' in numbers

    def test_order_detail(self, client):
        resp = auth(client, 'get', '/api/v1/orders')
        order_id = resp.get_json()[0]['id']
        detail = auth(client, 'get', f'/api/v1/orders/{order_id}')
        assert detail.status_code == 200
        assert detail.get_json()['number'] == 'ZAM-API-001'

    def test_create_order_missing_fields(self, client):
        resp = auth(client, 'post', '/api/v1/orders', json={'number': 'X'})
        assert resp.status_code == 400

    def test_create_order_success_with_template_match(self, client):
        resp = auth(client, 'post', '/api/v1/orders', json={
            'number': 'ZAM-API-002',
            'product_name': 'Kontrola ZO API',
            'client': 'ABC Sp. z o.o.',
            'quantity': 2,
            'due_date': '2026-08-01',
            'external_number': 'STR-5001',
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['ok'] is True
        assert data['template_matched'] == 'Kontrola ZO API'

        listed = auth(client, 'get', '/api/v1/orders?external_number=STR-5001').get_json()
        assert len(listed) == 1
        assert listed[0]['external_number'] == 'STR-5001'
        assert listed[0]['reports_total'] == 2

    def test_create_order_duplicate_number_rejected(self, client):
        resp = auth(client, 'post', '/api/v1/orders', json={
            'number': 'ZAM-API-001', 'product_name': 'X', 'client': 'Y',
        })
        assert resp.status_code == 409


# ── Checklisty / raporty ───────────────────────────────────────────────────────

class TestChecklistsApi:
    def _report_id(self, client):
        items = auth(client, 'get', '/api/v1/checklists').get_json()['items']
        return items[0]['id']

    def test_checklist_detail_has_new_fields(self, client):
        rid = self._report_id(client)
        detail = auth(client, 'get', f'/api/v1/checklists/{rid}').get_json()
        for key in ('started_at', 'duration_seconds', 'score', 'compliant'):
            assert key in detail

    def test_start_then_complete(self, client):
        rid = self._report_id(client)
        start = auth(client, 'post', f'/api/v1/checklists/{rid}/start')
        assert start.status_code == 200
        assert start.get_json()['started_at'] is not None

        complete = auth(client, 'post', f'/api/v1/checklists/{rid}/complete')
        assert complete.status_code == 200
        data = complete.get_json()
        assert data['status'] == 'completed'
        assert data['completed_at'] is not None

        again = auth(client, 'post', f'/api/v1/checklists/{rid}/complete')
        assert again.status_code == 400

    def test_create_checklist_via_api(self, client):
        templates = auth(client, 'get', '/api/v1/templates').get_json()
        tmpl_id = next(t['id'] for t in templates if t['name'] == 'Kontrola ZO API')
        resp = auth(client, 'post', '/api/v1/checklists', json={'template_id': tmpl_id})
        assert resp.status_code == 201
        assert resp.get_json()['ok'] is True

    def test_create_checklist_unknown_operator_rejected(self, client):
        templates = auth(client, 'get', '/api/v1/templates').get_json()
        tmpl_id = next(t['id'] for t in templates if t['name'] == 'Kontrola ZO API')
        resp = auth(client, 'post', '/api/v1/checklists', json={
            'template_id': tmpl_id, 'operator': 'nieznany',
        })
        assert resp.status_code == 404

    def test_create_completed_series_with_operator(self, client):
        """Zbiorczy wpis serii list kontrolnych 'jakby kontrola się odbyła' —
        przypadek zewnętrznego programu do ręcznego wpisywania serii do bazy."""
        templates = auth(client, 'get', '/api/v1/templates').get_json()
        tmpl_id = next(t['id'] for t in templates if t['name'] == 'Kontrola ZO API')
        resp = auth(client, 'post', '/api/v1/checklists', json={
            'template_id': tmpl_id,
            'template_name': 'Kontrola ZO API',
            'title': 'Seria dla klienta X',
            'quantity': 3,
            'operator': 'oper',
            'completed': True,
            'performed_at': '2026-09-01T08:00:00',
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['ok'] is True
        assert data['quantity'] == 3
        assert data['operator'] == 'oper'
        assert len(data['items']) == 3
        assert all(item['status'] == 'completed' for item in data['items'])
        assert data['items'][0]['title'] == 'Seria dla klienta X – 1/3'

        detail = auth(client, 'get', f"/api/v1/checklists/{data['items'][0]['id']}").get_json()
        assert detail['status'] == 'completed'
        assert detail['author'] == 'oper'
        assert detail['completed_at'].startswith('2026-09-01T08:00:00')
        assert detail['compliant'] is True
        assert all(item['result'] == 'ok' for item in detail['items'])

    def test_create_checklist_bad_performed_at_rejected(self, client):
        templates = auth(client, 'get', '/api/v1/templates').get_json()
        tmpl_id = next(t['id'] for t in templates if t['name'] == 'Kontrola ZO API')
        resp = auth(client, 'post', '/api/v1/checklists', json={
            'template_id': tmpl_id, 'performed_at': 'not-a-date',
        })
        assert resp.status_code == 400

    def test_list_users(self, client):
        resp = auth(client, 'get', '/api/v1/users')
        assert resp.status_code == 200
        usernames = [u['username'] for u in resp.get_json()]
        assert 'oper' in usernames
        assert 'admin' in usernames

    def test_list_installers(self, client):
        resp = auth(client, 'get', '/api/v1/installers')
        assert resp.status_code == 200
        names = [i['name'] for i in resp.get_json()]
        assert 'Jan Kowalski' in names

    def test_create_completed_checklist_assigns_installer(self, client):
        """Zadanie typu 'installer' (np. 'Montaż') musi dostać przypisanego
        montera, samo zaznaczenie OK nie wystarcza."""
        templates = auth(client, 'get', '/api/v1/templates').get_json()
        tmpl_id = next(t['id'] for t in templates if t['name'] == 'Kontrola ZO API')
        resp = auth(client, 'post', '/api/v1/checklists', json={
            'template_id': tmpl_id,
            'operator': 'oper',
            'completed': True,
            'installers': [{'name': 'Jan Kowalski', 'role': 'Obudowa'}],
        })
        assert resp.status_code == 201
        rid = resp.get_json()['id']

        detail = auth(client, 'get', f'/api/v1/checklists/{rid}').get_json()
        installer_item = next(i for i in detail['items'] if i['task'] == 'Montaż')
        assert installer_item['result'] == 'ok'
        assert installer_item['installers'] == [
            {'name': 'Jan Kowalski', 'role': 'Obudowa', 'is_at_fault': True}
        ]
        # Zadanie innego typu nie ma pola installers.
        other_item = next(i for i in detail['items'] if i['task'] == 'Zadanie API')
        assert other_item['installers'] is None

    def test_create_checklist_unknown_installer_rejected(self, client):
        templates = auth(client, 'get', '/api/v1/templates').get_json()
        tmpl_id = next(t['id'] for t in templates if t['name'] == 'Kontrola ZO API')
        resp = auth(client, 'post', '/api/v1/checklists', json={
            'template_id': tmpl_id, 'installers': ['Nieznany Monter'],
        })
        assert resp.status_code == 404

    def test_create_checklist_installers_as_plain_strings(self, client):
        templates = auth(client, 'get', '/api/v1/templates').get_json()
        tmpl_id = next(t['id'] for t in templates if t['name'] == 'Kontrola ZO API')
        resp = auth(client, 'post', '/api/v1/checklists', json={
            'template_id': tmpl_id, 'completed': True,
            'installers': ['Jan Kowalski'],
        })
        assert resp.status_code == 201
        rid = resp.get_json()['id']
        detail = auth(client, 'get', f'/api/v1/checklists/{rid}').get_json()
        installer_item = next(i for i in detail['items'] if i['task'] == 'Montaż')
        assert installer_item['installers'] == [
            {'name': 'Jan Kowalski', 'role': None, 'is_at_fault': True}
        ]


# ── Kosztorysy ─────────────────────────────────────────────────────────────────

class TestKosztorysApi:
    def test_prices(self, client):
        resp = auth(client, 'get', '/api/v1/prices')
        assert resp.status_code == 200
        codes = [p['code'] for p in resp.get_json()]
        assert 'dc01' in codes

    def test_labor_rates(self, client):
        resp = auth(client, 'get', '/api/v1/labor-rates')
        assert resp.status_code == 200
        assert resp.get_json()[0]['total'] == 6

    def test_quotes_list_and_detail(self, client):
        listed = auth(client, 'get', '/api/v1/quotes').get_json()
        assert len(listed) == 1
        detail = auth(client, 'get', f"/api/v1/quotes/{listed[0]['id']}").get_json()
        assert detail['dimensions']['width'] == 600


# ── Spawalnia ──────────────────────────────────────────────────────────────────

class TestSpawalniaApi:
    def test_create_and_read_zo(self, client):
        create = auth(client, 'post', '/api/v1/spawalnia/ZO-API-100', json={'quantity': 3})
        assert create.status_code == 201
        assert len(create.get_json()['created_ids']) == 3

        read = auth(client, 'get', '/api/v1/spawalnia/ZO-API-100')
        assert read.status_code == 200
        assert len(read.get_json()['records']) == 3

    def test_read_unknown_zo_returns_empty_list(self, client):
        resp = auth(client, 'get', '/api/v1/spawalnia/NIEISTNIEJACE-ZO')
        assert resp.status_code == 200
        assert resp.get_json()['records'] == []


# ── QAR ────────────────────────────────────────────────────────────────────────

class TestQarApi:
    def test_list_and_detail(self, client):
        listed = auth(client, 'get', '/api/v1/qar').get_json()
        assert any(r['number'] == 'QAR-2026-0001' for r in listed)
        rid = listed[0]['id']
        detail = auth(client, 'get', f'/api/v1/qar/{rid}')
        assert detail.status_code == 200

    def test_create_missing_fields(self, client):
        resp = auth(client, 'post', '/api/v1/qar', json={'title': 'only title'})
        assert resp.status_code == 400

    def test_create_success(self, client):
        resp = auth(client, 'post', '/api/v1/qar', json={
            'title': 'Nowa niezgodność', 'description': 'Opis problemu',
            'category': 'Spawanie',
        })
        assert resp.status_code == 201
        assert resp.get_json()['number'].startswith('QAR-')
