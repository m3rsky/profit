"""Przejście całego scenariusza pokazu KJ Prestiż w prawdziwej przeglądarce (uruchamiane przez
test_prestiz_kj_browser.py). Wypisuje wiersz SMOKE_RESULT z JSON-em: błędy JavaScript i wynik każdego kroku."""
import io
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from PIL import Image

from app import app, db
import conftest

port = int(sys.argv[1])
workdir = tempfile.mkdtemp()
dbfile = os.path.join(workdir, 'kj.db').replace(chr(92), '/')
app.config.update({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + dbfile, 'SECRET_KEY': 'x',
                   'PKJ_UPLOAD_FOLDER': os.path.join(workdir, 'up')})
app.extensions.pop('sqlalchemy', None)
db.init_app(app)
with app.app_context():
    db.create_all()
    conftest._seed()
    from prestiz_kj import demo_data
    demo_data.seed_configuration()
    demo_data.seed_orders()

photo = os.path.join(workdir, 'wada.png')
Image.new('RGB', (40, 30), 'red').save(photo)

threading.Thread(target=lambda: app.run(port=port, use_reloader=False), daemon=True).start()
import urllib.request
for _ in range(60):                                   # czekamy, aż serwer odpowie (zamiast stałej przerwy)
    try:
        urllib.request.urlopen(f'http://127.0.0.1:{port}/login', timeout=1)
        break
    except Exception:
        time.sleep(0.25)

from playwright.sync_api import sync_playwright

base = f'http://127.0.0.1:{port}/kj-prestiz'
out = {'errors': []}


def watch(page):
    page.on('pageerror', lambda e: out['errors'].append(str(e)))


with sync_playwright() as p:
    b = p.chromium.launch()
    desk = b.new_context(viewport={'width': 1400, 'height': 900})
    phone = b.new_context(viewport={'width': 390, 'height': 844})
    d, m = desk.new_page(), phone.new_page()
    watch(d)
    watch(m)
    for pg in (d, m):
        pg.goto(f'http://127.0.0.1:{port}/login')
        pg.fill('#username', 'admin')
        pg.fill('#password', 'Admin1234!')
        pg.click('button.login-submit')

    # Scenariusz: krok 3 jest bieżący
    d.goto(base + '/scenariusz')
    out['steps'] = d.locator('.pkj-step').count()
    out['now_step'] = d.locator('.pkj-step--now h2').inner_text()
    out['done_before'] = d.locator('.pkj-step--done').count()

    # 3. Meldunek Spawania na stronie zlecenia
    d.click('text=Melduj na stronie zlecenia')
    d.locator('tr', has_text='Spawanie').get_by_role('button', name='Melduj operację').click()
    d.wait_for_selector('.rs:has-text("RW wstrzymane")', timeout=8000)
    out['rw_held'] = d.locator('.rs:has-text("RW wstrzymane")').count() >= 3

    # 4. Aplikacja KJ: Korpus + Drzwi, reszta OK, Drzwi NOK ze zdjęciem
    m.goto(base + '/m/?q=ZP-DEMO-001')
    out['phone_order'] = m.locator('.m-top').inner_text()
    m.uncheck('label:has-text("Płyta montażowa") input')
    m.click('text=Rozpocznij kontrolę')
    m.click('text=Pozostałe punkty: OK')
    card = m.locator('.m-pt', has_text='Spoina narożna kompletna')
    card.locator('.m-b-ng').click()
    m.wait_for_selector('.m-pt:has-text("Spoina narożna kompletna") .m-b-ng.on', timeout=8000)   # zapis zakończony
    card.locator('textarea').fill('Spoina niepełna przy narożniku')
    card.locator('textarea').blur()
    card.locator('input[type=file]').set_input_files(photo)
    m.wait_for_selector('.m-pt:has-text("Spoina narożna kompletna") .m-ph', timeout=8000)
    out['photo_added'] = card.locator('.m-ph').count() == 1
    m.click('#m-finish')
    out['nok_banner'] = 'Wykryto niezgodność' in m.locator('.m-banner').inner_text()

    # 5. Zawrót: Spawanie + ręcznie Szlifowanie, przyczyna, poprawka
    m.click('text=Zgłoś zawrót: Drzwi')
    m.check('label:has-text("Szlifowanie") input[name=extra]')
    m.select_option('select[name=reason_id]', label='Spoina niepełna')
    m.fill('textarea[name=description]', 'Dospawać narożnik')
    m.click('text=Utwórz zlecenie naprawcze')
    out['zn_created'] = 'Utworzono ZN-ZP-DEMO-001/1' in m.locator('.m-banner').inner_text()

    # 6. Meldunek operacji ZN na komputerze
    d.goto(base + '/zlecenia?typ=zn')
    d.get_by_role('link', name='ZN-ZP-DEMO-001/1').first.click()
    for n in (1, 2):
        d.get_by_role('button', name='Melduj operację').first.click()
        d.wait_for_selector(f'.rs-ok:has-text("Zakończona") >> nth={n - 1}', timeout=8000)
    out['zn_done'] = d.locator('.rs-ok', has_text='Zakończone').count() >= 1

    # Kontrola sprawdzająca: tylko punkt, który był NOK
    m.goto(base + '/m/?q=ZP-DEMO-001')
    m.click('text=Rozpocznij kontrolę sprawdzającą')
    out['recheck_points'] = m.locator('.m-pt').count()
    m.locator('.m-pt .m-b-ok').first.click()
    m.wait_for_selector('.m-pt .m-b-ok.on', timeout=8000)                                         # zapis zakończony
    m.click('#m-finish')
    out['recheck_ok'] = 'Kontrola zgodna' in m.locator('.m-banner').inner_text()

    # Scenariusz wykonany, raporty działają
    d.goto(base + '/scenariusz')
    out['done_after'] = d.locator('.pkj-step--done').count()
    out['all_done'] = d.locator('text=Scenariusz wykonany').count() == 1
    d.goto(base + '/raporty/niezgodnosci')
    out['register_row'] = d.locator('text=Spoina narożna kompletna').count() >= 1
    d.goto(base + '/raporty/fpy')
    d.wait_for_timeout(1500)
    out['fpy_canvas'] = d.locator('canvas#pkj-chart').count() == 1
    b.close()

print('SMOKE_RESULT ' + json.dumps(out))
