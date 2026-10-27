"""Układ wzorcowy kolumn listy produkcyjnej (wzór: arkusz PSH).

Kolumny każdego arkusza z nagłówkami są dopasowywane po nazwie do kolumn wzorca (np. `SPAW.` i
`GRZ/SP` to `SPAW. OB.`, `DRZWI` to `SPAW DRZW.`, `ZŁOŻONE` to `SKŁADANIE`), dostają nazwy
wzorca i są wyświetlane w kolejności wzorca. Kolumn bez odpowiednika we wzorcu (np. LANTEK,
D.posp) nie ruszamy, trafiają za kolumny wzorca. Kolumny wzorca, których arkusz nie ma, po prostu
nie są pokazywane. WYPALANIE, LASER i SZYKOWANIE to kolumny dodatkowe: widać je tylko w arkuszach,
które je mają (WYPALANIE i LASER przed GIĘCIEM, SZYKOWANIE przed D.zaw).

Dane w bazie i eksport do Excela się nie zmieniają, bo zmienia się tylko nazwa i kolejność w widoku.
Układ obejmuje wszystkie arkusze i można go wyłączyć dla wybranego (ustawienie `layout` w sheetcfg).

Widok pokazuje domyślnie tylko kolumny oznaczone w CANON jako widoczne; pozostałe (ZAM, TERMIN, ILOŚĆ,
KOLOR, LASER...) i kolumny bez odpowiednika są ukryte, a użytkownik włącza je w "Ukryte kolumny"."""
import re

from openpyxl.utils import get_column_letter

from . import sheetcfg
from .controllink import plain

# (nazwa we wzorcu, wzorzec nazwy po plain() albo None dla kolumny wirtualnej, czy to etap produkcji,
#  czy kolumna jest domyślnie widoczna). Kolumny domyślnie ukryte użytkownik włącza sam w "Ukryte kolumny".
CANON = [
    ('ZAM', r'^ZAM\.?$', False, False),
    ('DATA', r'^(DATA|ROZP\.?|ROZ\.?|DATA PRODUKCJI)$', False, True),
    ('CZAS REALIZACJI', r'^CZAS REA', False, False),
    ('TERMIN REALIZACJI', r'^TERMIN REA', False, False),
    ('NAZWA TOW.', r'^NAZWA( TOW\.?)?$', False, True),
    ('ILOŚĆ', r'^(ILOSC|SZT)$', False, False),
    ('Z', r'^Z$', False, False),
    ('NAZWA ODBIORCY', r'^(NAZWA )?ODBIORC', False, True),
    ('NUMER ZO', r'^(NUMER ZO|ZO)$', False, True),
    ('KOLOR', r'^KOLOR$', False, False),
    ('StreamSoft', r'^STREAMSOFT$', False, False),
    ('WYPALANIE', r'^WYPAL', True, False),
    ('LASER', r'^LASER$', True, False),
    ('GIĘCIE OB.', r'^GIECIE( OB\.?| OBU\.?)?$', True, True),
    ('GIĘCIE DR', r'^GIECIE DR', True, True),
    ('SPAW. OB.', r'^(SPAW\.?( OB\.?)?|GRZ/SP)$', True, True),
    ('SPAW DRZW.', r'^(SPAW )?DRZW', True, True),
    ('MASKI', r'^MAS(KI)?$', True, True),
    ('ELEMENTY', None, False, True),      # kolumna wirtualna: elementy kontroli etapowej zlecenia
    ('CZYSZ. OB.', r'^(CZYSZ\.?( OB\.?)?|WYCZYSZCZONE)$', True, True),
    ('CZYSZ. DRZW.', r'^CZYSZ\.? DRZW', True, True),
    ('MYJNIA', r'^MYJ', True, True),
    ('MAL', r'^MAL', True, True),
    ('SZYKOWANIE', r'^SZYK', True, False),
    ('Drzwi.ZAW.', r'^(D\.? ?ZAW|DRZWI\.? ?ZAW)', False, True),
    ('Drzwi. PRZYW.', r'^(D\.? ?(PRZYW|DOST)|DRZWI\.? ?PRZYW)', False, True),
    ('SKŁADANIE', r'^(SKLAD|ZLOZ)', True, True),
    ('KONTROLA KOŃCOWA', r'^(KJ|KONTROLA KONCOWA)', True, True),
    ('PAKOWANIE', r'^PAK', True, True),
]
VIRTUAL_ELEMENTS = 'el'      # klucz kolumny wirtualnej w widoku (obok liczb = indeksów kolumn pliku)

# Nazwy nadawane automatycznie przez wcześniejsze wersje układu: takie nazwy można podmienić na obecne,
# a nazwy wpisane przez użytkownika zostają.
LEGACY_NAMES = {'MYJ.', 'D.zaw', 'D. PRZYW.', 'KJ', 'PAK.'}

