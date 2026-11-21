"""Zdjęcia i miejsce na dysku: ustawienia kompresji i retencji oraz uruchamianie konserwacji.

Strona tylko dla superusera. Kompresja i usuwanie działają partiami (kilkanaście sekund na żądanie),
a przeglądarka powtarza wywołania, aż nic nie zostanie, bo hosting przerywa długie żądania.
"""
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

import photo_tools

zdjecia_bp = Blueprint('zdjecia', __name__, url_prefix='/admin/zdjecia')


def superuser_only(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_superadmin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _folders():
    cfg = current_app.config
    base = current_app.root_path
    return cfg['UPLOAD_FOLDER'], cfg.get('QAR_UPLOAD_FOLDER', ''), base


@zdjecia_bp.route('/')
@login_required
@superuser_only
def index():
    uploads, qar, base = _folders()
    return render_template('admin/zdjecia.html', info=photo_tools.preview(uploads, qar, base),
                           labels=photo_tools.LABELS)


@zdjecia_bp.route('/zapisz', methods=['POST'])
@login_required
@superuser_only
def save():
    errors = photo_tools.save_settings(request.form)
    if errors:
        for e in errors:
            flash(e, 'danger')
    else:
        flash('Ustawienia zdjęć zapisane.', 'success')
    return redirect(url_for('zdjecia.index'))


@zdjecia_bp.route('/uruchom/<action>', methods=['POST'])
@login_required
@superuser_only
def run(action):
    uploads = current_app.config['UPLOAD_FOLDER']
    if action == 'kompresja':
        result = photo_tools.run_compress(uploads)
    elif action == 'usuwanie':
        if not (request.get_json(silent=True) or {}).get('confirm'):
            return jsonify({'error': 'Brak potwierdzenia.'}), 400
        result = photo_tools.run_purge(uploads, user_id=current_user.id)
    else:
        abort(404)
    if result is None:
        return jsonify({'error': 'Ta operacja jest wyłączona w ustawieniach (wartość 0).'}), 400
    result['finished'] = result['remaining'] == 0 or result['done'] == 0
    return jsonify(result)
