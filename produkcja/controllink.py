"""Powiązanie Listy produkcyjnej z kontrolami: Kontrola etapowa (kontrole działów w
kolumnach etapów) i Kontrola Końcowa (listy kontrolne przy kolumnie KJ).

Kolumna etapu ma "powiązanie": działy (i opcjonalnie elementy kontroli), których kontrole ją
dotyczą. Każda para dział + element to jedno "miejsce" kontroli, np. GIĘCIE OB. = Gięcie z
elementami Korpus, G/D, Boki (3 miejsca), a DRZWI = Spawanie + Zgrzewanie z elementem Drzwi
(2 miejsca). Zlecenie z kontrolą łączymy po numerze ZO, a produkt (inny zapis w ERP niż na
liście, np. `PSH-600-800-300 IP65` i `PSH 600X800X300 (P)`) porównujemy po wymiarach.

Powiązania są zapisane w sheetcfg (po oryginalnej nazwie kolumny, więc przeżywają ponowne
wczytanie pliku). Dopóki nikt ich nie zapisał, działają podpowiedzi z nazw kolumn."""
import json
import re
import unicodedata
from collections import Counter

from sqlalchemy import or_

from models import (db, ControlOrder, ControlRecord, ControlRecordElement, ControlResult,
                    ControlElement, ProductionDepartment, Report, ProdRow)
from . import excel_io, sheetcfg


def plain(text):
    """Wielkie litery bez polskich znaków i nadmiarowych spacji."""
    s = unicodedata.normalize('NFKD', str(text or '')).replace('ł', 'l').replace('Ł', 'L')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(s.upper().split())


def _numbers(text):
    return re.findall(r'\d+', re.sub(r'\bIP\s*\d+\b', ' ', plain(text)))


def _type_key(text):
    """Typ produktu (litery przed pierwszą liczbą, bez myślników, kropek i spacji):
    `RU-L-1150...` i `RUL 1900X1150...` dają `RUL`, `SP-D` i `SPD` dają `SPD`."""
    m = re.match(r'^[A-Z][A-Z\s\-./]*', plain(text))
    return re.sub(r'[^A-Z]', '', m.group(0)) if m else ''


def product_score(a, b):
    """Jak dobrze zgadzają się dwa zapisy tego samego produktu (0 = to nie ten produkt):
    100 identyczny tekst, 50+ te same wymiary w dowolnej kolejności (np. 1900X1150X250 i
    1150-1900-250), niżej zgodność większości wymiarów (literówka w jednym wymiarze).
    Typy muszą być zgodne (RU-L = RUL, SP-D = SPD, RZ-M = RZM)."""
    if plain(a) and plain(a) == plain(b):
        return 100
    ta, tb = _type_key(a), _type_key(b)
    if ta and tb and not (ta.startswith(tb) or tb.startswith(ta)):
        return 0
    na, nb = Counter(_numbers(a)), Counter(_numbers(b))
    la, lb = sum(na.values()), sum(nb.values())
    if not la or not lb:
        return 0
    common = sum((na & nb).values())
    if common == la == lb:
        return 50 + common
    n = min(la, lb)
    if n >= 2 and common == n:                 # krótszy zapis w całości zawarty w dłuższym
        return 20 + common
    if n >= 3 and common == n - 1:             # jeden wymiar inaczej (literówka)
        return common
    return 0


def same_product(a, b):
    """Czy dwa zapisy mogą oznaczać ten sam produkt (patrz product_score)."""
    return product_score(a, b) > 0


