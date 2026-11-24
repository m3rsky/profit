"""Strona /admin/czas-pracy: próg bezczynności przy liczeniu czasu list kontrolnych. Dostęp tylko dla superusera."""
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import worktime
from models import ChecklistSession

czas_bp = Blueprint('czas', __name__, url_prefix='/admin/czas-pracy')


def superuser_only(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_superadmin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


@czas_bp.route('/')
@login_required
@superuser_only
def index():
    open_count = ChecklistSession.query.filter(ChecklistSession.ended_at.is_(None)).count()
    return render_template('admin/czas_pracy.html', minutes=worktime.idle_minutes(),
                           limits=(worktime.IDLE_MIN, worktime.IDLE_MAX), open_count=open_count,
                           grace=int(worktime.GRACE.total_seconds()))


@czas_bp.route('/zapisz', methods=['POST'])
@login_required
@superuser_only
def save():
    error = worktime.save_idle_minutes(request.form.get('work_idle_minutes', ''))
    flash(error or 'Ustawienie czasu pracy zapisane.', 'danger' if error else 'success')
    return redirect(url_for('czas.index'))
