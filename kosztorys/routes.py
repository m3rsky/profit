import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import (render_template, redirect, url_for, request,
                   flash, jsonify, abort, session, current_app, make_response)
from flask_login import login_required, current_user

from models import (db, AuditLog, CabinetType, MaterialPrice, LaborRate,
                    Quote, QuoteConfig, CatalogProduct)
from .calculator import calculate, get_prices_dict, get_labor_dict
from . import kosztorys_bp

UTC = timezone.utc


# ── Pomocnicze ────────────────────────────────────────────────────────────────

def _audit(action, target_type=None, target_id=None, detail=None):
    try:
        uid = current_user.id if current_user.is_authenticated else None
        db.session.add(AuditLog(
            user_id=uid, action=action, target_type=target_type,
            target_id=target_id, detail=detail, ip=request.remote_addr
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Audit error: %s', exc)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def _next_quote_number() -> str:
    year = datetime.now(UTC).year
    prefix = f'KSZ-{year}-'
    last = (Quote.query
            .filter(Quote.number.like(f'{prefix}%'))
            .order_by(Quote.id.desc())
            .first())
    n = int(last.number.split('-')[-1]) + 1 if last else 1
    return f'{prefix}{n:04d}'


def _prices() -> dict:
    return get_prices_dict(MaterialPrice.query.all())


def _labor() -> dict:
    return get_labor_dict(LaborRate.query.all())


def _family_code(cabinet_type_id) -> str:
    ct = db.session.get(CabinetType, cabinet_type_id)
    return ct.code if ct else 'PSH_IP65'


def _run_calc(cfg_obj) -> dict:
    """Uruchamia odpowiedni kalkulator dla obiektu QuoteConfig."""
    cfg = {c.key: getattr(cfg_obj, c.key)
           for c in cfg_obj.__table__.columns
           if c.key not in ('id', 'quote_id', 'calculation')}
    family = _family_code(cfg_obj.quote.cabinet_type_id)
    return calculate(family, cfg, _prices(), _labor())


# ── Lista wycen ───────────────────────────────────────────────────────────────

@kosztorys_bp.route('/')
@login_required
def list_quotes():
    status_filter = request.args.get('status', 'all')
    q = Quote.query.order_by(Quote.created_at.desc())
    if status_filter != 'all':
        q = q.filter_by(status=status_filter)
    quotes = q.all()
    return render_template('kosztorys/list.html',
                           quotes=quotes, active_tab=status_filter)


# ── Bulk delete wycen ────────────────────────────────────────────────────────

@kosztorys_bp.route('/bulk-delete', methods=['POST'])
@login_required
def bulk_delete_quotes():
    ids = request.form.getlist('quote_ids', type=int)
    if not ids:
        flash('Nie zaznaczono żadnych wycen.', 'warning')
        return redirect(url_for('kosztorys.list_quotes'))
    quotes = Quote.query.filter(Quote.id.in_(ids)).all()
    deleted = 0
    for q in quotes:
        if current_user.is_admin or q.created_by_id == current_user.id:
            number = q.number
            db.session.delete(q)
            _audit('quote_delete', 'quote', q.id, number)
            deleted += 1
    db.session.commit()
    flash(f'Usunięto {deleted} wycen.', 'success')
    return redirect(url_for('kosztorys.list_quotes'))


# ── Nowa wycena ───────────────────────────────────────────────────────────────

@kosztorys_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_quote():
    cabinet_types = CabinetType.query.filter_by(is_active=True).all()

    if request.method == 'POST':
        f = request.form

        cabinet_type_id = int(f.get('cabinet_type_id', 0))
        cabinet_type = db.session.get(CabinetType, cabinet_type_id)
        if not cabinet_type:
            flash('Wybierz typ szafy.', 'error')
            return render_template('kosztorys/new.html', cabinet_types=cabinet_types)

        try:
            width  = int(f['width'])
            height = int(f['height'])
            depth  = int(f['depth'])
        except (ValueError, KeyError):
            flash('Podaj poprawne wymiary.', 'error')
            return render_template('kosztorys/new.html', cabinet_types=cabinet_types)

        cfg_data = _cfg_from_form(f)
        cfg_dict = {**cfg_data, 'width': width, 'height': height, 'depth': depth}
        result = calculate(cabinet_type.code, cfg_dict, _prices(), _labor())

        quote = Quote(
            number=_next_quote_number(),
            client_name=f.get('client_name', '').strip() or '—',
            cabinet_type_id=cabinet_type_id,
            status='draft',
            notes=f.get('notes', '').strip(),
            created_by_id=current_user.id,
        )
        db.session.add(quote)
        db.session.flush()

        config = QuoteConfig(
            quote_id=quote.id,
            width=width, height=height, depth=depth,
            **cfg_data,
            calculation=result,
        )
        db.session.add(config)
        db.session.commit()

        _audit('quote_create', 'quote', quote.id, quote.number)
        flash(f'Wycena {quote.number} zostala utworzona.', 'success')
        return redirect(url_for('kosztorys.detail_quote', quote_id=quote.id))

    return render_template('kosztorys/new.html', cabinet_types=cabinet_types)


# ── Kreator (4-krokowy wizard) ────────────────────────────────────────────────

@kosztorys_bp.route('/kreator', methods=['GET', 'POST'])
@kosztorys_bp.route('/kreator/', methods=['GET', 'POST'])
@login_required
def kreator():
    cabinet_types = CabinetType.query.filter_by(is_active=True).all()
    catalog = (CatalogProduct.query
               .filter_by(is_active=True)
               .order_by(CatalogProduct.family, CatalogProduct.sort_order,
                         CatalogProduct.width)
               .all())

    # Grupuj katalog po family
    catalog_by_family: dict = {}
    for p in catalog:
        catalog_by_family.setdefault(p.family, []).append(p)

    if request.method == 'POST':
        f = request.form

        cabinet_type_id = int(f.get('cabinet_type_id', 0))
        ct = db.session.get(CabinetType, cabinet_type_id)
        if not ct:
            flash('Wybierz typ szafy.', 'error')
            return render_template('kosztorys/kreator.html',
                                   cabinet_types=cabinet_types,
                                   catalog_by_family=catalog_by_family)

        try:
            width  = int(f['width'])
            height = int(f['height'])
            depth  = int(f['depth'])
        except (ValueError, KeyError):
            flash('Podaj poprawne wymiary.', 'error')
            return render_template('kosztorys/kreator.html',
                                   cabinet_types=cabinet_types,
                                   catalog_by_family=catalog_by_family)

        cfg_data = _cfg_from_form(f)
        cfg_dict = {**cfg_data, 'width': width, 'height': height, 'depth': depth}
        result = calculate(ct.code, cfg_dict, _prices(), _labor())

        quote = Quote(
            number=_next_quote_number(),
            client_name=f.get('client_name', '').strip() or '—',
            cabinet_type_id=cabinet_type_id,
            status='draft',
            notes=f.get('notes', '').strip(),
            created_by_id=current_user.id,
        )
        db.session.add(quote)
        db.session.flush()

        config = QuoteConfig(
            quote_id=quote.id,
            width=width, height=height, depth=depth,
            **cfg_data,
            calculation=result,
        )
        db.session.add(config)
        db.session.commit()

        _audit('quote_create_kreator', 'quote', quote.id, quote.number)
        flash(f'Wycena {quote.number} zostala zapisana.', 'success')
        return redirect(url_for('kosztorys.detail_quote', quote_id=quote.id))

    return render_template('kosztorys/kreator.html',
                           cabinet_types=cabinet_types,
                           catalog_by_family=catalog_by_family)


# ── Podgląd wyceny ────────────────────────────────────────────────────────────

@kosztorys_bp.route('/<int:quote_id>')
@login_required
def detail_quote(quote_id):
    quote = Quote.query.get_or_404(quote_id)
    calc  = quote.config.calculation if quote.config else {}
    return render_template('kosztorys/detail.html', quote=quote, calc=calc)


# ── Edycja wyceny ─────────────────────────────────────────────────────────────

@kosztorys_bp.route('/<int:quote_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_quote(quote_id):
    quote = Quote.query.get_or_404(quote_id)
    cabinet_types = CabinetType.query.filter_by(is_active=True).all()

    if request.method == 'POST':
        f = request.form
        action = f.get('_action', 'save')

        quote.client_name     = f.get('client_name', '').strip() or '—'
        quote.notes           = f.get('notes', '').strip()
        quote.cabinet_type_id = int(f.get('cabinet_type_id', quote.cabinet_type_id))

        if action == 'change_status':
            new_status = f.get('status')
            if new_status in ('draft', 'sent', 'accepted', 'closed'):
                quote.status = new_status

        cfg = quote.config
        cfg.width  = int(f.get('width', cfg.width))
        cfg.height = int(f.get('height', cfg.height))
        cfg.depth  = int(f.get('depth', cfg.depth))

        cfg_data = _cfg_from_form(f)
        for k, v in cfg_data.items():
            setattr(cfg, k, v)

        cfg_dict = {k: getattr(cfg, k) for k in cfg_data}
        cfg_dict.update({'width': cfg.width, 'height': cfg.height, 'depth': cfg.depth})
        family = _family_code(quote.cabinet_type_id)
        cfg.calculation = calculate(family, cfg_dict, _prices(), _labor())

        quote.updated_at = datetime.now(UTC)
        db.session.commit()

        _audit('quote_edit', 'quote', quote.id, quote.number)
        flash('Wycena zaktualizowana.', 'success')
        return redirect(url_for('kosztorys.detail_quote', quote_id=quote.id))

    return render_template('kosztorys/edit.html',
                           quote=quote, cabinet_types=cabinet_types)


# ── Usuń wycenę ───────────────────────────────────────────────────────────────

@kosztorys_bp.route('/<int:quote_id>/delete', methods=['POST'])
@login_required
def delete_quote(quote_id):
    quote = Quote.query.get_or_404(quote_id)
    if not current_user.is_admin and quote.created_by_id != current_user.id:
        abort(403)
    number = quote.number
    db.session.delete(quote)
    db.session.commit()
    _audit('quote_delete', 'quote', quote_id, number)
    flash(f'Wycena {number} zostala usunieta.', 'success')
    return redirect(url_for('kosztorys.list_quotes'))


# ── AJAX: podgląd kalkulacji (bez zapisu) ────────────────────────────────────

@kosztorys_bp.route('/preview', methods=['POST'])
@login_required
def preview_calc():
    """Zwraca JSON z wynikiem kalkulacji dla podgladu na zywo."""
    f = request.get_json(silent=True) or request.form
    try:
        cfg = _cfg_from_form(f)
        cfg['width']  = int(f.get('width', 0))
        cfg['height'] = int(f.get('height', 0))
        cfg['depth']  = int(f.get('depth', 0))
        cabinet_type_id = int(f.get('cabinet_type_id', 0))
        family = _family_code(cabinet_type_id) if cabinet_type_id else 'PSH_IP65'
        result = calculate(family, cfg, _prices(), _labor())
        return jsonify(result)
    except Exception as exc:
        current_app.logger.error('Preview calc error: %s', exc)
        return jsonify({'error': str(exc)}), 400


# ── Admin: ceny materiałów ────────────────────────────────────────────────────

@kosztorys_bp.route('/admin/prices', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_prices():
    if request.method == 'POST':
        for mp in MaterialPrice.query.all():
            val = request.form.get(f'price_{mp.code}', '')
            try:
                mp.price = float(val.replace(',', '.'))
                mp.updated_at = datetime.now(UTC)
                mp.updated_by_id = current_user.id
            except ValueError:
                pass
        db.session.commit()
        _audit('prices_update', detail='material prices updated')
        flash('Ceny materialow zostaly zaktualizowane.', 'success')
        return redirect(url_for('kosztorys.admin_prices'))

    prices = MaterialPrice.query.order_by(MaterialPrice.id).all()
    return render_template('kosztorys/admin_prices.html', prices=prices)


# ── Admin: stawki robocizny ───────────────────────────────────────────────────

@kosztorys_bp.route('/admin/rates', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_rates():
    FIELDS = ('laser', 'bending', 'welding', 'grinding', 'assembly', 'packaging')

    if request.method == 'POST':
        for lr in LaborRate.query.all():
            for field in FIELDS:
                val = request.form.get(f'{lr.volume_range}_{field}', '')
                try:
                    setattr(lr, field, float(val.replace(',', '.')))
                except ValueError:
                    pass
            lr.updated_at = datetime.now(UTC)
        db.session.commit()
        _audit('rates_update', detail='labor rates updated')
        flash('Stawki robocizny zostaly zaktualizowane.', 'success')
        return redirect(url_for('kosztorys.admin_rates'))

    rates = LaborRate.query.order_by(LaborRate.id).all()
    fields = FIELDS
    return render_template('kosztorys/admin_rates.html', rates=rates, fields=fields)


# ── Admin: typy szaf ──────────────────────────────────────────────────────────

@kosztorys_bp.route('/admin/cabinets', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_cabinets():
    if request.method == 'POST':
        action = request.form.get('_action')
        if action == 'add':
            code = request.form.get('code', '').strip().upper()
            name = request.form.get('name', '').strip()
            desc = request.form.get('description', '').strip()
            if code and name:
                if CabinetType.query.filter_by(code=code).first():
                    flash('Typ szafy o tym kodzie juz istnieje.', 'error')
                else:
                    db.session.add(CabinetType(code=code, name=name, description=desc))
                    db.session.commit()
                    flash(f'Dodano typ: {name}', 'success')
        elif action == 'toggle':
            ct_id = int(request.form.get('cabinet_id', 0))
            ct = db.session.get(CabinetType, ct_id)
            if ct:
                ct.is_active = not ct.is_active
                db.session.commit()
                flash('Status zmieniony.', 'success')
        return redirect(url_for('kosztorys.admin_cabinets'))

    cabinets = CabinetType.query.order_by(CabinetType.id).all()
    return render_template('kosztorys/admin_cabinets.html', cabinets=cabinets)


# ── Admin: katalog produktów ──────────────────────────────────────────────────

@kosztorys_bp.route('/admin/catalog', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_catalog():
    FAMILIES = [
        ('compact', 'PSH Kompakt'),
        ('ip65',    'PSH IP65'),
        ('inox',    'PSH INOX'),
        ('modular', 'PSH Modular'),
    ]

    if request.method == 'POST':
        action = request.form.get('_action')

        if action == 'add':
            code   = request.form.get('code', '').strip().upper()
            family = request.form.get('family', 'compact')
            name   = request.form.get('name', '').strip()
            try:
                w = int(request.form.get('width',  0))
                h = int(request.form.get('height', 0))
                d = int(request.form.get('depth',  0))
                price = float(request.form.get('catalog_price', 0))
            except (ValueError, TypeError):
                flash('Podaj poprawne dane liczbowe.', 'error')
                return redirect(url_for('kosztorys.admin_catalog'))
            if not code or not name:
                flash('Kod i nazwa sa wymagane.', 'error')
            elif CatalogProduct.query.filter_by(code=code).first():
                flash('Produkt o tym kodzie juz istnieje.', 'error')
            else:
                sort_order = (CatalogProduct.query
                              .filter_by(family=family)
                              .count())
                db.session.add(CatalogProduct(
                    code=code, family=family, name=name,
                    width=w, height=h, depth=d,
                    catalog_price=price, sort_order=sort_order,
                ))
                db.session.commit()
                flash(f'Dodano: {name}', 'success')

        elif action == 'update_price':
            prod_id = int(request.form.get('prod_id', 0))
            prod = db.session.get(CatalogProduct, prod_id)
            if prod:
                try:
                    prod.catalog_price = float(
                        request.form.get(f'price_{prod_id}', prod.catalog_price))
                    prod.is_active = bool(request.form.get(f'active_{prod_id}'))
                    db.session.commit()
                    flash('Zaktualizowano produkt.', 'success')
                except ValueError:
                    flash('Niepoprawna cena.', 'error')

        elif action == 'save_all':
            for prod in CatalogProduct.query.all():
                val = request.form.get(f'price_{prod.id}', '')
                active = bool(request.form.get(f'active_{prod.id}'))
                try:
                    prod.catalog_price = float(val.replace(',', '.'))
                    prod.is_active = active
                except ValueError:
                    pass
            db.session.commit()
            _audit('catalog_update', detail='catalog prices updated')
            flash('Katalog zaktualizowany.', 'success')

        elif action == 'delete':
            prod_id = int(request.form.get('prod_id', 0))
            prod = db.session.get(CatalogProduct, prod_id)
            if prod:
                db.session.delete(prod)
                db.session.commit()
                flash('Produkt usuniety.', 'success')

        return redirect(url_for('kosztorys.admin_catalog'))

    products = (CatalogProduct.query
                .order_by(CatalogProduct.family, CatalogProduct.sort_order,
                          CatalogProduct.width)
                .all())
    products_by_family: dict = {}
    for prod in products:
        products_by_family.setdefault(prod.family, []).append(prod)

    return render_template('kosztorys/admin_catalog.html',
                           products_by_family=products_by_family,
                           families=FAMILIES)


# ── Eksport Excel ─────────────────────────────────────────────────────────────

@kosztorys_bp.route('/<int:quote_id>/excel')
@login_required
def export_excel(quote_id):
    quote = Quote.query.get_or_404(quote_id)
    from .excel_export import generate_excel
    output = generate_excel(quote)
    response = make_response(output.getvalue())
    filename = f'Kosztorys_{quote.number}.xlsx'
    response.headers['Content-Type'] = (
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ── Eksport PDF ───────────────────────────────────────────────────────────────

@kosztorys_bp.route('/<int:quote_id>/pdf')
@login_required
def export_pdf(quote_id):
    quote = Quote.query.get_or_404(quote_id)
    from .pdf_export import generate_pdf
    pdf_bytes = generate_pdf(quote)
    response = make_response(pdf_bytes)
    filename = f'Kosztorys_{quote.number}.pdf'
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ── Helper: parsowanie formularza ─────────────────────────────────────────────

def _cfg_from_form(f) -> dict:
    """Wyciaga pola konfiguracji szafy z obiektu formularza (dict-like)."""
    def _bool(key): return bool(f.get(key))
    def _int(key, default=0):
        try:
            return int(f.get(key, default))
        except (ValueError, TypeError):
            return default
    def _float(key, default=0.0):
        try:
            return float(str(f.get(key, default)).replace(',', '.'))
        except (ValueError, TypeError):
            return default

    return {
        'thickness_body':     _float('thickness_body', 1.2),
        'thickness_plate':    _float('thickness_plate', 3.0),
        'has_mounting_plate': _bool('has_mounting_plate'),
        'back_welded':        _bool('back_welded'),
        'back_screwed':       _bool('back_screwed'),
        'door_single':        _bool('door_single'),
        'door_double':        _bool('door_double'),
        'door_reinforcement': _int('door_reinforcement'),
        'lock_three_point':   _bool('lock_three_point'),
        'lock_cam':           _bool('lock_cam'),
        'lock_standard':      _bool('lock_standard'),
        'vertical_beam':      _int('vertical_beam'),
        'plinth':             _bool('plinth'),
        'cable_entries':      _int('cable_entries'),
        'canopy':             _bool('canopy'),
        'monoblok':           _int('monoblok'),
        'back_seal':          _bool('back_seal'),
        'non_standard_color': _bool('non_standard_color'),
        'design_hours':       _int('design_hours'),
        'stud_m6_qty':        _int('stud_m6_qty', 0),
        'nut_m8_qty':         _int('nut_m8_qty', 0),
        'hinge_qty':          _int('hinge_qty', 0),
        'screw_cap_qty':      _int('screw_cap_qty', 0),
        'plug_qty':           _int('plug_qty', 0),
        'margin':             _float('margin', 2.15),
        'discount_pct':       _float('discount_pct', 0.0),
        'bonus_pct':          _float('bonus_pct', 0.0),
    }
