from flask import render_template, send_from_directory
from flask_login import login_required

from . import prestiz_kj_bp
from .access import view_required
from .demo_data import configuration_exists
from .service import module_counts, upload_folder


@prestiz_kj_bp.route('/')
@login_required
@view_required
def index():
    return render_template('prestiz_kj/index.html', counts=module_counts(),
                           has_config=configuration_exists())


@prestiz_kj_bp.route('/grafika/<path:filename>')
@login_required
@view_required
def serve_upload(filename):
    """Grafiki punktów i zdjęcia modułu (folder static/pkj_uploads, poza publicznym static)."""
    return send_from_directory(upload_folder(), filename)
