"""Układ wzorcowy kolumn listy produkcyjnej (wzór: arkusz PSH).

Kolumny każdego arkusza z nagłówkami są dopasowywane po nazwie do kolumn wzorca (np. `SPAW.` i
`GRZ/SP` to `SPAW. OB.`, `DRZWI` to `SPAW DRZW.`, `ZŁOŻONE` to `SKŁADANIE`), dostają nazwy
wzorca i są wyświetlane w kolejności wzorca. Kolumn bez odpowiednika we wzorcu (np. LANTEK,
D.posp) nie ruszamy, trafiają za kolumny wzorca. Kolumny wzorca, których arkusz nie ma, po prostu
nie są pokazywane. WYPALANIE, LASER i SZYKOWANIE to kolumny dodatkowe: widać je tylko w arkuszach,
które je mają (WYPALANIE i LASER przed GIĘCIEM, SZYKOWANIE przed D.zaw).

Dane w bazie i eksport do Excela się nie zmieniają, bo zmienia się tylko nazwa i kolejność w widoku.
Układ obejmuje wszystkie arkusze i można go wyłączyć dla wybranego (ustawienie `layout` w sheetcfg)."""
import re

from openpyxl.utils import get_column_letter

from .controllink import plain

# (nazwa we wzorcu, wzorzec nazwy po plain(), czy kolumna etapu produkcji)
CANON = [
    ('ZAM', r'^(ZAM\.?|DATA PRODUKCJI)$', False),
    ('DATA', r'^(DATA|ROZP\.?|ROZ\.?)$', False),
    ('CZAS REALIZACJI', r'^CZAS REA', False),
    ('TERMIN REALIZACJI', r'^TERMIN REA', False),
    ('NAZWA TOW.', r'^NAZWA( TOW\.?)?$', False),
    ('ILOŚĆ', r'^(ILOSC|SZT)$', False),
    ('Z', r'^Z$', False),
    ('NAZWA ODBIORCY', r'^(NAZWA )?ODBIORC', False),
    ('NUMER ZO', r'^(NUMER ZO|ZO)$', False),
    ('KOLOR', r'^KOLOR$', False),
    ('StreamSoft', r'^STREAMSOFT$', False),
    ('WYPALANIE', r'^WYPAL', True),
    ('LASER', r'^LASER$', True),
    ('GIĘCIE OB.', r'^GIECIE( OB\.?| OBU\.?)?$', True),
    ('GIĘCIE DR', r'^GIECIE DR', True),
    ('SPAW. OB.', r'^(SPAW\.?( OB\.?)?|GRZ/SP)$', True),
    ('SPAW DRZW.', r'^(SPAW )?DRZW', True),
    ('MASKI', r'^MAS(KI)?$', True),
    ('CZYSZ. OB.', r'^(CZYSZ\.?( OB\.?)?|WYCZYSZCZONE)$', True),
    ('CZYSZ. DRZW.', r'^CZYSZ\.? DRZW', True),
    ('MYJ.', r'^MYJ', True),
    ('MAL', r'^MAL', True),
    ('SZYKOWANIE', r'^SZYK', True),
    ('D.zaw', r'^D\.? ?ZAW', False),
    ('D. PRZYW.', r'^D\.? ?(PRZYW|DOST)', False),
    ('SKŁADANIE', r'^(SKLAD|ZLOZ)', True),
    ('PAK.', r'^PAK', True),
    ('KJ', r'^KJ', True),
]

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
    for ci, (_name, pattern, _stage) in enumerate(CANON):
        rx = re.compile(pattern)
        for i, n in names.items():
            if i not in used and rx.search(n):
                out[i] = ci
                used.add(i)
                break
    return out


def display_order(origs, sheet_name=None):
    """Kolejność wyświetlania: kolumny wzorca w jego kolejności, potem pozostałe jak w pliku."""
    m = match(effective(sheet_name, origs))
    matched = sorted(m, key=lambda i: m[i])
    rest = [i for i in range(len(origs)) if i not in m]
    return matched + rest


def canon_names(origs, sheet_name=None):
    """{indeks kolumny: nazwa we wzorcu} dla dopasowanych kolumn."""
    return {i: CANON[ci][0] for i, ci in match(effective(sheet_name, origs)).items()}


def stage_indexes(origs, sheet_name=None):
    return {i for i, ci in match(effective(sheet_name, origs)).items() if CANON[ci][2]}
