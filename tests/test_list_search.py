"""Wspólne wyszukiwanie list (makro _list_search.html, STANDARD_LIST.md)."""
import pytest
from test_app import login

LISTS = [
    ('/kontrola/', True, True),
    ('/reports', True, True),
    ('/qar/', True, True),
    ('/dokumentacja/', False, False),
    ('/produkcja/historia', True, False),
]


@pytest.mark.parametrize('url,has_qr,has_filters', LISTS)
def test_every_list_uses_shared_search(client, url, has_qr, has_filters):
    login(client, 'admin', 'Admin1234!')
    resp = client.get(url)
    assert resp.status_code == 200, url
    html = resp.get_data(as_text=True)
    assert 'class="ls-row"' in html and 'ls-submit' in html
    assert ('data-ls-qr' in html) == has_qr
    assert ('data-ls-toggle' in html) == has_filters


def test_scripts_loaded_once(client):
    login(client, 'admin', 'Admin1234!')
    html = client.get('/kontrola/').get_data(as_text=True)
    assert html.count('js/list-search.js') == 1
    assert client.get('/static/js/list-search.js').status_code == 200
