"""Wpis pracownika (inicjały) w kolumnę etapu listy produkcyjnej."""
import json
import re

from models import db, ProdEntry, ProdSetting
from . import excel_io
from .routes import _data, _store_row_fields

SPLIT_RE = re.compile(r'\s*[/,+;]\s*')
MAX_PEOPLE = 2


def enforce_assignments():
    return ProdSetting.get('enforce_assignments', '0') == '1'


def employee_allowed(employee, stage):
    """Gdy przydziały są wymuszane, pracownik wpisuje się tylko w etapy swoich działów
    (etap bez działu jest dostępny dla wszystkich)."""
    if not enforce_assignments() or stage.department_id is None:
        return True
    return stage.department_id in employee.department_ids


def apply_entry(row, stage, employee, user_id=None, confirm=False):
    """Wpisuje inicjały pracownika w komórkę etapu.

    Zwraca dict(status, value, message): status = ok | already | confirm | forbidden.
    `confirm` potwierdza nietypowe sytuacje (trzecia osoba, komórka "-", inna wartość)."""
    sheet = row.sheet
    col = stage.col_index
    if col in sheet.formula_cols:
        return {'status': 'forbidden', 'message': 'Ta kolumna jest liczona formułą.'}
    if not employee_allowed(employee, stage):
        return {'status': 'forbidden',
                'message': f'{employee.name} nie ma przydziału do etapu „{stage.label}”.'}

    data = _data(row, len(sheet.columns))
    cur = data[col]
    text = '' if cur is None else str(cur).strip()
    initials = employee.initials.strip()

    if text in ('', 'x', 'X'):
        new = initials
    elif text == '-':
        if not confirm:
            return {'status': 'confirm', 'value': text,
                    'message': 'Etap oznaczony jako niewykonywany ("-"). Wpisać mimo to?'}
        new = initials
    elif isinstance(cur, (int, float)) or excel_io.ISO_DATE.match(text):
        if not confirm:
            return {'status': 'confirm', 'value': text,
                    'message': f'W komórce jest wartość „{text}”. Zastąpić inicjałami?'}
        new = initials
    else:
        people = [p for p in SPLIT_RE.split(text) if p]
        if initials.lower() in (p.lower() for p in people):
            return {'status': 'already', 'value': text,
                    'message': f'{employee.name} jest już wpisany(a) w tym etapie.'}
        if len(people) >= MAX_PEOPLE and not confirm:
            return {'status': 'confirm', 'value': text,
                    'message': f'W tym etapie są już {len(people)} osoby ({text}). Dopisać trzecią?'}
        new = f'{text}/{initials}'

    data[col] = new
    changed = set(json.loads(row.changed)) if row.changed else set()
    changed.add(col)
    row.changed = json.dumps(sorted(changed))
    _store_row_fields(row, data, sheet.mapping)
    f = excel_io.row_fields(data, sheet.mapping)
    db.session.add(ProdEntry(
        employee_id=employee.id, row_id=row.id, user_id=user_id, sheet_name=sheet.name,
        zo=row.zo, product=str(f.get('product') or '')[:250], col_label=stage.label,
        value_before=text or None, value_after=new))
    db.session.commit()
    return {'status': 'ok', 'value': new, 'message': f'Wpisano: {new}'}
