"""Tryb prezentacji: strona Scenariusz (kroki pokazu ze stanem) i strona "Pytania do Streamsoft"."""
from flask import render_template
from flask_login import login_required

from . import prestiz_kj_bp
from .access import view_required
from .scenario import scenario_state
from .service import SETTING_CHOICES, get_setting


@prestiz_kj_bp.route('/scenariusz')
@login_required
@view_required
def scenario():
    return render_template('prestiz_kj/scenariusz.html', **scenario_state())


@prestiz_kj_bp.route('/pytania')
@login_required
@view_required
def questions():
    modes = {key: SETTING_CHOICES[key].get(get_setting(key), '-') for key in ('approval_mode', 'scrap_mode')}
    return render_template('prestiz_kj/pytania.html', modes=modes)
