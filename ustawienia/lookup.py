"""Rozpoznawanie kodów skanowanych na stanowisku: bufory i plakietki pracowników działów."""
from models import Buffer, DepartmentEmployee

BADGE_PREFIX = 'PSH-DEP:'


def norm_code(code):
    """Kod bufora do porównań: bez spacji, wielkimi literami ("a 001" = "A001")."""
    return ''.join(str(code or '').split()).upper()[:64]


def find_buffer(code):
    """Bufor po kodzie z etykiety (QR, kod kreskowy albo wpisany ręcznie). Zwraca też bufory
    nieaktywne, decyzję podejmuje wołający."""
    code = norm_code(code)
    if not code:
        return None
    return Buffer.query.filter_by(code=code).first()


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