def _assign(items, zo_rows, name_of):
    """Które wiersze listy odpowiadają pozycjom (zlecenia kontroli, listy kontrolne) jednego ZO.
    Pozycja trafia do wierszy, z którymi zgadza się najlepiej; gdy nic się nie zgadza, a pod
    ZO jest jedna pozycja i jeden wiersz, to produkt nie rozstrzyga i łączymy je po samym ZO.
    Zwraca {id wiersza: [pozycje]}."""
    out = {}
    for it in items:
        scores = {rid: product_score(p, name_of(it)) for rid, p in zo_rows}
        best = max(scores.values(), default=0)
        if best > 0:
            for rid, sc in scores.items():
                if sc == best:
                    out.setdefault(rid, []).append(it)
        elif len(items) == 1 and len(zo_rows) == 1:
            out.setdefault(zo_rows[0][0], []).append(it)
    return out


def _zo_rows(zos):
    """{ZO: [(id wiersza, produkt)]} dla wszystkich wierszy listy z tymi numerami ZO (z każdego
    arkusza), bo dopasowanie ma brać pod uwagę także wiersze spoza wyświetlanej strony."""
    from models import ProdSheet
    mappings = {sh.id: sh.mapping for sh in ProdSheet.query.all()}
    out = {}
    for rid, sid, zo, data in db.session.query(ProdRow.id, ProdRow.sheet_id, ProdRow.zo, ProdRow.data)             .filter(ProdRow.zo.in_(zos)):
        f = excel_io.row_fields(json.loads(data or '[]'), mappings.get(sid, {}))
        out.setdefault(zo, []).append((rid, f.get('product') or ''))
    return out


# ── Podpowiedzi powiązań z nazw kolumn ─────────────────────────────────────────

def suggest(label, dept_ids, element_names):
    """Podpowiedź powiązania dla kolumny o nazwie `label`. `dept_ids`: {plain(nazwa działu): id},
    `element_names`: {plain(nazwa): nazwa}. Zwraca dict(depts, elements, final) albo None."""
    L = plain(label)
    if not L:
        return None

    def dept(prefix):
        return [i for n, i in dept_ids.items() if n.startswith(prefix)]

    def els(*names):
        return [element_names[plain(n)] for n in names if plain(n) in element_names]

    if L.startswith('KJ'):
        return {'depts': [], 'elements': [], 'final': True}
    if L.startswith('GIECIE'):
        if ' DR' in L:
            return {'depts': dept('GIECIE'), 'elements': els('Drzwi'), 'final': False}
        if ' OB' in L:
            return {'depts': dept('GIECIE'), 'elements': els('Korpus', 'G/D', 'Boki'), 'final': False}
        return {'depts': dept('GIECIE'), 'elements': [], 'final': False}
    if L.startswith('LASER'):
        return {'depts': dept('LASER'), 'elements': [], 'final': False}
    if L.startswith('CZYSZ'):
        return {'depts': dept('CZYSZCZ'), 'elements': [], 'final': False}
    if L.startswith('MYJ'):
        return {'depts': dept('MYCIE'), 'elements': [], 'final': False}
    if L.startswith('MAL'):
        return {'depts': dept('MALARN'), 'elements': [], 'final': False}
    if L.startswith('SZYK'):
        return {'depts': dept('KOMPLET'), 'elements': [], 'final': False}
    if L.startswith(('SKLAD', 'ZLOZ')):
        return {'depts': dept('SKLAD'), 'elements': [], 'final': False}
    if L.startswith('GRZ'):
        return {'depts': dept('SPAW') + dept('ZGRZEW'), 'elements': [], 'final': False}
    if 'DRZ' in L and (L.startswith('SPAW') or L.startswith('DRZ')):
        return {'depts': dept('SPAW') + dept('ZGRZEW'), 'elements': els('Drzwi'), 'final': False}
    if L.startswith('SPAW'):
        return {'depts': dept('SPAW'), 'elements': els('Korpus') if ' OB' in L else [], 'final': False}
    return None


def _catalog():
    depts = {plain(d.name): d.id for d in ProductionDepartment.query.filter_by(is_active=True)}
    elements = {plain(e.name): e.name for e in ControlElement.query.all()}
    return depts, elements


