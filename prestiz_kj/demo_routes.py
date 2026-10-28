from flask import redirect, request, url_for, flash
from flask_login import login_required

from . import prestiz_kj_bp
from .access import admin_required, audit
from .demo_data import seed_configuration, seed_orders, reset_scenario
from .demo_history import seed_history
from .service import reset_orders, reset_all


@prestiz_kj_bp.route('/demo/wczytaj', methods=['POST'])
@login_required
@admin_required
def demo_load():
    loaded_config = seed_configuration()
    loaded_orders = seed_orders()
    loaded_history = seed_history()
    if loaded_config or loaded_orders or loaded_history:
        audit('pkj_demo_load')
        parts = []
        if loaded_config:
            parts.append('konfiguracja, punkty, przyczyny, pracownicy demo')
        if loaded_orders:
            parts.append('zlecenia demo')
        if loaded_history:
            parts.append('dane historyczne z 4 tygodni (11 zleceń, kontrole, zawroty, zdjęcia)')
        flash('Wczytano dane demonstracyjne: ' + ', '.join(parts) + '.', 'success')
    else:
        flash('Dane demo już istnieją. Użyj "Resetuj zlecenia" albo "Resetuj wszystko", aby wczytać je od nowa.',
              'warning')
    return redirect(url_for('prestiz_kj.index'))


@prestiz_kj_bp.route('/demo/reset', methods=['POST'])
@login_required
@admin_required
def demo_reset_orders():
    """Kasuje zlecenia demo, kontrole i zawroty. Konfiguracja zostaje."""
    reset_orders()
    audit('pkj_demo_reset_orders')
    flash('Usunięto zlecenia demo, kontrole i zawroty. Konfiguracja została.', 'success')
    return redirect(url_for('prestiz_kj.index'))


@prestiz_kj_bp.route('/demo/reset-wszystko', methods=['POST'])
@login_required
@admin_required
def demo_reset_all():
    """Kasuje całą zawartość modułu (tylko tabele pkj_)."""
    reset_all()
    audit('pkj_demo_reset_all')
    flash('Usunięto całą zawartość modułu KJ Prestiż. Możesz wczytać dane demo od nowa.', 'success')
    return redirect(url_for('prestiz_kj.index'))


@prestiz_kj_bp.route('/demo/scenariusz-reset', methods=['POST'])
@login_required
@admin_required
def demo_reset_scenario():
    """Przywraca ZP-DEMO-001 do stanu sprzed pokazu. Konfiguracja i pozostałe zlecenia zostają."""
    from .models import PkjOperation
    if PkjOperation.query.first() is None:
        flash('Najpierw wczytaj dane demo.', 'warning')
    else:
        reset_scenario()
        audit('pkj_demo_reset_scenario')
        flash('Przywrócono zlecenie ZP-DEMO-001 do stanu początkowego.', 'success')
    target = request.form.get('next', '')
    return redirect(target if target.startswith('/kj-prestiz/') else url_for('prestiz_kj.orders_list'))
