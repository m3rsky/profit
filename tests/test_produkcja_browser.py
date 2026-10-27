"""Test dymny w prawdziwej przeglądarce: skrypty listy produkcyjnej nie zgłaszają błędów, a ukrywanie,
zmiana nazwy, szerokości i kolejności kolumny oraz przełączanie arkuszy działają po kliknięciu i przeciągnięciu.
Pomijany, gdy nie ma Playwrighta albo przeglądarki Chromium."""
import json
import os
import subprocess
import sys

import pytest

pytest.importorskip('playwright')


def test_header_actions_work_in_real_browser():
    script = os.path.join(os.path.dirname(__file__), '_browser_smoke.py')
    proc = subprocess.run([sys.executable, script, '5077'], capture_output=True, text=True, timeout=120,
                          cwd=os.path.join(os.path.dirname(__file__), '..'))
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith('SMOKE_RESULT ')), None)
    if line is None:
        if 'Executable doesn\'t exist' in proc.stderr or 'playwright install' in proc.stderr:
            pytest.skip('brak przeglądarki Chromium dla Playwrighta')
        pytest.fail('test przeglądarkowy nie zwrócił wyniku:\n' + proc.stdout[-1500:] + proc.stderr[-1500:])
    out = json.loads(line[len('SMOKE_RESULT '):])
    assert out['errors'] == [], f"błędy JavaScript na stronie: {out['errors']}"
    assert out['search_keeps_dates'], f"wyszukiwanie gubi zakres dat: {out['search_url']}"
    assert out['hidden'], 'kolumna nie została ukryta po kliknięciu w nagłówek'
    assert out['renamed'], 'nazwa kolumny nie zmieniła się po kliknięciu w nagłówek'
    assert out['move_popup'], f"kolumna nie przesunęła się przyciskiem w okienku nagłówka: {out['keys']}"
    assert out['move_drag'], f"kolumna nie przeniosła się przeciągnięciem nagłówka: {out['keys']}"
    assert out['switched'], 'lista wyboru arkusza nie przełączyła arkusza'
    assert out['resize_live'] > 50 and out['resize_saved'] > 50, f"szerokość nie zmienia się: {out}"
