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


def apply_to_import(sheet_name, columns, mapping):
    """Nakłada zapisane ustawienia na świeżo wczytany arkusz.
    Zwraca (columns, mapping); oryginalne nagłówki zostają w sd['columns']."""
    cfg = load(sheet_name)
    cols = list(columns)
    for idx, ent in cfg['names'].items():
        i = int(idx)
        if i < len(cols) and cols[i] == ent['orig']:
            cols[i] = ent['name']
    if cfg['mapping'] is not None:
        mapping = {role: ent['idx'] for role, ent in cfg['mapping'].items()
                   if ent['idx'] < len(columns) and columns[ent['idx']] == ent['orig']}
    return cols, mapping
