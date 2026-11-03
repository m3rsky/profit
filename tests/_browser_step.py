"""Test dymny podglądu 3D w prawdziwej przeglądarce (uruchamiany przez test_dokumentacja_browser.py).

Wgrywa przykładowe złożenie STEP do teczki Dokumentacji, klika "Podgląd 3D" i sprawdza, że model
wczytał się (liczba brył, gabaryt), a strona nie zgłasza błędów JavaScript."""
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from app import app, db
import conftest

port = int(sys.argv[1])
dbfile = os.path.join(tempfile.mkdtemp(), 'step.db').replace(chr(92), '/')
app.config.update({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + dbfile, 'SECRET_KEY': 'x',
                   'DOKUMENTACJA_UPLOAD_FOLDER': tempfile.mkdtemp()})
app.extensions.pop('sqlalchemy', None)
db.init_app(app)
with app.app_context():
    db.create_all()
    conftest._seed()

threading.Thread(target=lambda: app.run(port=port, use_reloader=False), daemon=True).start()
time.sleep(1.5)

from playwright.sync_api import sync_playwright

step_path = os.path.join(os.path.dirname(__file__), 'data', 'zlozenie-testowe.stp')
base = f'http://127.0.0.1:{port}'
out = {'errors': [], 'info': '', 'enabled': False}
with sync_playwright() as p:
    b = p.chromium.launch(args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
    ctx = b.new_context(viewport={'width': 1200, 'height': 800})
    pg = ctx.new_page()
    pg.on('pageerror', lambda e: out['errors'].append(str(e)))
    pg.goto(base + '/login')
    pg.fill('#username', 'admin')
    pg.fill('#password', 'Admin1234!')
    pg.click('button.login-submit')
    pg.wait_for_load_state()

    pg.goto(base + '/dokumentacja/nowa')
    pg.fill('input[name=title]', 'Teczka 3D')
    pg.fill('input[name=zo_number]', 'ZO/5/2026')
    pg.click('button[type=submit]')
    pg.wait_for_load_state()
    pg.set_input_files('#doc-file-input', step_path)
    pg.click('#dokumenty .doc-upload-actions button[type=submit]')
    pg.wait_for_load_state()
    out['zo_shown'] = 'ZO/5/2026' in pg.content()

    with ctx.expect_page() as popup:
        pg.click('text=Podgląd 3D')
    v = popup.value
    v.on('pageerror', lambda e: out['errors'].append(str(e)))
    v.wait_for_function("document.getElementById('info').textContent.includes('brył') "
                        "|| document.getElementById('info').textContent.startsWith('Błąd')", timeout=60000)
    out['info'] = v.inner_text('#info')
    out['enabled'] = not v.is_disabled('[data-view=iso]')
    out['canvas'] = v.evaluate("document.querySelector('#stage canvas').width > 0")

    # drzewo komponentów: nazwy, numeracja powtórzeń
    out['labels'] = v.evaluate("window.viewer3d.allNodes.filter(n => !n.body).map(n => n.label)")
    out['rows'] = v.locator('#tree .tr').count()
    out['auto_colors'] = v.evaluate("document.getElementById('colors').classList.contains('on')")

    # ukrywanie elementu odznaczeniem oka
    v.locator('#tree .tr', has_text='plate').locator('input[type=checkbox]').uncheck()
    out['hidden_after_uncheck'] = v.evaluate("window.viewer3d.parts.filter(p => !p.visible).length")
    out['plate_meshes'] = v.evaluate("window.viewer3d.allNodes.find(n => n.label === 'plate').all.length")
    v.click('#showall')
    out['hidden_after_showall'] = v.evaluate("window.viewer3d.parts.filter(p => !p.visible).length")

    # przezroczystość elementu suwakiem
    v.locator('#tree .tr', has_text='plate').locator('input[type=range]').fill('30')
    out['plate_opacity'] = v.evaluate("window.viewer3d.allNodes.find(n => n.label === 'plate').all.map(i => window.viewer3d.parts[i].mesh.material.opacity)")

    # klik w model zaznacza komponent; izolacja ukrywa resztę
    v.click('#showall')
    out['picked'] = v.evaluate("""() => { const r = document.querySelector('#stage canvas').getBoundingClientRect();
        for (let fx = 0.3; fx <= 0.7; fx += 0.05) for (let fy = 0.3; fy <= 0.7; fy += 0.05) {
          window.viewer3d.pickAt(r.left + r.width * fx, r.top + r.height * fy);
          if (window.viewer3d.selected()) return window.viewer3d.selected(); }
        return null; }""")
    if out['picked']:
        v.click('#s-iso')
        out['visible_after_iso'] = v.evaluate("window.viewer3d.parts.filter(p => p.visible).length")
    v.click('#showall')

    # przekrój
    v.click('#clipaxis [data-axis=x]')
    out['clip_on'] = v.evaluate("window.viewer3d.clipCount()")
    v.click('#clipaxis [data-axis=""]')
    out['clip_off'] = v.evaluate("window.viewer3d.clipCount()")

    # rentgen
    v.click('#xray')
    out['xray_transparent'] = v.evaluate("window.viewer3d.parts.every(p => p.mesh.material.transparent)")
    v.click('#xray')
    out['xray_off'] = v.evaluate("window.viewer3d.parts.every(p => !p.mesh.material.transparent || p.opacity < 1)")
    b.close()
print('STEP_RESULT ' + json.dumps(out))
