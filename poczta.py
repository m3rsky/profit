"""Strona /admin/poczta: stan konta pocztowego, ustawienia powiadomień i wiadomość próbna.
Dostęp tylko dla superusera."""
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request, url_for)
from flask_login import current_user, login_required

import mailer
from models import AuditLog

poczta_bp = Blueprint('poczta', __name__, url_prefix='/admin/poczta')


def superuser_only(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_superadmin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


@poczta_bp.route('/')
@login_required
@superuser_only
def index():
    cfg = current_app.config
    logs = (AuditLog.query.filter(AuditLog.action.in_(('mail_sent', 'mail_failed')))
            .order_by(AuditLog.id.desc()).limit(15).all())
    return render_template('admin/poczta.html', settings=mailer.get_settings(),
                           configured=mailer.is_configured(),
                           account={'smtp': cfg.get('MAIL_SMTP_HOST'), 'port': cfg.get('MAIL_PORT'),
                                    'user': cfg.get('MAIL_USER')},
                           sent_hour=mailer.sent_last_hour(), logs=logs)


@poczta_bp.route('/zapisz', methods=['POST'])
@login_required
@superuser_only
def save():
    errors = mailer.save_settings(request.form)
    if errors:
        for e in errors:
            flash(e, 'danger')
    else:
        flash('Ustawienia poczty zapisane.', 'success')
    return redirect(url_for('poczta.index'))


@poczta_bp.route('/probna', methods=['POST'])
@login_required
@superuser_only
def test_message():
    to = (request.form.get('to') or '').strip()
    if not mailer.EMAIL_RE.match(to):
        flash('Podaj poprawny adres e-mail.', 'danger')
        return redirect(url_for('poczta.index'))
    ok, msg = mailer.send('PSH QC: wiadomość próbna',
                          'To jest wiadomość próbna z systemu PSH QC.\n\n'
                          'Jeśli ją widzisz, wysyłka poczty z serwera działa.',
                          [to], wait=True, ignore_switch=True)
    flash('Wiadomość próbna wysłana.' if ok else f'Nie udało się wysłać: {msg}', 'success' if ok else 'danger')
    return redirect(url_for('poczta.index'))
