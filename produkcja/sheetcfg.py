"""Własne ustawienia kolumn arkusza (nazwy, role, etapy), które przeżywają ponowne
wczytanie pliku. Zapisane w ProdSetting pod kluczem `sheetcfg:<arkusz>`, a wpis
dotyczy kolumny tylko wtedy, gdy jej oryginalny nagłówek w nowym pliku się zgadza."""
import json

from models import db, ProdSetting, ProdUserView


def _key(sheet_name):
    return f'sheetcfg:{sheet_name}'[:64]


def load(sheet_name):
    try:
        cfg = json.loads(ProdSetting.get(_key(sheet_name), '') or '{}')
    except ValueError:
        cfg = {}
    cfg.setdefault('names', {})     # {idx: {"orig": ..., "name": ...}}
    cfg.setdefault('mapping', None)  # {rola: {"idx": n, "orig": ...}} albo None (auto)
    cfg.setdefault('layout', None)   # układ wzorcowy PSH: True/False albo None (domyślnie wg arkusza)
    return cfg


FIXED_COLUMNS = {'row': 'Wiersz', 'qa': 'Listy QA', 'el': 'Elementy'}   # kolumny aplikacji (nie z pliku)
MOVABLE_FIXED = {'el'}          # kolumny aplikacji, które można przesuwać (Wiersz i Listy QA są na stałe z lewej)


def _user_view(user_id, sheet_name):
    return ProdUserView.query.filter_by(user_id=user_id, sheet_name=sheet_name).first()


def col_keys(origs):
    """Stabilne klucze kolumn pliku do zapisu kolejności: oryginalny nagłówek (kolejne powtórzenia
    tego samego nagłówka dostają numer), więc kolejność przeżywa ponowne wczytanie pliku."""
    seen, keys = {}, []
    for o in origs:
        n = seen.get(o, 0)
        seen[o] = n + 1
        keys.append(o if n == 0 else f'{o}#{n}')
    return keys


def get_prefs(user_id, sheet_name, columns):
    """Własny widok użytkownika: {'hidden': indeksy ukryte, 'shown': indeksy pokazane mimo domyślnego
    ukrycia, 'fixed': ukryte kolumny aplikacji, 'order': klucze w wybranej kolejności}. Kolumna pliku
    liczy się tylko gdy oryginalny nagłówek się zgadza (ustawienia przeżywają ponowne wczytanie pliku)."""
    view = _user_view(user_id, sheet_name)
    try:
        raw = json.loads(view.hidden_json) if view and view.hidden_json else {}
    except ValueError:
        raw = {}
    if 'v' not in raw:      # starszy zapis: {indeks: nagłówek, 'row': ..., 'qa': ...}
        raw = {'v': 2, 'hidden': {k: v for k, v in raw.items() if k.isdigit()},
               'fixed': [k for k in raw if k in FIXED_COLUMNS]}
    cfg = load(sheet_name)

    def valid(d):
        return {int(i) for i, orig in (d or {}).items()
                if str(i).isdigit() and int(i) < len(columns) and orig == orig_name(cfg, columns, int(i))}

    return {'hidden': valid(raw.get('hidden')), 'shown': valid(raw.get('shown')),
            'fixed': {k for k in raw.get('fixed', []) if k in FIXED_COLUMNS},
            'order': [k for k in raw.get('order', []) if isinstance(k, str)]}


def save_prefs(user_id, sheet_name, columns, prefs):
    """Zapisuje własny widok użytkownika (patrz get_prefs)."""
    cfg = load(sheet_name)
    names = lambda idxs: {str(i): orig_name(cfg, columns, i) for i in sorted(idxs) if i < len(columns)}
    view = _user_view(user_id, sheet_name)
    if view is None:
        view = ProdUserView(user_id=user_id, sheet_name=sheet_name)
        db.session.add(view)
    view.hidden_json = json.dumps({
        'v': 2, 'hidden': names(prefs['hidden']), 'shown': names(prefs['shown']),
        'fixed': sorted(k for k in prefs['fixed'] if k in FIXED_COLUMNS), 'order': list(prefs['order'])},
        ensure_ascii=False)