def sheet_links(sheet, columns=None):
    """Powiązania kolumn arkusza: {indeks kolumny: dict(depts, elements, final)}. Zapisane
    ustawienia (jeśli były zapisywane) albo podpowiedzi z nazw kolumn etapów."""
    columns = columns or sheet.columns
    cfg = sheetcfg.load(sheet.name)
    depts, elements = _catalog()
    saved = cfg.get('controls')
    out = {}
    if saved is not None:
        for idx, ent in saved.items():
            i = int(idx)
            if i < len(columns) and ent.get('orig') == sheetcfg.orig_name(cfg, columns, i):
                ids = [d for d in ent.get('depts', []) if d in depts.values()]
                if ids or ent.get('final'):
                    out[i] = {'depts': ids, 'elements': ent.get('elements', []),
                              'final': bool(ent.get('final'))}
        return out
    from models import ProdStageColumn
    for st in ProdStageColumn.query.filter_by(sheet_id=sheet.id, is_active=True):
        if st.col_index < len(columns):
            sug = suggest(columns[st.col_index], depts, elements)
            if sug and (sug['depts'] or sug['final']):
                out[st.col_index] = sug
    return out


def save_links(sheet, form, columns):
    """Zapis powiązań z formularza strony kolumn (pola ctl_dept_<i>, ctl_el_<i>, ctl_final)."""
    cfg = sheetcfg.load(sheet.name)
    depts, elements = _catalog()
    valid_d, valid_e = set(depts.values()), set(elements.values())
    final = {int(x) for x in form.getlist('ctl_final') if x.isdigit()}
    controls = {}
    for i in range(len(columns)):
        d = [int(x) for x in form.getlist(f'ctl_dept_{i}') if x.isdigit() and int(x) in valid_d]
        e = [x for x in form.getlist(f'ctl_el_{i}') if x in valid_e]
        if d or i in final:
            controls[str(i)] = {'orig': sheetcfg.orig_name(cfg, columns, i), 'depts': d,
                                'elements': e if d else [], 'final': i in final}
    cfg['controls'] = controls
    sheetcfg.save(sheet.name, cfg)


def catalog_for_form():
    return (ProductionDepartment.query.filter_by(is_active=True)
            .order_by(ProductionDepartment.name).all(),
            ControlElement.query.filter_by(is_active=True).order_by(ControlElement.order, ControlElement.id).all())


# ── Stan kontroli wierszy ──────────────────────────────────────────────────────

def _state(created, done, total, ng):
    if not created:
        return None
    if ng:
        return 'ng'
    return 'done' if done >= total else 'open'


