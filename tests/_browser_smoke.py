"""Test dymny listy produkcyjnej w prawdziwej przeglądarce (uruchamiany przez test_produkcja_browser.py).

Wypisuje JSON z wynikami: czy skrypty strony nie zgłaszają błędów oraz czy działają ukrywanie kolumny,
zmiana jej nazwy i zmiana szerokości przeciągnięciem."""
import io
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
from openpyxl import Workbook

from app import app, db
import conftest

port = int(sys.argv[1])
dbfile = os.path.join(tempfile.mkdtemp(), 'smoke.db').replace(chr(92), '/')
app.config.update({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + dbfile, 'SECRET_KEY': 'x',
                   'PRODUKCJA_FOLDER': tempfile.mkdtemp()})
app.extensions.pop('sqlalchemy', None)
db.init_app(app)
with app.app_context():
    db.create_all()
    conftest._seed()

today = datetime.combine(datetime.now().date(), datetime.min.time())
wb = Workbook()
ws = wb.active
ws.title = 'PSH'
ws.append(['ZAM', 'CZAS REALIZACJI', 'TERMIN REALIZACJI', 'NAZWA TOW.', 'ILOŚĆ', 'ODBIORCA', 'NUMER ZO', 'GIĘCIE', 'SPAW.', 'KJ'])
for n in range(20):
    ws.append([today, 3, today, f'PSH 600X800X{300 + n} dlugi tekst', 1, 'KLIENT TESTOWY', f'ZO-{n}', 'AB', 'CD', 'EF'])
xlsx = os.path.join(tempfile.mkdtemp(), 'l.xlsx')
buf = io.BytesIO()
wb.save(buf)
open(xlsx, 'wb').write(buf.getvalue())

threading.Thread(target=lambda: app.run(port=port, use_reloader=False), daemon=True).start()
time.sleep(1.5)

from playwright.sync_api import sync_playwright

base = f'http://127.0.0.1:{port}'
out = {'errors': []}
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 1500, 'height': 900})
    pg.on('pageerror', lambda e: out['errors'].append(str(e)))
    pg.goto(base + '/login')
    pg.fill('#username', 'admin')
    pg.fill('#password', 'Admin1234!')
    pg.click('button.login-submit')
    pg.goto(base + '/produkcja/import')
    pg.set_input_files('#file', xlsx)
    pg.click('#import-btn')
    pg.wait_for_url('**/produkcja/')
    with app.app_context():
        from models import ProdSheet
        sid = ProdSheet.query.first().id
    sheet_url = f'{base}/produkcja/sheet/{sid}'
    pg.goto(sheet_url)
    pg.wait_for_selector('#prod-table')

    def header(c):
        return pg.locator(f'th.colhead[data-c="{c}"]')

    # ukrywanie: klik w nagłówek, "Ukryj kolumnę"
    header(8).click(position={'x': 10, 'y': 10})
    with pg.expect_navigation():
        pg.locator('.col-pop button', has_text='Ukryj').click()
    pg.wait_for_selector('#prod-table')
    out['hidden'] = header(8).count() == 0

    # zmiana nazwy: klik w nagłówek, "Zmień nazwę", wpisanie, Enter
    header(7).click(position={'x': 10, 'y': 10})
    pg.locator('.col-pop button', has_text='Zmień nazwę').click()
    pg.fill('.col-pop input', 'Gięcie testowe')
    with pg.expect_navigation():
        pg.press('.col-pop input', 'Enter')
    pg.wait_for_selector('#prod-table')
    out['renamed'] = 'Gięcie testowe' in header(7).inner_text()

    # szerokość: przeciągnięcie uchwytu, zapis po odświeżeniu
    before = header(3).bounding_box()['width']
    hb = pg.locator('.col-rs[data-c="3"]').bounding_box()
    x, y = hb['x'] + hb['width'] / 2, hb['y'] + hb['height'] / 2
    pg.mouse.move(x, y)
    pg.mouse.down()
    pg.mouse.move(x + 60, y, steps=5)
    pg.mouse.move(x + 120, y, steps=5)
    pg.mouse.up()
    time.sleep(0.5)
    out['resize_live'] = header(3).bounding_box()['width'] - before
    pg.reload()
    pg.wait_for_selector('#prod-table')
    out['resize_saved'] = header(3).bounding_box()['width'] - before
    b.close()
print('SMOKE_RESULT ' + json.dumps(out))
