"""Biblioteki interfejsu są w static/vendor: aplikacja nie ładuje niczego z zewnętrznych serwerów."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Adres zewnętrzny w miejscu, które każe przeglądarce coś pobrać (znacznik, import CSS, src w skrypcie).
EXTERNAL = re.compile(
    r"""(<script[^>]+src=|<link[^>]+href=|<img[^>]+src=|@import\s+(?:url\()?|url\(|\.src\s*=)\s*["']?(?:https?:)?//""",
    re.I,
)
STATIC_REF = re.compile(r"""url_for\('static', filename='(vendor/[^']+)'\)|['"]/static/(vendor/[^'"?]+)""")


def _sources():
    yield from (ROOT / 'templates').rglob('*.html')
    yield from (ROOT / 'static' / 'js').glob('*.js')
    yield from (ROOT / 'static' / 'css').glob('*.css')
    yield ROOT / 'static' / 'sw.js'
    yield ROOT / 'static' / 'offline.html'


def test_no_external_assets():
    found = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if EXTERNAL.search(line):
                found.append(f'{path.relative_to(ROOT)}:{number}: {line.strip()[:120]}')
    assert not found, 'Zasoby z zewnętrznych serwerów:\n' + '\n'.join(found)


def test_referenced_vendor_files_exist():
    refs = set()
    for path in _sources():
        for a, b in STATIC_REF.findall(path.read_text(encoding='utf-8')):
            refs.add(a or b)
    assert len(refs) == 4, refs
    missing = [r for r in refs if not (ROOT / 'static' / r).is_file()]
    assert not missing, missing


def test_icon_fonts_exist():
    css = ROOT / 'static' / 'vendor' / 'bootstrap-icons' / 'bootstrap-icons.min.css'
    fonts = re.findall(r'url\("([^"?]+)', css.read_text(encoding='utf-8'))
    assert fonts
    assert all((css.parent / f).is_file() for f in fonts), fonts


def test_vendor_files_are_served(client):
    for url in ('/static/vendor/html5-qrcode/html5-qrcode.min.js',
                '/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2',
                '/static/vendor/chartjs/chart.umd.js',
                '/static/vendor/sortablejs/Sortable.min.js'):
        assert client.get(url).status_code == 200, url
