"""Test w prawdziwej przeglądarce: podgląd 3D pliku STEP z Dokumentacji wczytuje model (three.js + WASM
w Web Workerze). Pomijany, gdy nie ma Playwrighta albo przeglądarki Chromium."""
import json
import os
import subprocess
import sys

import pytest

pytest.importorskip('playwright')


def test_step_viewer_loads_model_in_real_browser():
    script = os.path.join(os.path.dirname(__file__), '_browser_step.py')
    proc = subprocess.run([sys.executable, script, '5078'], capture_output=True, text=True, timeout=180,
                          cwd=os.path.join(os.path.dirname(__file__), '..'))
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith('STEP_RESULT ')), None)
    if line is None:
        if 'Executable doesn\'t exist' in proc.stderr or 'playwright install' in proc.stderr:
            pytest.skip('brak przeglądarki Chromium dla Playwrighta')
        pytest.fail('test przeglądarkowy nie zwrócił wyniku:\n' + proc.stdout[-1500:] + proc.stderr[-1500:])
    out = json.loads(line[len('STEP_RESULT '):])
    assert out['errors'] == [], f"błędy JavaScript na stronie: {out['errors']}"
    assert out['zo_shown'], 'numer ZO nie jest widoczny w teczce'
    assert 'Błąd' not in out['info'], f"podgląd zgłosił błąd: {out['info']}"
    assert 'brył' in out['info'] and 'gabaryt' in out['info'], out['info']
    assert out['enabled'] and out['canvas']
    assert out['back_to_project'], 'przycisk powrotu powinien wrócić do projektu w tym samym oknie'
    assert out['same_window'], 'podgląd 3D powinien otwierać się w tym samym oknie, bez nowej karty'

    # panel elementów, przekrój, klik w model, rentgen
    assert 'plate' in out['labels'] and 'rod-assembly' in out['labels'], out['labels']
    assert 'l-bracket-assembly #1' in out['labels'] and 'l-bracket-assembly #2' in out['labels'], out['labels']
    assert out['rows'] >= len(out['labels'])
    assert not out['auto_colors'], 'plik z własnymi kolorami nie powinien dostać kolorów automatycznych'
    assert out['hidden_after_uncheck'] == out['plate_meshes'] > 0, 'oko nie ukryło elementu'
    assert out['hidden_after_showall'] == 0, '"Pokaż wszystko" nie przywróciło widoczności'
    assert out['plate_opacity'] and all(abs(o - 0.3) < 0.01 for o in out['plate_opacity']), out['plate_opacity']
    assert out['picked'], 'klik w model nie zaznaczył żadnego elementu'
    assert 0 < out['visible_after_iso'] < 18, 'Izoluj nie ukryło reszty'
    assert out['clip_on'] == 1 and out['clip_off'] == 0, 'przekrój nie włącza się i wyłącza'
    assert out['xray_transparent'], 'tryb rentgen nie uczynił modelu półprzezroczystym'
    assert out['xray_off']

    # rozstrzelenie, pomiar, okno rozwinięcia
    assert out['exploded_moved'] > 0 and out['exploded_reset'] == 0, 'rozstrzelenie nie przesuwa lub nie wraca'
    assert out['meas_hits'] >= 2, 'brak punktów trafienia w model do pomiaru'
    assert out['meas_hint1'].startswith('Wybrano'), out['meas_hint1']
    assert out['meas_results'] == 1 and out['meas_row'][1].endswith('mm'), out['meas_row']
    assert 'rozstrzelenie' in out['meas_exploded_hint'], out['meas_exploded_hint']
    assert out['flat_modal'] and len(out['flat_text']) > 5, out['flat_text']

    # zapis pomiarów, widoczność i odtworzenie po przeładowaniu
    assert out['draft_count'] == 1 and out['draft_saved'] is False, out
    assert out['draft_element'], 'pomiar powinien mieć przypisany element'
    assert out['saved_id'], 'pomiar nie został zapisany na serwerze'
    assert out['hidden_one'] and out['shown_group'] and out['hidden_all'] and out['shown_all'], out
    r = out['restored']
    assert r['saved'] and r['label'] == 'Pomiar testowy' and r['element'] == out['draft_element'], r
    assert r['rows'] and r['rows'][0][1].endswith('mm') and r['visible'], r
    assert out['after_delete'] == 0, 'pomiar nie został usunięty na serwerze'
    assert out['draft_collapsed'] and out['fd_collapsed'], 'wiersze pomiarów powinny być domyślnie zwinięte'
    rc = out['restored_collapsed']
    assert rc['bodies'] == 0 and rc['title'] == 'Pomiar testowy' and rc['value'].endswith('mm'), rc   # lista: nazwa i wartość, bez szczegółów
    assert out['restored_name_input'] == 'Pomiar testowy', 'po rozwinięciu pole nazwy ma pokazywać obecną nazwę'
    assert out['renamed_title'] == 'Zmieniona nazwa' and out['renamed_server'] == 'Zmieniona nazwa', out
    assert out['fd_renamed_server'] == ['Wymiar z wykroju'] and out['fd_title_after_rename'] == 'Wymiar z wykroju', out

    # wymiary na wykroju: przeniesienie z 3D, pomiar wprost na wykroju, zapis, ukrywanie
    t = {x['model3d']: x for x in out['fd_transferred']}
    assert len(out['fd_transferred']) == 2, out['fd_transferred']
    saved3d = t['54.20 mm']
    assert saved3d['saved'] and abs(float(saved3d['flat'].split()[0]) - 76.28) < 0.02, f'wymiar 3D przeniesiony na wykrój: {saved3d}'
    draft = next(x for x in out['fd_transferred'] if not x['saved'])           # niezapisany szkic też trafia na wykrój
    assert abs(float(draft['flat'].split()[0]) - 50.0) < 0.02, draft
    assert 'płaszczyzn' in out['fd_skipped'] and 'Nie przeniesiono 1' in out['fd_skipped'], out['fd_skipped']
    m = out['fd_measured']
    assert len(m) == 1 and not m[0]['saved'] and abs(float(m[0]['v'].split()[0]) - out['fd_expect']) < 0.01, (m, out['fd_expect'])
    assert out['fd_labels_shown'] == 3 and out['fd_labels_hidden'] == 0 and out['fd_labels_again'] == 3, out
    f3 = out['fd_flat_in_3d']                                                  # zapisany na wykroju pojawia się w 3D
    assert len(f3) == 1 and f3[0]['saved'] and 'wykroju' in f3[0]['note'], f3
    assert any(abs(f3[0]['len3d'] - want) < 0.06 for want in (75.83, 73.74)), f3
    assert sorted(x['space'] for x in out['fd_server']) == ['3d', 'flat'], out['fd_server']
    assert [x['K'] for x in out['fd_server'] if x['space'] == 'flat'] == [0.5], out['fd_server']
    assert out['fd_3d_list_after_reload'] == 1, 'pomiary z wykroju nie powinny trafiać do listy 3D'
    assert sorted((x['src'], x['saved'], x['drawable']) for x in out['fd_restored']) == [('3d', True, True), ('flat', True, True)], out['fd_restored']
    assert out['fd_server_after_delete'] == ['3d'], out['fd_server_after_delete']

    # przeciąganie czerwonej etykiety wymiaru: w 3D (piksele) i na wykroju (mm), zapis na serwerze
    assert abs(out['label_moved'][0] - 90) <= 2 and abs(out['label_moved'][1] - 50) <= 2, out['label_moved']
    assert out['label_offset'] and abs(out['label_offset'][0] - 90) <= 2 and abs(out['label_offset'][1] - 50) <= 2, out['label_offset']
    assert out['leader_shown'], 'po przesunięciu etykiety powinna być widoczna linia łącząca'
    assert out['label_offset_server'] == out['label_offset'], (out['label_offset_server'], out['label_offset'])
    assert out['label_offset_restored'] == out['label_offset'], 'położenie etykiety nie wróciło po przeładowaniu'
    assert out['label_offset_after_reset'], 'dwuklik nie przywrócił domyślnego położenia'
    assert out['label_hidden'] and out['leader_hidden'], 'ukryty pomiar nie może zostawiać linii łączącej na ekranie'
    assert out['leader_back'], 'po ponownym pokazaniu pomiaru linia łącząca powinna wrócić'
    flat_moved = {(x['src'], x['saved']): x['flat'] for x in out['fd_offsets']}
    assert flat_moved[('flat', True)] and flat_moved[('3d', True)], out['fd_offsets']
    assert out['fd_leaders'] >= 2, 'przesunięte etykiety na wykroju powinny mieć linię łączącą'
    assert out['fd_offsets_server'] == ['3d', 'flat'], out['fd_offsets_server']
    assert sorted((x['src'], x['has']) for x in out['fd_offsets_restored'] if x['src'] == 'flat') == [('flat', True)], out['fd_offsets_restored']

    # zmiana nazwy elementu w drzewie
    assert out['ren_tree'] == {'label': 'Płyta montażowa', 'custom': True, 'text': True}, out['ren_tree']
    assert out['ren_chip'].endswith('Płyta montażowa'), out['ren_chip']
    assert out['ren_chip_button'].endswith('Płyta 2'), out['ren_chip_button']
    assert out['ren_after_reload'] == 'Płyta 2', 'własna nazwa elementu nie przetrwała przeładowania'
    assert out['ren_reset'] is False and out['ren_reset_after_reload'] == 'plate', 'pusta nazwa powinna przywrócić oryginalną'

    # telefon: szuflada na dole sterowana palcem
    assert out['mob_start']['state'] == 'min' and out['mob_start']['h'] == 52, out['mob_start']
    assert out['mob_up']['state'] == 'mid' and out['mob_up']['h'] > 250, out['mob_up']
    assert out['mob_max']['state'] == 'max' and out['mob_max']['h'] > 600, out['mob_max']
    assert out['mob_down']['state'] == 'min' and out['mob_down']['h'] == 52, out['mob_down']
    assert out['mob_tap1']['state'] == 'mid' and out['mob_tap2']['state'] == 'min', (out['mob_tap1'], out['mob_tap2'])
    assert dict(out['mob_sections']) == {'clip': True, 'expl': True, 'meas': False, 'tree': False}, out['mob_sections']
    assert out['mob_clip_open'], 'dotknięcie nagłówka sekcji nie rozwinęło przekroju'
    assert 'palec' in out['mob_hint'], out['mob_hint']

    # geometria na siatkach syntetycznych (znane wymiary, dokładność do 0.02 mm)
    g = out['geom']
    br = g['bracket']
    assert br['ok'], br['errors']
    assert abs(br['t'] - 2.0) < 1e-3 and br['bends'] == 1 and abs(br['angle'] - 90) < 0.5 and abs(br['rIn'] - 3.0) < 0.01, br
    for k in ('05', '033'):
        got, want = br['dims' + k], br['expect' + k]
        assert abs(got[0] - want[0]) < 0.02 and abs(got[1] - want[1]) < 0.02, f'wymiar wykroju K={k}: {got} oczekiwano {want}'
    assert br['holes'] == 0
    assert g['dxf']['eof'] and g['dxf']['contour'] and g['dxf']['bend'] and g['dxf']['lines'] > 4, g['dxf']
    assert g['notSheet'] is False, 'samo płaskie ciało nie powinno się rozwijać'
    w = g['washer']
    assert w['type'] == 'plane' and len(w['circles']) == 2, w
    assert abs(w['circles'][0]['d'] - 8) < 0.01 and abs(w['circles'][1]['d'] - 20) < 0.01, w
    for c in w['circles']:
        assert abs(c['c'][0] - 7) < 0.01 and abs(c['c'][1] - 5) < 0.01 and abs(c['c'][2]) < 0.01, c
    ff = g['flatFeats']
    assert ff['lines'] == 5 and ff['bendLines'] == 1 and ff['corners'] == 4 and ff['circles'] == 0, ff
    tr = g['transfer']
    for k in ('inner', 'outer', 'line'):
        assert tr[k] is not None, k
        assert abs(float(tr[k][1].split()[0]) - 76.28) < 0.02, (k, tr[k])    # po prostowaniu gięcia, nie 54.20 jak w 3D
    assert abs(tr['straight3d'] - 54.20) < 0.01 and tr['outside'] is False and tr['plane'] is False, tr
    assert abs(g['flatDiag']['got'] - g['flatDiag']['expect']) < 0.02, g['flatDiag']
    p = g['pairs']
    assert p['ptpt'][0] == ['Odległość', '5.00 mm'], p['ptpt']
    assert p['circleLine'][0] == ['Odległość', '10.00 mm'] and p['circleLine'][1] == ['Średnica otworu', 'Ø6.00'], p['circleLine']
    assert p['planeParallel'][0] == ['Odległość (równoległe)', '1.20 mm'], p['planeParallel']
    assert p['planeAngle'][0] == ['Kąt między płaszczyznami', '30.00°'], p['planeAngle']
    assert p['lineSkew'][0] == ['Najmniejsza odległość', '5.00 mm'] and p['lineSkew'][1] == ['Kąt', '90.00°'], p['lineSkew']
    assert p['pointPlane'][0] == ['Odległość od płaszczyzny', '5.50 mm'], p['pointPlane']
