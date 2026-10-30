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