# Arkusze bez wiersza nagłówków w pliku: nazwy etapów wynikają z pozycji kolumn (PROFIT ma te same
# kolumny etapów co RZ RW IF: GIĘCIE, GRZ/SP, DRZWI, MASKI, CZYSZ., MYJ, MAL., SKŁAD., PAK, KJ).
HEADERLESS_STAGES = {
    'PROFIT': {11: 'GIĘCIE', 12: 'GRZ/SP', 13: 'DRZWI', 15: 'MASKI', 16: 'CZYSZ.', 17: 'MYJ',
               18: 'MAL.', 19: 'SKŁAD.', 21: 'PAK', 22: 'KJ'},
}


def default_enabled(sheet_name, headerless):
    """Układ obejmuje wszystkie arkusze (można go wyłączyć dla wybranego)."""
    return True


def effective(sheet_name, origs):
    """Nazwy kolumn do dopasowania: dla arkusza bez nagłówków podstawia nazwy etapów z pozycji."""
    stages = HEADERLESS_STAGES.get(str(sheet_name or '').strip().upper())
    if not stages:
        return list(origs)
    return [stages.get(i, n) for i, n in enumerate(origs)]


def match(origs):
    """Dopasowanie kolumn do wzorca: {indeks kolumny: indeks we wzorcu}. Kolumna ma jedno
    dopasowanie, kolumny nazwane tylko literą (puste nagłówki) są pomijane."""
    names = {i: plain(n) for i, n in enumerate(origs)
             if n and str(n).strip() != get_column_letter(i + 1)}
    out, used = {}, set()
    for ci, (_name, pattern, _stage, _vis) in enumerate(CANON):
        if pattern is None:
            continue
        rx = re.compile(pattern)
        for i, n in names.items():
            if i not in used and rx.search(n):
                out[i] = ci
                used.add(i)
                break
    return out


def display_order(origs, sheet_name=None):
    """Kolejność wyświetlania: kolumny wzorca w jego kolejności (z kolumną wirtualną ELEMENTY
    w jej miejscu), potem pozostałe kolumny jak w pliku. Elementy: indeksy kolumn albo 'el'."""
    m = match(effective(sheet_name, origs))
    by_canon = {ci: i for i, ci in m.items()}
    out = []
    for ci, entry in enumerate(CANON):
        if entry[1] is None:
            out.append(VIRTUAL_ELEMENTS)
        elif ci in by_canon:
            out.append(by_canon[ci])
    return out + [i for i in range(len(origs)) if i not in m]


def default_visible(origs, sheet_name=None):
    """Indeksy kolumn pliku domyślnie widocznych we wzorcu (pozostałe są domyślnie ukryte)."""
    return {i for i, ci in match(effective(sheet_name, origs)).items() if CANON[ci][3]}


def canon_names(origs, sheet_name=None):
    """{indeks kolumny: nazwa we wzorcu} dla dopasowanych kolumn."""
    return {i: CANON[ci][0] for i, ci in match(effective(sheet_name, origs)).items()}


def stage_indexes(origs, sheet_name=None):
    return {i for i, ci in match(effective(sheet_name, origs)).items() if CANON[ci][2]}


def canon_name_set():
    return {e[0] for e in CANON} | LEGACY_NAMES


def sync_names(sheet):
    """Nadaje kolumnom arkusza nazwy wzorca (kolumny bez własnej nazwy użytkownika oraz z nazwą nadaną
    przez wcześniejszą wersję układu). Zwraca True, gdy coś zmieniono (wołający zatwierdza transakcję)."""
    import json
    from models import ProdStageColumn
    cfg = sheetcfg.load(sheet.name)
    columns = list(sheet.columns)
    origs = [sheetcfg.orig_name(cfg, columns, i) for i in range(len(columns))]
    known = canon_name_set()
    changed = False
    for i, canon in canon_names(origs, sheet.name).items():
        cur = columns[i]
        ent = cfg['names'].get(str(i))
        if cur == canon or (ent is not None and ent['name'] not in known):
            continue                              # już jest, albo nazwa wpisana przez użytkownika
        columns[i] = canon
        if canon != origs[i]:
            cfg['names'][str(i)] = {'orig': origs[i], 'name': canon}
        else:
            cfg['names'].pop(str(i), None)
        for st in ProdStageColumn.query.filter_by(sheet_id=sheet.id, col_index=i):
            if st.label == cur:
                st.label = canon[:64]
        changed = True
    if changed:
        sheet.columns_json = json.dumps(columns, ensure_ascii=False)
        sheetcfg.save(sheet.name, cfg)
    return changed
