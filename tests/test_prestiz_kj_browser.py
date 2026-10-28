"""Test w prawdziwej przeglądarce: cały scenariusz pokazu KJ Prestiż od meldunku Spawania, przez kontrolę ze zdjęciem
na telefonie (390 px), zawrót i zlecenie naprawcze, po kontrolę sprawdzającą i raporty. Pomijany, gdy nie ma
Playwrighta albo przeglądarki Chromium."""
import json
import os
import subprocess
import sys

import socket

import pytest

pytest.importorskip('playwright')


def test_presentation_scenario_in_real_browser():
    script = os.path.join(os.path.dirname(__file__), '_browser_kj.py')
    with socket.socket() as s:                       # wolny port, żeby poprzednie uruchomienie nie przeszkadzało
        s.bind(('127.0.0.1', 0))
        port = str(s.getsockname()[1])
    proc = subprocess.run([sys.executable, script, port], capture_output=True, text=True, timeout=240,
                          cwd=os.path.join(os.path.dirname(__file__), '..'))
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith('SMOKE_RESULT ')), None)
    if line is None:
        if "Executable doesn't exist" in proc.stderr or 'playwright install' in proc.stderr:
            pytest.skip('brak przeglądarki Chromium dla Playwrighta')
        pytest.fail('test przeglądarkowy nie zwrócił wyniku:\n' + proc.stdout[-1500:] + proc.stderr[-2500:])
    out = json.loads(line[len('SMOKE_RESULT '):])
    assert out['errors'] == [], f"błędy JavaScript: {out['errors']}"
    assert out['steps'] == 7 and out['now_step'].startswith('Meldunek operacji Spawanie')
    assert out['done_before'] == 2
    assert out['rw_held'], 'po meldunku Spawania RW materiałów nie jest wstrzymane'
    assert 'ZP-DEMO-001' in out['phone_order']
    assert out['photo_added'], 'zdjęcie z telefonu nie dodało się do punktu'
    assert out['nok_banner'], 'kontrola z NOK nie pokazała komunikatu o niezgodności'
    assert out['zn_created'], 'zawrót nie utworzył zlecenia naprawczego ZN-ZP-DEMO-001/1'
    assert out['zn_done'], 'operacje ZN nie zakończyły się po meldunku'
    assert out['recheck_points'] == 1, 'kontrola sprawdzająca ma pokazywać tylko punkt, który był NOK'
    assert out['recheck_ok'], 'kontrola sprawdzająca OK nie zamknęła zawrotu'
    assert out['done_after'] == 7 and out['all_done'], 'scenariusz nie jest oznaczony jako wykonany'
    assert out['register_row'] and out['fpy_canvas']
