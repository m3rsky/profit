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


FIXED_COLUMNS = {'row': 'Wiersz', 'qa': 'Listy QA'}   # kolumny aplikacji obok kolumn pliku


def _user_view(user_id, sheet_name):
    view = ProdUserView.query.filter_by(user_id=user_id, sheet_name=sheet_name).first()
    try:
        return view, (json.loads(view.hidden_json) if view and view.hidden_json else {})
    except ValueError:
        return view, {}


def hidden_view(user_id, sheet_name, columns):
    """Co użytkownik ukrył w widoku arkusza: (indeksy kolumn pliku, klucze kolumn aplikacji).
    Kolumna pliku liczy się tylko gdy oryginalny nagłówek się zgadza, więc ukrycie
    przeżywa ponowne wczytanie pliku."""
    _view, hidden = _user_view(user_id, sheet_name)
    cfg = load(sheet_name)
    idx = {int(i) for i, orig in hidden.items()
           if i.isdigit() and int(i) < len(columns) and orig == orig_name(cfg, columns, int(i))}
    return idx, {k for k in hidden if k in FIXED_COLUMNS}


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


def save_hidden(user_id, sheet_name, columns, hidden_idx, hidden_fixed):
    """Zapisuje widok użytkownika (ukryte kolumny pliku i kolumny aplikacji)."""
    cfg = load(sheet_name)
    hidden = {str(i): orig_name(cfg, columns, i) for i in sorted(hidden_idx) if i < len(columns)}
    hidden.update({k: FIXED_COLUMNS[k] for k in hidden_fixed if k in FIXED_COLUMNS})
    view, _old = _user_view(user_id, sheet_name)
    if view is None:
        view = ProdUserView(user_id=user_id, sheet_name=sheet_name)
        db.session.add(view)
    view.hidden_json = json.dumps(hidden, ensure_ascii=False)


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
            if i not in applied and str(i) not in cfg['names'] and cols[i] != canon:
                cfg['names'][str(i)] = {'orig': columns[i], 'name': canon}
                cols[i] = canon
                changed = True
        if changed:
            save(sheet_name, cfg)
    return cols, mapping
