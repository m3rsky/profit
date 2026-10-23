"""Standard wyglądu list (STANDARD_LIST.md): statusy jako znaczniki .rs zamiast plakietek."""
from tests.test_app import login


class TestListStatusMarkers:
    def test_qar_list_uses_status_marker(self, client):
        login(client, 'admin', 'Admin1234!')
        html = client.get('/qar/').get_data(as_text=True)
        assert 'class="rs rs-warn"' in html and 'Otwarty' in html
        assert 'class="badge status-' not in html

    def test_final_control_list_uses_status_marker(self, client):
        login(client, 'admin', 'Admin1234!')
        html = client.get('/reports').get_data(as_text=True)
        assert 'badge badge-success">Ukończony' not in html and 'badge badge-warning">W trakcie' not in html