def control_info(rows):
    """Stan kontroli dla wierszy: {id wiersza: {'stages': {kol: dict}, 'final': dict | None}}.
    stages[kol] = created, done, total, ng, state, order_id; final = total, done, ng, ids, state."""
    rows = [r for r in rows if r.zo]
    result = {r.id: {'stages': {}, 'final': None} for r in rows}
    if not rows:
        return result
    links_by_sheet = {}
    for r in rows:
        if r.sheet_id not in links_by_sheet:
            links_by_sheet[r.sheet_id] = sheet_links(r.sheet)
    zos = sorted({r.zo for r in rows})
    zo_rows = _zo_rows(zos)

    # Kontrola etapowa
    orders_by_zo = {}
    for o in ControlOrder.query.filter(ControlOrder.zo_number.in_(zos)).all():
        orders_by_zo.setdefault(o.zo_number, []).append(o)
    order_ids = [o.id for os_ in orders_by_zo.values() for o in os_]
    records_by_order, rec_elements, ng_records = {}, {}, set()
    if order_ids:
        recs = (ControlRecord.query.filter(ControlRecord.control_order_id.in_(order_ids),
                                           ControlRecord.department_id.isnot(None))
                .order_by(ControlRecord.id.desc()).all())
        for rec in recs:
            records_by_order.setdefault(rec.control_order_id, []).append(rec)
        rids = [rec.id for rec in recs]
        for i in range(0, len(rids), 500):
            chunk = rids[i:i + 500]
            for rid, name in db.session.query(ControlRecordElement.record_id, ControlRecordElement.name) \
                    .filter(ControlRecordElement.record_id.in_(chunk)):
                rec_elements.setdefault(rid, set()).add(plain(name))
            ng_records |= {rid for (rid,) in db.session.query(ControlResult.record_id)
                           .filter(ControlResult.record_id.in_(chunk), ControlResult.result == 'NG')}
    order_claims = {}
    for zo, orders in orders_by_zo.items():
        for rid, items in _assign(orders, zo_rows.get(zo, []), lambda o: o.product_name).items():
            order_claims.setdefault(rid, []).extend(items)

    for r in rows:
        matched = order_claims.get(r.id, [])
        recs = sorted((rec for o in matched for rec in records_by_order.get(o.id, [])),
                      key=lambda x: -x.id)
        if not recs:
            continue
        for col, link in links_by_sheet[r.sheet_id].items():
            if link['final']:
                continue
            slots = [(d, e) for d in link['depts'] for e in (link['elements'] or [None])]
            created = done = 0
            ng = False
            order_id = None
            for d, e in slots:
                hit = next((rec for rec in recs if rec.department_id == d
                            and (e is None or plain(e) in rec_elements.get(rec.id, ()))), None)
                if hit is None:
                    continue
                created += 1
                done += 1 if hit.status == 'completed' else 0
                ng = ng or hit.id in ng_records
                order_id = order_id or hit.control_order_id
            if created:
                result[r.id]['stages'][col] = {
                    'created': created, 'done': done, 'total': len(slots), 'ng': ng,
                    'state': _state(created, done, len(slots), ng), 'order_id': order_id}

    # Kontrola Końcowa: listy kontrolne z kluczem QR produkt|klient|ZO
    reports_by_zo = {}
    for i in range(0, len(zos), 100):
        likes = [Report.qr_key.like('%|' + z.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_'),
                                    escape='\\') for z in zos[i:i + 100]]
        for rep in Report.query.filter(Report.report_type == 'kontroler', or_(*likes)).all():
            reports_by_zo.setdefault(rep.qr_key.rsplit('|', 1)[-1], []).append(rep)
    report_claims = {}
    for zo in zos:
        by_key = {}
        for rep in reports_by_zo.get(plain(zo), []):
            by_key.setdefault(rep.qr_key, []).append(rep)
        for rid, keys in _assign(list(by_key), zo_rows.get(zo, []), lambda k: k.split('|', 1)[0]).items():
            report_claims.setdefault(rid, []).extend(rep for k in keys for rep in by_key[k])
    for r in rows:
        hits = report_claims.get(r.id, [])
        if hits:
            ng = sum(1 for rep in hits if rep.stats['ng'])
            done = sum(1 for rep in hits if rep.status == 'completed')
            result[r.id]['final'] = {
                'total': len(hits), 'done': done, 'ng': ng, 'ids': [rep.id for rep in hits],
                'state': _state(len(hits), done, len(hits), ng)}
    return result


# ── Podsumowanie do filtrów ────────────────────────────────────────────────────

def summary(info, kind):
    """Stan wiersza dla filtra: kind 'etapowa' lub 'koncowa'. Zwraca 'brak', 'otwarta',
    'zakonczona' albo 'ng' (NG wygrywa, zakończona tylko gdy wszystkie utworzone są zakończone)."""
    if kind == 'koncowa':
        f = info.get('final')
        states = [f['state']] if f else []
    else:
        states = [s['state'] for s in info.get('stages', {}).values()]
    if not states:
        return 'brak'
    if 'ng' in states:
        return 'ng'
    return 'zakonczona' if all(s == 'done' for s in states) else 'otwarta'
