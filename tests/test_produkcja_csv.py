"""Testy wczytywania i pobierania CSV (eksport pojedynczego arkusza z Excela, UTF-8)."""
import io
import json

import pytest

from app import db
from models import (User, ProdFile, ProdSheet, ProdRow, ProdSheetAccess, ProdStageColumn)
from test_app import login, logout, _csrf
from test_produkcja import (prod_folder, planner, _import, _admin, _sheet_id,  # noqa: F401
                            _row_id, PRODUCT)

# po zastosowaniu układu wzorcowego PSH (ROZP. = DATA, ODBIORCA = NAZWA ODBIORCY, SPAW. = SPAW. OB.)
HEADER_LAYOUT = 'ZAM;DATA;CZAS REALIZACJI;TERMIN REALIZACJI;NAZWA TOW.;ILOŚĆ;NAZWA ODBIORCY;NUMER ZO;KOLOR;SPAW. OB.;KJ'
HEADER = 'ZAM;ROZP.;CZAS REALIZACJI;TERMIN REALIZACJI;NAZWA TOW.;ILOŚĆ;ODBIORCA;NUMER ZO;KOLOR;SPAW.;KJ'


def _csv(*lines, bom=True, enc='utf-8'):
    text = '\r\n'.join(lines) + '\r\n'
    return (('﻿' if bom else '') + text).encode(enc)


def _post_csv(client, files, names):
    data = {'csv': [(io.BytesIO(content), fname) for fname, content in files],
            'sheet_name': names, '_csrf_token': _csrf(client)}
    return client.post('/produkcja/import-csv', data=data, content_type='multipart/form-data',
                       follow_redirects=True)


def test_csv_creates_new_sheet_with_headers_and_types(app, client):
    _admin(client)
    content = _csv(HEADER,
                   f'2026-09-07 00:00:00;08.09.2026;3;2026-09-10;{PRODUCT};2;ELEKTRO TEST;007/25;RAL 7035;MK;',
                   ';;;;;;;;;;',                      # pusty wiersz z eksportu Excela
                   '23-06;24-06;;;PSH 862;1,5;KLIENT;ZO-C2;;;')
    resp = _post_csv(client, [('PRODUKCJA - PSH.csv', content)], ['NOWY'])
    assert 'Wczytano z CSV: NOWY (2)' in resp.get_data(as_text=True)
    with app.app_context():
        sh = ProdSheet.query.filter_by(name='NOWY').first()
        assert sh.header_row == 1 and sh.mapping['zo'] == 7 and sh.mapping['product'] == 4
        rows = ProdRow.query.filter_by(sheet_id=sh.id).order_by(ProdRow.position).all()
        assert [r.position for r in rows] == [2, 4]            # numery wierszy jak w Excelu
        d = json.loads(rows[0].data)
        assert d[0] == '2026-09-07' and d[1] == '2026-09-08' and d[5] == 2 and d[7] == '007/25'
        assert json.loads(rows[1].data)[5] == 1.5               # przecinek dziesiętny
        assert sh.formula_cols == [] and {s.label for s in ProdStageColumn.query.filter_by(sheet_id=sh.id)} == {'SPAW. OB.', 'KJ'}


def test_csv_semicolon_comma_and_cp1250(app, client):
    _admin(client)
    comma = _csv(HEADER.replace(';', ','), f'2026-09-07,,3,,{PRODUCT},1,KLIENT,ZO-A,,,', bom=False)
    cp = _csv(HEADER, f'2026-09-07;;3;;{PRODUCT};1;ŁÓDŹ KLIENT;ZO-B;;;', bom=False, enc='cp1250')
    _post_csv(client, [('a.csv', comma), ('b.csv', cp)], ['A', 'B'])
    with app.app_context():
        assert ProdRow.query.filter_by(zo='ZO-A').count() == 1
        row_b = ProdRow.query.filter_by(zo='ZO-B').first()
        assert json.loads(row_b.data)[6] == 'ŁÓDŹ KLIENT'


def test_csv_replaces_only_named_sheet_keeps_access_and_makes_backup(app, client, planner):
    _admin(client)
    _import(client)                                         # PSH i SP z xlsx
    psh, sp = _sheet_id(app, 'PSH'), _sheet_id(app, 'SP')
    with app.app_context():
        uid = User.query.filter_by(username='planista').first().id
        db.session.add(ProdSheetAccess(user_id=uid, sheet_id=psh))
        db.session.commit()
    content = _csv(HEADER, f'2026-09-07;;3;;{PRODUCT};2;ELEKTRO TEST;ZO-NEW;RAL 7035;AB;')
    resp = _post_csv(client, [('psh.csv', content)], ['psh'])          # wielkość liter nazwy nie ma znaczenia
    assert 'Wczytano z CSV: PSH (1)' in resp.get_data(as_text=True)
    with app.app_context():
        assert ProdSheet.query.filter_by(name='SP').first().id == sp         # SP nietknięty
        assert ProdSheet.query.count() == 2
        new_psh = ProdSheet.query.filter_by(name='PSH').first()
        assert [r.zo for r in new_psh.rows] == ['ZO-NEW']
        assert ProdSheetAccess.query.filter_by(user_id=uid, sheet_id=new_psh.id).count() == 1
        kinds = sorted(f.kind for f in ProdFile.query.all())
        assert kinds.count('backup') == 1                   # kopia xlsx sprzed zastąpienia PSH


def test_csv_sheet_blocks_xlsx_export_but_downloads_csv(app, client):
    _admin(client)
    _post_csv(client, [('x.csv', _csv(HEADER, f'2026-09-07;;3;;{PRODUCT};2;KLIENT;ZO-X;;;'))], ['SAMCSV'])
    sid = _sheet_id(app, 'SAMCSV')
    resp = client.post('/produkcja/export', data={'_csrf_token': _csrf(client)}, follow_redirects=True)
    assert 'Brak wczytanego pliku .xlsx' in resp.get_data(as_text=True)
    out = client.get(f'/produkcja/sheet/{sid}/csv')
    assert out.status_code == 200 and out.data.startswith(b'\xef\xbb\xbf')       # UTF-8 z BOM
    lines = out.data.decode('utf-8-sig').splitlines()
    assert lines[0] == HEADER_LAYOUT and lines[1].startswith('2026-09-07;;3;;' + PRODUCT)
    # pobrany CSV można wczytać z powrotem bez zmian
    again = _post_csv(client, [('x.csv', out.data)], ['SAMCSV'])
    assert 'Wczytano z CSV: SAMCSV (1)' in again.get_data(as_text=True)


def test_csv_errors_and_permissions(app, client, planner):
    _admin(client)
    resp = _post_csv(client, [('e.csv', b'')], ['X'])
    assert 'nie udało się odczytać' in resp.get_data(as_text=True)
    resp = _post_csv(client, [('a.csv', _csv(HEADER)), ('b.csv', _csv(HEADER))], ['Ten', 'ten'])
    assert 'innego arkusza' in resp.get_data(as_text=True)
    with app.app_context():
        assert ProdSheet.query.count() == 0
    logout(client)
    login(client, planner, 'Plan1234!')
    r = client.post('/produkcja/import-csv', data={'_csrf_token': _csrf(client)})
    assert r.status_code == 403                              # import tylko dla admina
