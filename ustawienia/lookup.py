"""Rozpoznawanie plakietek pracowników działów skanowanych na stanowisku."""
from models import DepartmentEmployee

BADGE_PREFIX = 'PSH-DEP:'


def parse_badge(text):
    """Token z odczytu plakietki pracownika działu (`PSH-DEP:<token>`) albo None."""
    text = (text or '').strip()
    if not text.upper().startswith(BADGE_PREFIX):
        return None
    token = text[len(BADGE_PREFIX):].strip()
    return token or None


def find_department_employee(text):
    """Aktywny pracownik działu z odczytu plakietki albo None."""
    token = parse_badge(text)
    if not token:
        return None
    emp = DepartmentEmployee.query.filter_by(token=token).first()
    return emp if emp and emp.is_active else None
