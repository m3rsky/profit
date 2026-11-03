from functools import wraps

from flask import abort, current_app, request
from flask_login import current_user

from models import db, AuditLog

MODULE = 'kj_prestiz'


def view_required(f):
    """Dostęp do modułu (macierz uprawnień)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can(MODULE):
            abort(403)
        return f(*args, **kwargs)
    return decorated


def controller_required(f):
    """Moduł + uprawnienie kj_prestiz.control (aplikacja KJ, meldunki)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can(MODULE) or not current_user.can('kj_prestiz.control'):
            abort(403)
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Moduł + uprawnienie kj_prestiz.admin (konfiguracja, zlecenia, zatwierdzanie)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can(MODULE) or not current_user.can('kj_prestiz.admin'):
            abort(403)
        return f(*args, **kwargs)
    return decorated


def audit(action, target_id=None, detail=None):
    try:
        db.session.add(AuditLog(
            user_id=current_user.id if current_user.is_authenticated else None, action=action,
            target_type='pkj', target_id=target_id, detail=detail, ip=request.remote_addr))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('KJ Prestiż audit error: %s', exc)
