"""Zapytania i agregacje raportów KJ Prestiż. Jedno źródło danych dla ekranu, eksportu do Excela i karty PDF.

Każdy raport zwraca słownik: title, subtitle, kpis, tables (kolumny z rodzajem wartości i wiersze z surowymi
wartościami), chart (opcjonalnie) i note. Format wyświetlania daje `format_cell`, a Excel zapisuje liczby jako liczby."""
from collections import Counter, OrderedDict
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from models import db
from .models import (PkjEmployee, PkjInspection, PkjInspectionMaterial, PkjOperation, PkjOrder, PkjOrderOperation,
                     PkjOrderMaterial, PkjPhoto, PkjResult, PkjReturn, PkjReturnOperation)
from .repairs import DECISIONS, RETURN_STATUS_LABELS
from .service import get_setting

UTC = timezone.utc
LOCAL_TZ = ZoneInfo('Europe/Warsaw')
NO_DEPT = 'Bez działu'
ORDER_STATUS_LABELS = {'nowe': 'Nowe', 'w_produkcji': 'W produkcji', 'do_kontroli': 'Do kontroli',
                       'zakonczone': 'Zakończone', 'zamkniete': 'Zamknięte'}


# ── Filtry ────────────────────────────────────────────────────────────────────

def _parse_day(raw):
    try:
        return datetime.strptime((raw or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def parse_filters(args):
    return {'od': _parse_day(args.get('od')), 'do': _parse_day(args.get('do')),
            'klient': (args.get('klient') or '').strip(), 'produkt': (args.get('produkt') or '').strip(),
            'dzial': (args.get('dzial') or '').strip()}


def filters_to_query(f):
    """Parametry URL zgodne z formularzem (do linków eksportu i paginacji)."""
    out = {}
    if f['od']:
        out['od'] = f['od'].isoformat()
    if f['do']:
        out['do'] = f['do'].isoformat()
    for key in ('klient', 'produkt', 'dzial'):
        if f[key]:
            out[key] = f[key]
    return out


def describe_filters(f):
    parts = []
    if f['od'] or f['do']:
        parts.append(f"okres: {f['od'].strftime('%d.%m.%Y') if f['od'] else '...'} do "
                     f"{f['do'].strftime('%d.%m.%Y') if f['do'] else '...'}")
    for key, label in (('klient', 'klient'), ('produkt', 'produkt'), ('dzial', 'dział')):
        if f[key]:
            parts.append(f'{label}: {f[key]}')
    return ', '.join(parts) if parts else 'bez filtrów (wszystkie dane)'


def filter_options():
    clients = sorted({c for (c,) in db.session.query(PkjOrder.client).filter(PkjOrder.type == 'ZP').distinct() if c})
    products = sorted({p for (p,) in db.session.query(PkjOrder.product).filter(PkjOrder.type == 'ZP').distinct() if p})
    depts = sorted({d for (d,) in db.session.query(PkjEmployee.department).distinct() if d})
    return {'clients': clients, 'products': products, 'depts': depts}


def _bounds(d, end=False):
    day = d + timedelta(days=1) if end else d
    return datetime.combine(day, time.min, LOCAL_TZ).astimezone(UTC).replace(tzinfo=None)


def to_local(dt):
    if dt is None:
        return None
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).astimezone(LOCAL_TZ)


def week_start(dt):
    local = to_local(dt)
    return (local - timedelta(days=local.weekday())).date()


# ── Dane bazowe: kontrole po filtrach ─────────────────────────────────────────

def inspection_rows(f, statuses=('zakonczona', 'przerwana')):
    q = (db.session.query(PkjInspection, PkjOrder, PkjOrderOperation.operation_name, PkjEmployee.department)
         .join(PkjOrder, PkjOrder.id == PkjInspection.order_id)
         .outerjoin(PkjOrderOperation, PkjOrderOperation.id == PkjInspection.order_operation_id)
         .outerjoin(PkjEmployee, PkjEmployee.id == PkjInspection.performer_id)
         .filter(PkjInspection.status.in_(statuses)))
    if f['od']:
        q = q.filter(PkjInspection.started_at >= _bounds(f['od']))
    if f['do']:
        q = q.filter(PkjInspection.started_at < _bounds(f['do'], end=True))
    if f['klient']:
        q = q.filter(PkjOrder.client == f['klient'])
    if f['produkt']:
        q = q.filter(PkjOrder.product == f['produkt'])
    if f['dzial']:
        q = q.filter(PkjEmployee.department == f['dzial'])
    rows = [SimpleNamespace(insp=i, order=o, op=op or '-', dept=dept or NO_DEPT)
            for i, o, op, dept in q.order_by(PkjInspection.started_at, PkjInspection.id).all()]
    return rows


def _imats(insp_ids):
    out = {}
    if insp_ids:
        for m in PkjInspectionMaterial.query.filter(PkjInspectionMaterial.inspection_id.in_(insp_ids)).all():
            out.setdefault(m.inspection_id, []).append(m)
    return out


def _op_order():
    return {o.name: (o.order, o.id) for o in PkjOperation.query.all()}


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


# ── Formatowanie ──────────────────────────────────────────────────────────────

def format_cell(value, kind):
    if value is None or value == '':
        return '-'
    if kind == 'int':
        return f'{int(value)}'
    if kind == 'pct':
        return f'{value:.1f}%'.replace('.', ',')
    if kind == 'money':
        return f'{value:,.2f}'.replace(',', ' ').replace('.', ',') + ' PLN'
    if kind == 'float1':
        return f'{value:.1f}'.replace('.', ',')
    if kind == 'datetime':
        return to_local(value).strftime('%d.%m.%Y %H:%M')
    if kind == 'date':
        return value.strftime('%d.%m.%Y')
    return str(value)


def _table(title, columns, rows, total=None):
    return {'title': title, 'columns': columns, 'rows': rows, 'total': total}


def _group_label_week(d):
    return f"tydz. od {d.strftime('%d.%m')}"


# ── FPY ───────────────────────────────────────────────────────────────────────

FPY_AXES = OrderedDict([('operacja', 'Operacja'), ('dzial', 'Dział wykonawcy'), ('tydzien', 'Tydzień'),
                        ('material', 'Materiał')])


def fpy_report(f, by='operacja'):
    by = by if by in FPY_AXES else 'operacja'
    rows = [r for r in inspection_rows(f) if r.insp.round == 1]
    mats = _imats([r.insp.id for r in rows])
    groups = {}
    for r in rows:
        for m in mats.get(r.insp.id, []):
            if by == 'operacja':
                key = r.op
            elif by == 'dzial':
                key = r.dept
            elif by == 'material':
                key = m.name
            else:
                key = week_start(r.insp.started_at)
            g = groups.setdefault(key, [0, 0])
            g[0] += 1
            g[1] += 1 if m.verdict == 'OK' else 0
    if by == 'operacja':
        order = _op_order()
        keys = sorted(groups, key=lambda k: order.get(k, (10 ** 6, 0)))
    elif by == 'tydzien':
        keys = sorted(groups)
    else:
        keys = sorted(groups)
    table_rows = []
    for k in keys:
        n, ok = groups[k]
        table_rows.append([_group_label_week(k) if by == 'tydzien' else k, n, ok, n - ok, _pct(ok, n)])
    total_n = sum(g[0] for g in groups.values())
    total_ok = sum(g[1] for g in groups.values())
    cols = [('Grupa', 'text'), ('Skontrolowane materiały', 'int'), ('Zgodne za pierwszym razem', 'int'),
            ('Niezgodne', 'int'), ('FPY', 'pct')]
    return {
        'title': 'Dobrze za pierwszym razem (FPY)',
        'subtitle': f'Odsetek materiałów zgodnych w pierwszej kontroli, wg: {FPY_AXES[by].lower()}.',
        'kpis': [('FPY ogółem', format_cell(_pct(total_ok, total_n), 'pct')),
                 ('Skontrolowane materiały', str(total_n)), ('Niezgodne za pierwszym razem', str(total_n - total_ok))],
        'tables': [_table('FPY wg ' + FPY_AXES[by].lower(), cols, table_rows,
                          ['Razem', total_n, total_ok, total_n - total_ok, _pct(total_ok, total_n)])],
        'chart': {'type': 'bar', 'labels': [r[0] for r in table_rows], 'values': [r[4] or 0 for r in table_rows],
                  'name': 'FPY', 'suffix': '%', 'max': 100,
                  'tips': [f'{r[2]} z {r[1]} zgodnych' for r in table_rows]} if table_rows else None,
        'note': 'Przerwana kontrola liczy się jako niezgodna. Kontrole sprawdzające po naprawie nie wchodzą do FPY.',
        'axes': FPY_AXES, 'axis': by, 'axis_param': 'po',
    }


# ── Niezgodności ──────────────────────────────────────────────────────────────

def _nok_records(f):
    """Jeden rekord na niezgodny materiał w kontroli: punkty, zawrót, ZN, wykonawca."""
    rows = inspection_rows(f)
    by_id = {r.insp.id: r for r in rows}
    mats = [m for m in PkjInspectionMaterial.query.filter(
        PkjInspectionMaterial.inspection_id.in_(list(by_id) or [0]), PkjInspectionMaterial.verdict == 'NOK').all()]
    results = {}
    for res in PkjResult.query.filter(PkjResult.inspection_id.in_(list(by_id) or [0]),
                                      PkjResult.result == 'NOK').all():
        results.setdefault(res.inspection_id, []).append(res)
    returns = {}
    for ret in PkjReturn.query.filter(PkjReturn.inspection_id.in_(list(by_id) or [0])).all():
        returns[(ret.inspection_id, ret.order_material_id)] = ret
    zn_ids = [r.repair_order_id for r in returns.values() if r.repair_order_id]
    zns = {z.id: z for z in PkjOrder.query.filter(PkjOrder.id.in_(zn_ids or [0])).all()}
    out = []
    for m in mats:
        base = by_id[m.inspection_id]
        failing = [x for x in results.get(m.inspection_id, [])
                   if x.inspection_material_id == m.id or x.inspection_material_id is None]
        ret = returns.get((m.inspection_id, m.order_material_id))
        out.append(SimpleNamespace(imat=m, base=base, points=failing, ret=ret,
                                   zn=zns.get(ret.repair_order_id) if ret and ret.repair_order_id else None))
    out.sort(key=lambda r: (r.base.insp.started_at, r.imat.id), reverse=True)
    return out


def _point_label(rec):
    if rec.imat.interrupted:
        return 'Kontrola przerwana (błąd wyklucza element)'
    return '; '.join(dict.fromkeys(p.title for p in rec.points)) or '-'


def nonconformities_report(f):
    recs = _nok_records(f)
    table = []
    for r in recs:
        i, o = r.base.insp, r.base.order
        status = 'Wymagany zawrót'
        if r.zn is not None:
            status = ORDER_STATUS_LABELS.get(r.zn.status, r.zn.status)
        elif r.ret is not None:
            status = RETURN_STATUS_LABELS.get(r.ret.status, r.ret.status)
        table.append([i.started_at, o.number, o.client, o.product, r.base.op, r.imat.name, _point_label(r),
                      r.ret.reason_name if r.ret else None, i.performer_name, i.inspector_name,
                      'pierwsza' if i.round == 1 else f'sprawdzająca ({i.round - 1})',
                      DECISIONS.get(r.ret.decision) if r.ret else None, r.zn.number if r.zn else None, status])
    cols = [('Data kontroli', 'datetime'), ('Zlecenie', 'text'), ('Klient', 'text'), ('Produkt', 'text'),
            ('Operacja', 'text'), ('Materiał', 'text'), ('Niezgodny punkt', 'text'), ('Przyczyna', 'text'),
            ('Wykonawca', 'text'), ('Kontroler', 'text'), ('Kontrola', 'text'), ('Decyzja', 'text'),
            ('Zlecenie naprawcze', 'text'), ('Status', 'text')]
    open_n = sum(1 for r in recs if r.zn is None or r.zn.status != 'zamkniete')
    return {
        'title': 'Rejestr niezgodności',
        'subtitle': 'Każdy niezgodny materiał z kontroli: punkt, przyczyna, wykonawca, decyzja i status zlecenia naprawczego.',
        'kpis': [('Niezgodności', str(len(recs))), ('Niezamknięte', str(open_n)),
                 ('Złomy', str(sum(1 for r in recs if r.ret and r.ret.decision == 'zlom')))],
        'tables': [_table('Niezgodności', cols, table)], 'chart': None,
        'note': 'Wiersze z kontroli sprawdzających pokazują powtórną niezgodność tego samego materiału.',
    }


# ── Pareto ────────────────────────────────────────────────────────────────────

PARETO_AXES = OrderedDict([('punkt', 'Punkt kontrolny'), ('przyczyna', 'Przyczyna'), ('material', 'Materiał'),
                           ('operacja', 'Operacja')])


def pareto_report(f, axis='punkt'):
    axis = axis if axis in PARETO_AXES else 'punkt'
    recs = _nok_records(f)
    counter = Counter()
    for r in recs:
        if axis == 'punkt':
            if r.imat.interrupted or not r.points:
                counter['Kontrola przerwana'] += 1
            for title in dict.fromkeys(p.title for p in r.points):
                counter[title] += 1
        elif axis == 'przyczyna':
            counter[r.ret.reason_name if r.ret else 'Bez zawrotu'] += 1
        elif axis == 'material':
            counter[r.imat.name] += 1
        else:
            counter[r.base.op] += 1
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(counter.values())
    table, cum = [], 0
    for name, n in items:
        cum += n
        table.append([name, n, _pct(n, total), _pct(cum, total)])
    cols = [(PARETO_AXES[axis], 'text'), ('Liczba', 'int'), ('Udział', 'pct'), ('Narastająco', 'pct')]
    return {
        'title': 'Pareto niezgodności',
        'subtitle': f'Najczęstsze niezgodności wg: {PARETO_AXES[axis].lower()}. Słupki to udział, linia to udział narastający.',
        'kpis': [('Niezgodności', str(len(recs))), ('Pozycji na wykresie', str(len(items))),
                 ('Lider', f'{items[0][0]} ({_pct(items[0][1], total)}%)'.replace('.', ',') if items else '-')],
        'tables': [_table('Pareto wg ' + PARETO_AXES[axis].lower(), cols, table, ['Razem', total, 100.0, None])],
        'chart': {'type': 'pareto', 'labels': [r[0] for r in table], 'values': [r[2] for r in table],
                  'cumulative': [r[3] for r in table], 'name': 'Udział', 'suffix': '%', 'max': 100,
                  'tips': [f'{r[1]} szt.' for r in table]} if table else None,
        'note': 'Jedna niezgodność może dotyczyć kilku punktów, wtedy liczy się przy każdym z nich.' if axis == 'punkt' else None,
        'axes': PARETO_AXES, 'axis': axis, 'axis_param': 'os',
    }


# ── Koszt zawrotów ────────────────────────────────────────────────────────────

COST_AXES = OrderedDict([('operacja', 'Operacja'), ('przyczyna', 'Przyczyna'), ('material', 'Materiał'),
                         ('tydzien', 'Tydzień')])


def _num(key):
    try:
        return float(str(get_setting(key)).replace(',', '.'))
    except (TypeError, ValueError):
        return 0.0


def returns_cost_report(f, by='operacja'):
    by = by if by in COST_AXES else 'operacja'
    rows = {r.insp.id: r for r in inspection_rows(f)}
    rets = PkjReturn.query.filter(PkjReturn.inspection_id.in_(list(rows) or [0])).order_by(PkjReturn.id).all()
    hour_cost, op_hours, scrap_cost = _num('repair_hour_cost'), _num('repair_op_hours'), _num('scrap_material_cost')
    n_ops = dict(db.session.query(PkjReturnOperation.return_id, db.func.count(PkjReturnOperation.id))
                 .filter(PkjReturnOperation.return_id.in_([r.id for r in rets] or [0]))
                 .group_by(PkjReturnOperation.return_id).all())
    zn_ids = [r.repair_order_id for r in rets if r.repair_order_id]
    zns = {z.id: z for z in PkjOrder.query.filter(PkjOrder.id.in_(zn_ids or [0])).all()}
    last_report = dict(db.session.query(PkjOrderOperation.order_id, db.func.max(PkjOrderOperation.reported_at))
                       .filter(PkjOrderOperation.order_id.in_(zn_ids or [0]))
                       .group_by(PkjOrderOperation.order_id).all())
    open_ops = {oid for (oid,) in db.session.query(PkjOrderOperation.order_id)
                .filter(PkjOrderOperation.order_id.in_(zn_ids or [0]), PkjOrderOperation.status != 'zakonczona')}
    groups = {}
    for ret in rets:
        base = rows[ret.inspection_id]
        om = db.session.get(PkjOrderMaterial, ret.order_material_id)
        key = {'operacja': base.op, 'przyczyna': ret.reason_name or '-', 'material': om.material_name if om else '-',
               'tydzien': week_start(base.insp.started_at)}[by]
        hours = n_ops.get(ret.id, 0) * op_hours
        labour = hours * hour_cost
        material = scrap_cost if ret.decision == 'zlom' else 0.0
        zn = zns.get(ret.repair_order_id)
        days = None
        if zn is not None and zn.id not in open_ops and last_report.get(zn.id) and zn.created_at:
            days = (last_report[zn.id] - zn.created_at).total_seconds() / 86400
        g = groups.setdefault(key, {'n': 0, 'fix': 0, 'scrap': 0, 'ops': 0, 'hours': 0.0, 'labour': 0.0,
                                    'material': 0.0, 'days': []})
        g['n'] += 1
        g['fix' if ret.decision == 'poprawka' else 'scrap'] += 1
        g['ops'] += n_ops.get(ret.id, 0)
        g['hours'] += hours
        g['labour'] += labour
        g['material'] += material
        if days is not None:
            g['days'].append(days)
    if by == 'operacja':
        order = _op_order()
        keys = sorted(groups, key=lambda k: order.get(k, (10 ** 6, 0)))
    else:
        keys = sorted(groups, key=(lambda k: k) if by == 'tydzien' else (lambda k: (-groups[k]['n'], k)))

    def avg(days):
        return round(sum(days) / len(days), 1) if days else None
    table = [[_group_label_week(k) if by == 'tydzien' else k, g['n'], g['fix'], g['scrap'], g['ops'],
              round(g['hours'], 1), round(g['labour'], 2), round(g['material'], 2),
              round(g['labour'] + g['material'], 2), avg(g['days'])] for k in keys for g in [groups[k]]]
    all_days = [d for g in groups.values() for d in g['days']]
    total = ['Razem', sum(g['n'] for g in groups.values()), sum(g['fix'] for g in groups.values()),
             sum(g['scrap'] for g in groups.values()), sum(g['ops'] for g in groups.values()),
             round(sum(g['hours'] for g in groups.values()), 1), round(sum(g['labour'] for g in groups.values()), 2),
             round(sum(g['material'] for g in groups.values()), 2),
             round(sum(g['labour'] + g['material'] for g in groups.values()), 2), avg(all_days)]
    cols = [('Grupa', 'text'), ('Zawroty', 'int'), ('Poprawki', 'int'), ('Złomy', 'int'),
            ('Operacje naprawcze', 'int'), ('Godziny pracy', 'float1'), ('Robocizna', 'money'),
            ('Materiał (złom)', 'money'), ('Koszt razem', 'money'), ('Śr. czas zawrotu (dni)', 'float1')]
    return {
        'title': 'Koszt zawrotów',
        'subtitle': f'Zlecenia naprawcze, czas i materiał na poprawki i złom, wg: {COST_AXES[by].lower()}.',
        'kpis': [('Koszt razem', format_cell(total[8], 'money')), ('Zawroty', str(total[1])),
                 ('Zezłomowane elementy', str(total[3])), ('Śr. czas zawrotu', format_cell(total[9], 'float1') + ' dni')],
        'tables': [_table('Koszt zawrotów wg ' + COST_AXES[by].lower(), cols, table, total)],
        'chart': {'type': 'bar', 'labels': [r[0] for r in table], 'values': [r[8] for r in table],
                  'name': 'Koszt razem', 'suffix': ' PLN',
                  'tips': [f'{r[1]} zawrotów, {r[3]} złomów' for r in table]} if table else None,
        'note': (f'Założenia (zmienisz w Konfiguracji, Ustawienia): {op_hours:g} h na operację naprawczą, '
                 f'{hour_cost:g} PLN za godzinę, {scrap_cost:g} PLN materiału na zezłomowany element. '
                 'Czas zawrotu to czas od utworzenia ZN do zameldowania ostatniej operacji (tylko zakończone naprawy).'),
        'axes': COST_AXES, 'axis': by, 'axis_param': 'po',
    }


# ── Wyniki wg pracownika ──────────────────────────────────────────────────────

def employees_report(f):
    rows = [r for r in inspection_rows(f) if r.insp.round == 1]
    mats = _imats([r.insp.id for r in rows])
    people = {}
    for r in rows:
        name = r.insp.performer_name or 'Brak wykonawcy'
        p = people.setdefault((name, r.dept), [0, 0])
        for m in mats.get(r.insp.id, []):
            p[0] += 1
            p[1] += 1 if m.verdict == 'NOK' else 0
    table = sorted(([name, dept, n, nok, _pct(nok, n)] for (name, dept), (n, nok) in people.items()),
                   key=lambda r: (-r[3], -(r[4] or 0), r[0]))
    n_all, nok_all = sum(r[2] for r in table), sum(r[3] for r in table)
    cols = [('Wykonawca', 'text'), ('Dział', 'text'), ('Skontrolowane materiały', 'int'),
            ('Niezgodne', 'int'), ('Udział niezgodnych', 'pct')]
    return {
        'title': 'Wyniki wg pracownika',
        'subtitle': 'Niezgodności w pierwszej kontroli wg wykonawcy pracy. Raport służy do szkoleń i doboru zadań, nie do kar.',
        'kpis': [('Wykonawców', str(len({r[0] for r in table}))), ('Skontrolowane materiały', str(n_all)),
                 ('Udział niezgodnych', format_cell(_pct(nok_all, n_all), 'pct'))],
        'tables': [_table('Wykonawcy', cols, table, ['Razem', None, n_all, nok_all, _pct(nok_all, n_all)])],
        'chart': None,
        'note': 'Dane demonstracyjne, osoby fikcyjne. Przy małej liczbie kontroli wynik jednej osoby jest przypadkowy.',
    }


# ── Czas kontroli ─────────────────────────────────────────────────────────────

def inspection_time_report(f):
    rows = [r for r in inspection_rows(f, statuses=('zakonczona',)) if r.insp.finished_at]

    def minutes(r):
        return (r.insp.finished_at - r.insp.started_at).total_seconds() / 60

    def build(keyfn, order=None):
        groups = {}
        for r in rows:
            groups.setdefault(keyfn(r), []).append(minutes(r))
        keys = sorted(groups, key=order or (lambda k: k))
        return [[k, len(groups[k]), round(sum(groups[k]) / len(groups[k]), 1), round(max(groups[k]), 1),
                 round(sum(groups[k]), 1)] for k in keys]
    cols = [('Grupa', 'text'), ('Kontrole', 'int'), ('Średni czas (min)', 'float1'),
            ('Najdłuższa (min)', 'float1'), ('Razem (min)', 'float1')]
    order = _op_order()
    by_op = build(lambda r: r.op, lambda k: order.get(k, (10 ** 6, 0)))
    by_ctrl = build(lambda r: r.insp.inspector_name or '-')
    all_min = [minutes(r) for r in rows]
    return {
        'title': 'Czas kontroli',
        'subtitle': 'Czas od rozpoczęcia do zakończenia kontroli wg operacji i kontrolera.',
        'kpis': [('Zakończone kontrole', str(len(rows))),
                 ('Średni czas', format_cell(sum(all_min) / len(all_min), 'float1') + ' min' if all_min else '-'),
                 ('Najdłuższa', format_cell(max(all_min), 'float1') + ' min' if all_min else '-')],
        'tables': [_table('Wg operacji', cols, by_op), _table('Wg kontrolera', cols, by_ctrl)],
        'chart': {'type': 'bar', 'labels': [r[0] for r in by_op], 'values': [r[2] for r in by_op],
                  'name': 'Średni czas', 'suffix': ' min', 'tips': [f'{r[1]} kontroli' for r in by_op]} if by_op else None,
        'note': 'Liczone dla zakończonych kontroli (bez przerwanych), razem z kontrolami sprawdzającymi.',
    }


# ── Karta kontroli zlecenia ───────────────────────────────────────────────────

def card_data(order):
    """Wszystkie kontrole zlecenia ZP: trasa, kontrole z punktami i zdjęciami, zawroty i zlecenia naprawcze."""
    ops = (PkjOrderOperation.query.filter_by(order_id=order.id).order_by(PkjOrderOperation.position).all())
    op_names = {o.id: o.operation_name for o in ops}
    inspections = (PkjInspection.query.filter_by(order_id=order.id).order_by(PkjInspection.id).all())
    ids = [i.id for i in inspections]
    mats = _imats(ids)
    results = {}
    for r in (PkjResult.query.filter(PkjResult.inspection_id.in_(ids or [0]))
              .order_by(PkjResult.position, PkjResult.id).all()):
        results.setdefault(r.inspection_id, []).append(r)
    photos = {}
    all_res = [r.id for rs in results.values() for r in rs]
    for ph in PkjPhoto.query.filter(PkjPhoto.result_id.in_(all_res or [0])).order_by(PkjPhoto.id).all():
        photos.setdefault(ph.result_id, []).append(ph)
    blocks = []
    for insp in inspections:
        sections, index = [], {}
        for r in results.get(insp.id, []):
            key = r.section or 'Wspólne'
            if key not in index:
                index[key] = {'name': key, 'results': []}
                sections.append(index[key])
            index[key]['results'].append(r)
        blocks.append({'insp': insp, 'op': op_names.get(insp.order_operation_id, '-'), 'sections': sections,
                       'materials': mats.get(insp.id, [])})
    mat_ids = [m.id for m in PkjOrderMaterial.query.filter_by(order_id=order.id).all()]
    names = {m.id: m.material_name for m in PkjOrderMaterial.query.filter_by(order_id=order.id).all()}
    returns = (PkjReturn.query.filter(PkjReturn.order_material_id.in_(mat_ids or [0])).order_by(PkjReturn.id).all())
    zns = {z.id: z for z in PkjOrder.query.filter(PkjOrder.parent_id == order.id).all()}
    return {'order': order, 'ops': ops, 'blocks': blocks, 'photos': photos, 'returns': returns,
            'zns': zns, 'material_names': names}


def cardable_orders():
    """Zlecenia ZP, które mają kontrole (do wyboru w raportach)."""
    ids = [i for (i,) in db.session.query(PkjInspection.order_id).distinct()]
    return (PkjOrder.query.filter(PkjOrder.id.in_(ids or [0]), PkjOrder.type == 'ZP')
            .order_by(PkjOrder.created_at.desc()).all())


REPORTS = OrderedDict([
    ('niezgodnosci', ('Rejestr niezgodności', 'bi-exclamation-octagon', nonconformities_report, None)),
    ('fpy', ('Dobrze za pierwszym razem (FPY)', 'bi-bullseye', fpy_report, 'po')),
    ('pareto', ('Pareto niezgodności', 'bi-bar-chart-line', pareto_report, 'os')),
    ('koszt-zawrotow', ('Koszt zawrotów', 'bi-cash-coin', returns_cost_report, 'po')),
    ('pracownicy', ('Wyniki wg pracownika', 'bi-people', employees_report, None)),
    ('czas-kontroli', ('Czas kontroli', 'bi-stopwatch', inspection_time_report, None)),
])


def build_report(slug, args):
    title, icon, fn, axis_param = REPORTS[slug]
    f = parse_filters(args)
    report = fn(f, args.get(axis_param) or '') if axis_param else fn(f)
    report['filters'] = f
    report['filters_text'] = describe_filters(f)
    return report
