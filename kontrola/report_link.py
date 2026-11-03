"""Kontrola etapowa w raporcie Kontroli Końcowej.

Raport końcowy (`Report`, typ `kontroler`) ma klucz QR `produkt|klient|ZO`. Dla tego klucza szukamy
zleceń Kontroli etapowej (`ControlOrder`): ZO musi być taki sam, produkt dopasowujemy po wymiarach
(`product_score`, ERP i lista zapisują ten sam produkt różnie), a klient jest dodatkowym sprawdzeniem
(pod jednym ZO jest jeden klient, ale często różne produkty), więc rozbieżność klienta tylko ostrzega.
Dane są liczone przy każdym wyświetleniu, więc raport pokazuje stan na żywo.

Ostrzeżenia (brak kontroli, kontrola w toku, NG, nieskontrolowany etap) korzystają z powiązań kolumn
Listy produkcyjnej (`controllink.sheet_links`): znamy wtedy etapy, które zlecenie powinno przejść."""
import json

from sqlalchemy import func

from models import (ControlOrder, ControlRecord, ProdRow, ProdSheet, ProductionDepartment)


def _split_key(report):
    parts = ((report.qr_key or '').split('|') + ['', '', ''])[:3]
    return parts[0], parts[1], parts[2]


def _stage_label(dept_name, elements):
    return f'{dept_name} ({elements})' if elements else dept_name


def _expected_slots(zo, product):
    """Etapy, które zlecenie ma przejść wg powiązań kolumn Listy produkcyjnej:
    [(id działu, element albo None)]. Pusta lista, gdy zlecenia nie ma na liście."""
    from produkcja import controllink, excel_io
    best, best_row, sheets = 0, None, {}
    for row in ProdRow.query.filter(func.upper(func.trim(ProdRow.zo)) == zo):
        sheet = sheets.get(row.sheet_id) or sheets.setdefault(row.sheet_id, ProdSheet.query.get(row.sheet_id))
        if sheet is None:
            continue
        fields = excel_io.row_fields(json.loads(row.data or '[]'), sheet.mapping)
        score = controllink.product_score(fields.get('product') or '', product)
        if score > best:
            best, best_row = score, sheet
    if best_row is None:
        return []
    slots = []
    for link in controllink.sheet_links(best_row).values():
        if link['final']:
            continue
        for d in link['depts']:
            for e in (link['elements'] or [None]):
                if (d, e) not in slots:
                    slots.append((d, e))
    return slots


def stage_controls_for_report(report):
    """Kontrole etapowe zlecenia raportu: dict(stages, warnings, orders) albo None, gdy raport nie ma ZO.
    stages: ostatnia kontrola każdego działu i zestawu elementów (z liczbą podejść), z pełnymi punktami.
    warnings: [(poziom 'ng' | 'warn' | 'info', tekst)]."""
    from produkcja.controllink import plain, product_score
    product, client, zo_raw = _split_key(report)
    zo = ' '.join(zo_raw.upper().split())
    if not zo:
        return None

    candidates = ControlOrder.query.filter(func.upper(func.trim(ControlOrder.zo_number)) == zo).all()
    scores = {o.id: product_score(o.product_name or '', product) for o in candidates}
    best = max(scores.values(), default=0)
    matched = [o for o in candidates if best > 0 and scores[o.id] == best]
    others = [o for o in candidates if o not in matched]

    warnings = []
    for o in matched:
        if plain(o.client) and plain(client) and plain(o.client) != plain(client):
            warnings.append(('warn', f'Klient w kontroli etapowej ({o.client}) różni się od klienta '
                                     f'raportu ({client}).'))
    if others:
        names = ', '.join(sorted({o.product_name or 'bez produktu' for o in others}))
        warnings.append(('info', f'Pod tym ZO są też kontrole etapowe innych produktów: {names}.'))

    groups = {}
    for o in matched:
        recs = (ControlRecord.query.filter(ControlRecord.control_order_id == o.id,
                                           ControlRecord.department_id.isnot(None))
                .order_by(ControlRecord.id).all())
        for rec in recs:
            groups.setdefault((rec.department_id, frozenset(plain(n) for n in rec.element_names)), []).append(rec)

    stages = []
    for recs in groups.values():
        rec = recs[-1]
        stages.append({
            'record': rec,
            'order_id': rec.control_order_id,
            'department': rec.department.name,
            'dept_order': (rec.department.order or 0, rec.department.name),
            'elements': rec.elements_label,
            'element_set': {plain(n) for r in recs for n in r.element_names},
            'status': rec.status,
            'result': rec.result,
            'controller': rec.controller.username if rec.controller else '',
            'performer': rec.employee.name if rec.employee else '',
            'when': rec.completed_at or rec.created_at,
            'attempt': len(recs),
            'groups': rec.result_groups,
            'ng_count': rec.ng_count,
            'rated': rec.rated_count,
            'total': len(rec.results),
        })
    stages.sort(key=lambda s: (s['dept_order'], s['elements']))

    if not stages:
        warnings.insert(0, ('warn', 'Brak kontroli etapowej dla tego ZO i produktu.'))
    for s in stages:
        label = _stage_label(s['department'], s['elements'])
        if s['status'] != 'completed':
            warnings.append(('warn', f'{label}: kontrola nie jest zakończona.'))
        elif s['result'] == 'NG':
            warnings.append(('ng', f'{label}: ostatnia kontrola z wynikiem NG.'))

    if stages:
        names = {d.id: d.name for d in ProductionDepartment.query}
        by_dept = {}
        for (dept_id, els) in groups:
            by_dept.setdefault(dept_id, set()).update(els)
        for d, e in _expected_slots(zo, product):
            if d not in by_dept or (e is not None and plain(e) not in by_dept[d]):
                warnings.append(('warn', f'Brak kontroli etapowej: {_stage_label(names.get(d, "?"), e)}.'))

    return {'stages': stages, 'warnings': warnings, 'orders': matched}