def apply_order(items, keys_of, user_order):
    """Nakłada kolejność użytkownika na kolejność bazową `items`: elementy z listy użytkownika zajmują
    te same miejsca co dotąd, ale w jego kolejności, pozostałe zostają tam, gdzie były."""
    pos = {k: n for n, k in enumerate(user_order)}
    listed = [x for x in items if keys_of(x) in pos]
    listed.sort(key=lambda x: pos[keys_of(x)])
    it = iter(listed)
    return [next(it) if keys_of(x) in pos else x for x in items]


MIN_WIDTH, MAX_WIDTH = 30, 1000


def column_widths(user_id, sheet_name, columns):
    """Szerokości kolumn ustawione przez użytkownika: {indeks: px} (tylko gdy oryginalny
    nagłówek się zgadza, więc szerokość przeżywa ponowne wczytanie pliku)."""
    view = ProdUserView.query.filter_by(user_id=user_id, sheet_name=sheet_name).first()
    try:
        saved = json.loads(view.widths_json) if view and view.widths_json else {}
    except ValueError:
        saved = {}
    cfg = load(sheet_name)
    out = {}
    for i, ent in saved.items():
        try:
            idx, orig, px = int(i), ent[0], int(ent[1])
        except (ValueError, TypeError, IndexError):
            continue
        if idx < len(columns) and orig == orig_name(cfg, columns, idx):
            out[idx] = max(MIN_WIDTH, min(MAX_WIDTH, px))
    return out


def save_width(user_id, sheet_name, columns, idx, px):
    """Zapisuje szerokość kolumny użytkownika; `px` pusty/0 przywraca szerokość automatyczną."""
    widths = column_widths(user_id, sheet_name, columns)
    if px:
        widths[idx] = max(MIN_WIDTH, min(MAX_WIDTH, int(px)))
    else:
        widths.pop(idx, None)
    cfg = load(sheet_name)
    view = ProdUserView.query.filter_by(user_id=user_id, sheet_name=sheet_name).first()
    if view is None:
        view = ProdUserView(user_id=user_id, sheet_name=sheet_name)
        db.session.add(view)
    view.widths_json = json.dumps({str(i): [orig_name(cfg, columns, i), w] for i, w in widths.items()},
                                  ensure_ascii=False)


def save(sheet_name, cfg):
    ProdSetting.set(_key(sheet_name), json.dumps(cfg, ensure_ascii=False))


def orig_name(cfg, columns, idx):
    ent = cfg['names'].get(str(idx))
    return ent['orig'] if ent else columns[idx]


def layout_enabled(cfg, sheet_name, headerless):
    """Czy arkusz ma układ wzorcowy PSH (ustawienie arkusza albo domyślnie wg jego nazwy)."""
    from . import layout
    if cfg.get('layout') is not None:
        return bool(cfg['layout'])
    return layout.default_enabled(sheet_name, headerless)


def apply_to_import(sheet_name, columns, mapping, headerless=False):
    """Nakłada zapisane ustawienia na świeżo wczytany arkusz.
    Zwraca (columns, mapping); oryginalne nagłówki zostają w sd['columns'].
    Przy włączonym układzie wzorcowym kolumny bez własnej nazwy dostają nazwy wzorca."""
    cfg = load(sheet_name)
    cols = list(columns)
    applied = set()
    for idx, ent in cfg['names'].items():
        i = int(idx)
        if i < len(cols) and cols[i] == ent['orig']:
            cols[i] = ent['name']
            applied.add(i)
    if cfg['mapping'] is not None:
        mapping = {role: ent['idx'] for role, ent in cfg['mapping'].items()
                   if ent['idx'] < len(columns) and columns[ent['idx']] == ent['orig']}
    if layout_enabled(cfg, sheet_name, headerless):
        from . import layout
        changed = False
        for i, canon in layout.canon_names(columns, sheet_name).items():
            ent = cfg['names'].get(str(i))
            if ent is None and cols[i] != canon:
                cfg['names'][str(i)] = {'orig': columns[i], 'name': canon}
                cols[i] = canon
                changed = True
            elif ent is not None and i in applied and ent['name'] in layout.LEGACY_NAMES and ent['name'] != canon:
                ent['name'] = canon           # nazwa nadana przez dawną wersję układu
                cols[i] = canon
                changed = True
        if changed:
            save(sheet_name, cfg)
    return cols, mapping
