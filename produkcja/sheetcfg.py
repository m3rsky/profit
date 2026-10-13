"""Własne ustawienia kolumn arkusza (nazwy, role, etapy), które przeżywają ponowne
wczytanie pliku. Zapisane w ProdSetting pod kluczem `sheetcfg:<arkusz>`, a wpis
dotyczy kolumny tylko wtedy, gdy jej oryginalny nagłówek w nowym pliku się zgadza."""
import json

from models import ProdSetting


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
