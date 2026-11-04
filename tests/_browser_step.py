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

    # geometria na siatkach syntetycznych: rozwinięcie blachy, otwory, pomiary między cechami
    out['geom'] = v.evaluate(open(os.path.join(os.path.dirname(__file__), '_geom_checks.js'), encoding='utf-8').read())

    # rozstrzelenie
    set_expl = "(k) => { const s = document.getElementById('explpos'); s.value = k; s.dispatchEvent(new Event('input')); }"
    v.evaluate(set_expl, 60)
    out['exploded_moved'] = v.evaluate("window.viewer3d.parts.filter(p => p.mesh.position.length() > 0.5).length")
    v.evaluate(set_expl, 0)
    out['exploded_reset'] = v.evaluate("window.viewer3d.parts.filter(p => p.mesh.position.length() > 1e-9).length")

    # pomiar: dwa kliknięcia w model dają wynik; przy rozstrzeleniu pomiar jest zablokowany
    v.click('#measbtn')
    pts = v.evaluate("""() => { const V = window.viewer3d, r = document.querySelector('#stage canvas').getBoundingClientRect(), hits = [];
        for (let fx = 0.25; fx <= 0.75; fx += 0.05) for (let fy = 0.25; fy <= 0.75; fy += 0.05) {
          const x = r.left + r.width * fx, y = r.top + r.height * fy; if (V.castRay(x, y)) hits.push([x, y]); }
        return hits; }""")
    out['meas_hits'] = len(pts)
    if len(pts) >= 2:
        v.mouse.click(pts[0][0], pts[0][1])
        out['meas_hint1'] = v.inner_text('#meas-hint')
        v.mouse.click(pts[-1][0], pts[-1][1])
        out['meas_results'] = v.evaluate("window.viewer3d.tools().measure.results().length")
        out['meas_row'] = v.evaluate("window.viewer3d.tools().measure.results()[0].rows[0]")
        v.click('#measclear')
        v.evaluate(set_expl, 50)
        v.mouse.click(pts[0][0], pts[0][1])
        out['meas_exploded_hint'] = v.inner_text('#meas-hint')
        v.evaluate(set_expl, 0)
    v.click('#measbtn')

    # okno rozwinięcia blachy otwiera się i zwraca wynik albo czytelny komunikat
    v.click('#showall')
    v.locator('#tree .tr', has_text='plate').locator('.nm').click()
    v.click('#s-flat')
    v.wait_for_function("document.getElementById('flat-info').textContent.length > 5 && !document.getElementById('flat-info').textContent.startsWith('Obliczanie')", timeout=20000)
    out['flat_modal'] = v.evaluate("document.getElementById('flat').classList.contains('open')")
    out['flat_text'] = v.inner_text('#flat-info')
    v.click('#flat-close')

    # zapis pomiaru, widoczność (pojedyncza, grupa, wszystkie), odtworzenie po przeładowaniu, usuwanie
    mres = "window.viewer3d.tools().measure.results()"
    v.click('#showall')
    v.click('#measbtn')
    v.mouse.click(pts[0][0], pts[0][1])
    v.mouse.click(pts[-1][0], pts[-1][1])
    out['draft_count'] = v.evaluate(f"{mres}.length")
    out['draft_saved'] = v.evaluate(f"{mres}[0].saved")
    out['draft_element'] = v.evaluate(f"{mres}[0].element")
    out['draft_collapsed'] = v.evaluate("document.querySelectorAll('#meas-list .mbody').length === 0")
    v.locator('#meas-list .mtitle').first.click()                                    # rozwiń: dane i nazwa
    v.locator('#meas-list .mname').first.fill('Pomiar testowy')
    v.locator('#meas-list .mname').first.dispatch_event('change')
    v.locator('#meas-list .msave').first.click()
    v.wait_for_function(f"{mres}[0] && {mres}[0].saved === true", timeout=15000)
    out['saved_id'] = v.evaluate(f"{mres}[0].dbId")
    lbl = v.locator('#meas-labels .mlabel').first
    b0 = lbl.bounding_box()
    cx, cy = b0['x'] + b0['width'] / 2, b0['y'] + b0['height'] / 2
    v.mouse.move(cx, cy); v.mouse.down(); v.mouse.move(cx + 90, cy + 50, steps=8); v.mouse.up()
    b1 = lbl.bounding_box()
    out['label_moved'] = [round(b1['x'] - b0['x']), round(b1['y'] - b0['y'])]
    out['label_offset'] = v.evaluate(f"{mres}[0].offsets['3d']")
    out['leader_shown'] = v.evaluate("(() => { const l = document.querySelector('.meas-leaders line'); return !!l && getComputedStyle(l).display !== 'none'; })()")
    v.wait_for_function("window.viewer3d.listMeasurements().then(l => !!(l[0].data.offsets && l[0].data.offsets['3d']))", timeout=15000)
    out['label_offset_server'] = v.evaluate("window.viewer3d.listMeasurements().then(l => l[0].data.offsets['3d'])")
    v.locator('#meas-list .mtop input[type=checkbox]').first.uncheck()
    out['hidden_one'] = v.evaluate(f"!{mres}[0].group.visible")
    v.locator('#meas-list .mghead input[type=checkbox]').first.check()
    out['shown_group'] = v.evaluate(f"{mres}[0].group.visible")
    v.click('#meashide')
    time.sleep(0.4)
    out['leader_hidden'] = v.evaluate("[...document.querySelectorAll('.meas-leaders line')].every(l => getComputedStyle(l).display === 'none')")
    out['label_hidden'] = v.evaluate("[...document.querySelectorAll('#meas-labels .mlabel')].every(l => getComputedStyle(l).display === 'none')")
    out['hidden_all'] = v.evaluate(f"{mres}.every(r => !r.group.visible)")
    v.click('#measshow')
    time.sleep(0.4)
    out['leader_back'] = v.evaluate("[...document.querySelectorAll('.meas-leaders line')].some(l => getComputedStyle(l).display !== 'none')")
    out['shown_all'] = v.evaluate(f"{mres}.every(r => r.group.visible)")
    v.reload()
    v.wait_for_function(f"window.viewer3d && window.viewer3d.tools().measure && {mres}.length === 1", timeout=60000)
    out['restored'] = v.evaluate(f"(() => {{ const r = {mres}[0]; return {{ saved: r.saved, label: r.label, element: r.element, rows: r.rows, visible: r.group.visible }}; }})()")
    out['label_offset_restored'] = v.evaluate(f"{mres}[0].offsets['3d']")
    v.locator('#meas-labels .mlabel').first.dblclick()
    out['label_offset_after_reset'] = v.evaluate(f"{mres}[0].offsets['3d'] === undefined")
    v.wait_for_function("window.viewer3d.listMeasurements().then(l => !(l[0].data.offsets && l[0].data.offsets['3d']))", timeout=15000)
    out['restored_collapsed'] = v.evaluate("({ bodies: document.querySelectorAll('#meas-list .mbody').length, title: document.querySelector('#meas-list .mtitle .mnm').textContent, value: document.querySelector('#meas-list .mtitle .mval').textContent })")
    v.locator('#meas-list .mtitle').first.click()
    out['restored_name_input'] = v.locator('#meas-list .mname').first.input_value()
    v.locator('#meas-list .mname').first.fill('Zmieniona nazwa')
    v.locator('#meas-list .mrename').first.click()
    v.wait_for_function("document.querySelector('#meas-list .mtitle .mnm').textContent === 'Zmieniona nazwa'", timeout=15000)
    out['renamed_title'] = v.evaluate("document.querySelector('#meas-list .mtitle .mnm').textContent")
    out['renamed_server'] = v.evaluate("window.viewer3d.listMeasurements().then(l => l[0].label)")
    v.locator('#meas-list .mrow button:text-is("Usuń")').first.click()
    v.wait_for_function(f"{mres}.length === 0", timeout=15000)
    out['after_delete'] = v.evaluate("fetch(location.pathname + '/pomiary').then(r => r.json()).then(l => l.length)")

    # wymiary na wykroju (syntetyczny wspornik jako bryła 9999): pomiar z 3D przeniesiony na wykrój,
    # mierzenie wprost na wykroju, zapis, ukrywanie, odtworzenie po przeładowaniu, usuwanie
    geom_js = open(os.path.join(os.path.dirname(__file__), '_geom_checks.js'), encoding='utf-8').read()
    zt = v.evaluate(geom_js)['zTop']
    v.evaluate("""(zt) => window.viewer3d.saveMeasurement({ label: 'z 3D', element: 'Wspornik', data: { space: '3d',
        A: { k: 'pt', p: [43, 0, zt], part: 9999 }, B: { k: 'pt', p: [0, 0, 2], part: 9999 },
        rows: [['Odległość', '54.20 mm']], segs: [[[43, 0, zt], [0, 0, 2]]] } }).then(rec => window.viewer3d.tools().measure.load([rec]))""", zt)
    # szkic (niezapisany) w 3D oraz pomiar na płaszczyźnie, który nie ma wymiaru na wykroju
    v.evaluate("""(zt) => { const M = window.viewer3d.tools().measure;
        M.compose({ k: 'pt', p: [0, 0, 2], part: 9999 }, { k: 'pt', p: [0, 50, 2], part: 9999 });
        M.compose({ k: 'plane', n: [0, 0, 1], anchor: [20, 25, 2], part: 9999 }, { k: 'pt', p: [43, 0, zt], part: 9999 }); }""", zt)
    open_flat = "window.viewer3d.tools().flat.open([{ index: 9999, label: 'Wspornik' }], 'Wspornik')"
    v.evaluate(open_flat)
    v.wait_for_function("document.querySelectorAll('#flat-dims .fd-row').length >= 1", timeout=20000)
    items = "window.viewer3d.tools().flat.dims.items()"
    out['fd_transferred'] = v.evaluate(f"{items}.filter(i => i.src === '3d').map(i => ({{ flat: i.rows[0][1], model3d: i.rows3d[0][1], saved: i.saved }}))")
    out['fd_skipped'] = v.inner_text('#flat-dims .fd-skipped')
    v.click('#flat-dims .fd-bar button:has-text("Mierz na wykroju")')
    far = v.evaluate("""() => { const f = window.viewer3d.tools().flat.dims.feats(), svg = document.querySelector('#flat-svg svg'), m = svg.getScreenCTM();
        const toClient = p => { const pt = svg.createSVGPoint(); pt.x = p[0]; pt.y = -p[1]; const q = pt.matrixTransform(m); return [q.x, q.y]; };
        let best = null;
        for (const a of f.corners) for (const b of f.corners) { const dd = Math.hypot(a.p[0] - b.p[0], a.p[1] - b.p[1]); if (!best || dd > best.dd) best = { dd, a, b }; }
        return { a: toClient(best.a.p), b: toClient(best.b.p), dd: best.dd }; }""")
    v.mouse.click(far['a'][0], far['a'][1])
    v.mouse.click(far['b'][0], far['b'][1])
    out['fd_measured'] = v.evaluate(f"{items}.filter(i => i.src === 'flat').map(i => ({{ saved: i.saved, v: i.rows[0][1] }}))")
    out['fd_expect'] = far['dd']
    out['fd_collapsed'] = v.evaluate("document.querySelectorAll('#flat-dims .fd-body').length === 0")
    v.locator('#flat-dims .fd-row.draft .fd-save').first.click()
    v.wait_for_function(f"{items}.some(i => i.src === 'flat' && i.saved)", timeout=15000)
    out['fd_flat_in_3d'] = v.evaluate("window.viewer3d.tools().measure.results().filter(r => r.space === 'flat').map(r => { const s = r.segs[0]; return { len3d: Math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1], s[1][2] - s[0][2]), flat: r.rows[0][1], note: r.note, saved: r.saved }; })")
    def drag_label(index, dx, dy):
        lab = v.locator('#flat-svg .dim-label').nth(index)
        bb = lab.bounding_box()
        x, y = bb['x'] + bb['width'] / 2, bb['y'] + bb['height'] / 2
        v.mouse.move(x, y); v.mouse.down(); v.mouse.move(x + dx, y + dy, steps=8); v.mouse.up()
    n_labels = v.evaluate("document.querySelectorAll('#flat-svg .dim-label').length")
    drag_label(n_labels - 1, 70, 40)                      # ostatnia etykieta: właśnie zapisany pomiar z wykroju
    drag_label(0, -50, -30)                               # pierwsza: pomiar z modelu 3D (zapisany)
    out['fd_offsets'] = v.evaluate(f"{items}.map(i => ({{ src: i.src, saved: i.saved, flat: (i.offsets || {{}}).flat || null }}))")
    out['fd_leaders'] = v.evaluate("[...document.querySelectorAll('#flat-svg svg g line')].filter(l => l.style.display !== 'none' && l.getAttribute('stroke-dasharray')).length")
    v.wait_for_function("window.viewer3d.listMeasurements().then(l => l.filter(r => r.data.offsets && r.data.offsets.flat).length >= 2)", timeout=15000)
    out['fd_offsets_server'] = v.evaluate("window.viewer3d.listMeasurements().then(l => l.filter(r => r.data.offsets && r.data.offsets.flat).map(r => r.data.space || '3d').sort())")
    labels = "document.querySelectorAll('#flat-svg svg g text').length"
    out['fd_labels_shown'] = v.evaluate(labels)
    v.click('#flat-dims .fd-bar button:has-text("Ukryj wszystkie")')
    out['fd_labels_hidden'] = v.evaluate(labels)
    v.click('#flat-dims .fd-bar button:has-text("Pokaż wszystkie")')
    out['fd_labels_again'] = v.evaluate(labels)
    out['fd_server'] = v.evaluate("window.viewer3d.listMeasurements().then(l => l.map(r => ({ space: r.data.space || '3d', K: r.data.K })))")
    v.click('#flat-close')
    v.reload()
    v.wait_for_function("window.viewer3d && window.viewer3d.tools().measure && window.viewer3d.tools().measure.results().length >= 1", timeout=60000)
    out['fd_3d_list_after_reload'] = v.evaluate("window.viewer3d.tools().measure.results().length")
    v.evaluate(geom_js)
    v.evaluate(open_flat)
    v.wait_for_function("document.querySelectorAll('#flat-dims .fd-row').length >= 2", timeout=20000)
    out['fd_restored'] = v.evaluate(f"{items}.map(i => ({{ src: i.src, saved: i.saved, drawable: i.drawable }}))")
    out['fd_offsets_restored'] = v.evaluate(f"{items}.filter(i => i.saved).map(i => ({{ src: i.src, has: !!((i.offsets || {{}}).flat) }}))")
    v.locator('#flat-dims .fd-row[data-src=flat] .fd-title').first.click()
    v.locator('#flat-dims .fd-row[data-src=flat] .fd-name').first.fill('Wymiar z wykroju')
    v.locator('#flat-dims .fd-row[data-src=flat] .fd-rename').first.click()
    v.wait_for_function("document.querySelector('#flat-dims .fd-row[data-src=flat] .fd-nm').textContent === 'Wymiar z wykroju'", timeout=15000)
    out['fd_renamed_server'] = v.evaluate("window.viewer3d.listMeasurements().then(l => l.filter(r => r.data.space === 'flat').map(r => r.label))")
    out['fd_title_after_rename'] = v.evaluate("document.querySelector('#flat-dims .fd-row[data-src=flat] .fd-nm').textContent")
    v.locator('#flat-dims .fd-row[data-src=flat] .fd-actions button:text-is("Usuń")').first.click()
    v.wait_for_function(f"{items}.filter(i => i.src === 'flat').length === 0", timeout=15000)
    out['fd_server_after_delete'] = v.evaluate("window.viewer3d.listMeasurements().then(l => l.map(r => r.data.space || '3d'))")

    # telefon: panel jako dolna szuflada przesuwana palcem (prawdziwe zdarzenia dotyku), sekcje zwijane dotknięciem
    viewer_url = v.url
    v.close()                      # programowy WebGL tej strony zabierałby procesor analizie STEP na stronie telefonu
    mctx = b.new_context(viewport={'width': 390, 'height': 800}, is_mobile=True, has_touch=True, storage_state=ctx.storage_state())
    mp = mctx.new_page()
    mp.goto(viewer_url)
    mp.wait_for_function("document.getElementById('info').textContent.includes('brył')", timeout=90000)
    cdp = mctx.new_cdp_session(mp)

    def touch(points):
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': points[0][0], 'y': points[0][1]}]})
        for x, y in points[1:]:
            cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x, 'y': y}]})
            time.sleep(0.016)
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
        time.sleep(0.5)

    def handle_y():
        return int(mp.evaluate("(() => { const r = document.getElementById('sheet-handle').getBoundingClientRect(); return r.top + r.height / 2; })()"))

    def swipe(dy):
        y0 = handle_y()
        touch([(195, y0 + dy * i / 12) for i in range(13)])

    sheet = "({ state: document.getElementById('panel').dataset.state, h: Math.round(document.getElementById('panel').getBoundingClientRect().height) })"
    out['mob_start'] = mp.evaluate(sheet)
    swipe(-300)
    out['mob_up'] = mp.evaluate(sheet)
    swipe(-400)
    out['mob_max'] = mp.evaluate(sheet)
    swipe(700)
    out['mob_down'] = mp.evaluate(sheet)
    touch([(195, handle_y())])
    out['mob_tap1'] = mp.evaluate(sheet)
    touch([(195, handle_y())])
    out['mob_tap2'] = mp.evaluate(sheet)
    out['mob_sections'] = mp.evaluate("[...document.querySelectorAll('#panel .sec')].map(s => [s.dataset.sec, s.classList.contains('collapsed')])")
    touch([(195, handle_y())])                                   # otwórz szufladę, potem rozwiń przekrój dotknięciem nagłówka
    mp.locator('#panel .sec[data-sec=clip] .sec-h').tap()
    out['mob_clip_open'] = mp.evaluate("!document.querySelector('#panel .sec[data-sec=clip]').classList.contains('collapsed')")
    out['mob_hint'] = mp.inner_text('#hint')
    mctx.close()
    b.close()
print('STEP_RESULT ' + json.dumps(out))
