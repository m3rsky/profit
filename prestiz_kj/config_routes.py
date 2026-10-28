"""Konfiguracja modułu (admin): operacje (K1), materiały i grupy (K2, K4), punkty (K3), przyczyny, pracownicy demo,
ustawienia. Każda strona ma jeden formularz i przycisk "Zapisz wszystko" (jak w Kontroli etapowej)."""
import os
import uuid

from flask import (render_template, redirect, url_for, request, flash, abort, current_app)
from flask_login import login_required
from sqlalchemy import func

from models import db
from . import prestiz_kj_bp
from .access import admin_required, audit
from .models import (PkjOperation, PkjMaterialGroup, PkjMaterial, PkjCheckPoint, PkjReason, PkjEmployee,
                     PkjOrderOperation, PkjOrderMaterial, PkjReturn, PkjResult, PkjInspection,
                     SCOPE_ALL, SCOPE_OPERATION, SCOPE_MATERIAL, SCOPE_GROUP)
from .service import (SETTING_CHOICES, SETTING_DEFAULTS, get_setting, set_setting, remove_upload,
                      upload_folder)

TABS = [('operacje', 'Operacje', 'bi-diagram-3'), ('materialy', 'Materiały i grupy', 'bi-boxes'),
        ('punkty', 'Punkty kontrolne', 'bi-list-check'), ('przyczyny', 'Przyczyny niezgodności', 'bi-tags'),
        ('pracownicy', 'Pracownicy demo', 'bi-people'), ('ustawienia', 'Ustawienia', 'bi-sliders')]
# Slugi z kolumną `order` (przesuwanie góra/dół)
ORDERED = {'operacje': PkjOperation, 'materialy': PkjMaterial, 'punkty': PkjCheckPoint, 'przyczyny': PkjReason}


# ── Pomocnicze ────────────────────────────────────────────────────────────────

def _ids_with_prefix(form, prefix):
    return sorted({int(k[len(prefix):]) for k in form if k.startswith(prefix) and k[len(prefix):].isdigit()})


def _num(raw, label):
    """Liczba z pola tekstowego (przecinek lub kropka). Zwraca (wartość albo None, błąd albo None)."""
    raw = (raw or '').strip().replace(',', '.')
    if not raw:
        return None, None
    try:
        return float(raw), None
    except ValueError:
        return None, f'{label}: "{raw}" nie jest liczbą.'


def _name_taken(model, name, exclude_id=None):
    q = model.query.filter(func.lower(model.name) == name.lower())
    if exclude_id is not None:
        q = q.filter(model.id != exclude_id)
    return q.first() is not None


def _next_order(model):
    return (db.session.query(func.max(model.order)).scalar() or 0) + 1


def _parse_group(raw):
    raw = (raw or '').strip()
    if raw.isdigit() and db.session.get(PkjMaterialGroup, int(raw)):
        return int(raw)
    return None


def _save_image(file):
    """Zapisuje grafikę punktu po walidacji. Zwraca nazwę pliku albo rzuca ValueError z komunikatem."""
    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    if ext not in current_app.config['ALLOWED_EXTENSIONS']:
        raise ValueError('Dozwolone są tylko obrazy (png, jpg, gif, webp).')
    try:
        from PIL import Image
        Image.open(file).verify()
        file.seek(0)
    except Exception:
        raise ValueError('Plik nie jest prawidłowym obrazem.')
    name = f'pkjpt_{uuid.uuid4().hex}.{ext}'
    file.save(os.path.join(upload_folder(), name))
    return name


# ── Zapis formularzy (zwracają: zmienione, dodano, błędy; bez commitu) ─────────

def _apply_operations(form, files):
    changed, added, errors = 0, False, []
    ids = _ids_with_prefix(form, 'name_')
    ops = {o.id: o for o in PkjOperation.query.filter(PkjOperation.id.in_(ids)).all()} if ids else {}
    for oid in ids:
        op = ops.get(oid)
        if op is None:
            continue
        name = form.get(f'name_{oid}', '').strip()[:128]
        if not name:
            errors.append(f'Operacja nr {oid}: nazwa jest wymagana, zmiany pominięto.')
            continue
        if _name_taken(PkjOperation, name, oid):
            errors.append(f'Operacja "{name}" już istnieje, zmiany pominięto.')
            continue
        op.name, op.kj_required, op.is_active = name, form.get(f'kj_{oid}') == '1', form.get(f'active_{oid}') == '1'
        changed += db.session.is_modified(op)
    new = form.get('new_name', '').strip()[:128]
    if new:
        if _name_taken(PkjOperation, new):
            errors.append(f'Operacja "{new}" już istnieje.')
        else:
            db.session.add(PkjOperation(name=new, order=_next_order(PkjOperation),
                                        kj_required=form.get('new_kj') == '1'))
            added = True
    return changed, added, errors


def _apply_materials(form, files):
    changed, added, errors = 0, False, []
    gids = _ids_with_prefix(form, 'gname_')
    groups = {g.id: g for g in PkjMaterialGroup.query.filter(PkjMaterialGroup.id.in_(gids)).all()} if gids else {}
    for gid in gids:
        g = groups.get(gid)
        name = form.get(f'gname_{gid}', '').strip()[:128]
        if g is None:
            continue
        if not name or _name_taken(PkjMaterialGroup, name, gid):
            errors.append(f'Grupa nr {gid}: pusta lub zduplikowana nazwa, zmiany pominięto.')
            continue
        g.name = name
        changed += db.session.is_modified(g)
    new_group = form.get('new_group', '').strip()[:128]
    if new_group:
        if _name_taken(PkjMaterialGroup, new_group):
            errors.append(f'Grupa "{new_group}" już istnieje.')
        else:
            db.session.add(PkjMaterialGroup(name=new_group))
            added = True
    db.session.flush()
    ids = _ids_with_prefix(form, 'name_')
    mats = {m.id: m for m in PkjMaterial.query.filter(PkjMaterial.id.in_(ids)).all()} if ids else {}
    for mid in ids:
        m = mats.get(mid)
        if m is None:
            continue
        name = form.get(f'name_{mid}', '').strip()[:128]
        if not name or _name_taken(PkjMaterial, name, mid):
            errors.append(f'Materiał nr {mid}: pusta lub zduplikowana nazwa, zmiany pominięto.')
            continue
        m.name, m.kj_flag, m.is_active = name, form.get(f'kj_{mid}') == '1', form.get(f'active_{mid}') == '1'
        m.group_id = _parse_group(form.get(f'group_{mid}'))
        changed += db.session.is_modified(m)
    new = form.get('new_name', '').strip()[:128]
    if new:
        if _name_taken(PkjMaterial, new):
            errors.append(f'Materiał "{new}" już istnieje.')
        else:
            db.session.add(PkjMaterial(name=new, order=_next_order(PkjMaterial), kj_flag=form.get('new_kj') == '1',
                                       group_id=_parse_group(form.get('new_group_id'))))
            added = True
    return changed, added, errors


def _parse_target(raw):
    """Pole zakresu: all | op:<id> | mat:<id> | grp:<id>. Zwraca (zakres, operation_id, material_id, group_id)."""
    raw = (raw or 'all').strip()
    kind, _, ident = raw.partition(':')
    if raw == 'all':
        return SCOPE_ALL, None, None, None
    if not ident.isdigit():
        raise ValueError('Nieprawidłowy zakres punktu.')
    ident = int(ident)
    model, scope = {'op': (PkjOperation, SCOPE_OPERATION), 'mat': (PkjMaterial, SCOPE_MATERIAL),
                    'grp': (PkjMaterialGroup, SCOPE_GROUP)}.get(kind, (None, None))
    if model is None or db.session.get(model, ident) is None:
        raise ValueError('Wskazana operacja, materiał lub grupa nie istnieje.')
    return (scope, ident if scope == SCOPE_OPERATION else None, ident if scope == SCOPE_MATERIAL else None,
            ident if scope == SCOPE_GROUP else None)


def _point_fields(form, suffix, label):
    """Wspólny odczyt pól punktu (istniejącego `_<id>` albo nowego `new_`). Rzuca ValueError z opisem."""
    p = (lambda name: f'{name}_{suffix}') if not suffix == 'new' else (lambda name: f'new_{name}')
    title = form.get(p('title'), '').strip()[:256]
    if not title:
        raise ValueError(f'{label}: treść punktu jest wymagana.')
    kind = form.get(p('kind'), 'ok_nok')
    if kind not in ('ok_nok', 'value'):
        kind = 'ok_nok'
    vmin, err1 = _num(form.get(p('min')), f'{label} (min)')
    vmax, err2 = _num(form.get(p('max')), f'{label} (max)')
    if err1 or err2:
        raise ValueError(err1 or err2)
    if kind == 'value' and vmin is not None and vmax is not None and vmin > vmax:
        raise ValueError(f'{label}: wartość min jest większa od max.')
    scope, op_id, mat_id, grp_id = _parse_target(form.get(p('target')))
    if kind == 'ok_nok':
        vmin = vmax = None
    return dict(title=title, help_text=form.get(p('help'), '').strip() or None, kind=kind,
                unit=(form.get(p('unit'), '').strip()[:16] or None) if kind == 'value' else None,
                min_value=vmin, max_value=vmax, scope=scope, operation_id=op_id, material_id=mat_id,
                group_id=grp_id, photo_required_on_nok=form.get(p('photo')) == '1')


def _apply_image(point, file, remove, errors, label):
    if remove and point.image:
        remove_upload(point.image)
        point.image = None
    if file and file.filename:
        try:
            name = _save_image(file)
        except ValueError as exc:
            errors.append(f'{label}: {exc}')
            return
        remove_upload(point.image)
        point.image = name


def _apply_points(form, files):
    changed, added, errors = 0, False, []
    ids = _ids_with_prefix(form, 'title_')
    pts = {p.id: p for p in PkjCheckPoint.query.filter(PkjCheckPoint.id.in_(ids)).all()} if ids else {}
    for pid in ids:
        pt = pts.get(pid)
        if pt is None:
            continue
        try:
            fields = _point_fields(form, str(pid), f'Punkt nr {pid}')
        except ValueError as exc:
            errors.append(f'{exc} Zmiany tego punktu pominięto.')
            continue
        for k, v in fields.items():
            setattr(pt, k, v)
        pt.is_active = form.get(f'active_{pid}') == '1'
        had_image = pt.image
        _apply_image(pt, files.get(f'image_{pid}'), form.get(f'remove_image_{pid}') == '1', errors,
                     f'Punkt nr {pid}')
        changed += db.session.is_modified(pt) or pt.image != had_image
    if form.get('new_title', '').strip():
        try:
            fields = _point_fields(form, 'new', 'Nowy punkt')
            pt = PkjCheckPoint(order=_next_order(PkjCheckPoint), **fields)
            _apply_image(pt, files.get('new_image'), False, errors, 'Nowy punkt')
            db.session.add(pt)
            added = True
        except ValueError as exc:
            errors.append(str(exc))
    return changed, added, errors


def _apply_reasons(form, files):
    changed, added, errors = 0, False, []
    ids = _ids_with_prefix(form, 'name_')
    rows = {r.id: r for r in PkjReason.query.filter(PkjReason.id.in_(ids)).all()} if ids else {}
    for rid in ids:
        r = rows.get(rid)
        if r is None:
            continue
        name = form.get(f'name_{rid}', '').strip()[:128]
        if not name or _name_taken(PkjReason, name, rid):
            errors.append(f'Przyczyna nr {rid}: pusta lub zduplikowana nazwa, zmiany pominięto.')
            continue
        r.name, r.is_active = name, form.get(f'active_{rid}') == '1'
        changed += db.session.is_modified(r)
    new = form.get('new_name', '').strip()[:128]
    if new:
        if _name_taken(PkjReason, new):
            errors.append(f'Przyczyna "{new}" już istnieje.')
        else:
            db.session.add(PkjReason(name=new, order=_next_order(PkjReason)))
            added = True
    return changed, added, errors


def _apply_employees(form, files):
    changed, added, errors = 0, False, []
    ids = _ids_with_prefix(form, 'name_')
    rows = {e.id: e for e in PkjEmployee.query.filter(PkjEmployee.id.in_(ids)).all()} if ids else {}
    for eid in ids:
        e = rows.get(eid)
        if e is None:
            continue
        name = form.get(f'name_{eid}', '').strip()[:128]
        if not name:
            errors.append(f'Pracownik nr {eid}: nazwa jest wymagana, zmiany pominięto.')
            continue
        e.name, e.department = name, form.get(f'dept_{eid}', '').strip()[:64] or None
        e.is_active = form.get(f'active_{eid}') == '1'
        changed += db.session.is_modified(e)
    new = form.get('new_name', '').strip()[:128]
    if new:
        db.session.add(PkjEmployee(name=new, department=form.get('new_dept', '').strip()[:64] or None))
        added = True
    return changed, added, errors


def _apply_settings(form, files):
    errors, changed = [], 0
    for key, choices in SETTING_CHOICES.items():
        value = form.get(key)
        if value in choices:
            if value != get_setting(key):
                changed += 1
            set_setting(key, value)
        elif value is not None:
            errors.append('Nieprawidłowa wartość ustawienia.')
    for key, label in (('repair_hour_cost', 'Koszt godziny poprawki'), ('repair_op_hours', 'Czas operacji naprawczej'),
                       ('scrap_material_cost', 'Koszt materiału złomu')):
        cost, err = _num(form.get(key), label)
        if err or (cost is not None and cost < 0):
            errors.append(err or f'{label} nie może być ujemny.')
        elif cost is not None:
            text = f'{cost:g}'
            if text != get_setting(key):
                changed += 1
            set_setting(key, text)
    return changed, False, errors


APPLY = {'operacje': _apply_operations, 'materialy': _apply_materials, 'punkty': _apply_points,
         'przyczyny': _apply_reasons, 'pracownicy': _apply_employees, 'ustawienia': _apply_settings}


# ── Strony ────────────────────────────────────────────────────────────────────

def _back(slug):
    return redirect(url_for('prestiz_kj.config_page', slug=slug,
                            zakres=request.form.get('back_zakres') or None))


@prestiz_kj_bp.route('/konfiguracja')
@login_required
@admin_required
def config_index():
    return redirect(url_for('prestiz_kj.config_page', slug='operacje'))


@prestiz_kj_bp.route('/konfiguracja/<slug>')
@login_required
@admin_required
def config_page(slug):
    if slug not in APPLY:
        abort(404)
    ctx = {'slug': slug, 'tabs': TABS}
    if slug == 'operacje':
        ctx['items'] = PkjOperation.query.order_by(PkjOperation.order, PkjOperation.id).all()
    elif slug == 'materialy':
        ctx['items'] = PkjMaterial.query.order_by(PkjMaterial.order, PkjMaterial.id).all()
        ctx['groups'] = PkjMaterialGroup.query.order_by(PkjMaterialGroup.name).all()
    elif slug == 'punkty':
        zakres = request.args.get('zakres', '')
        q = PkjCheckPoint.query
        if zakres in (SCOPE_ALL, SCOPE_OPERATION, SCOPE_MATERIAL, SCOPE_GROUP):
            q = q.filter_by(scope=zakres)
        else:
            zakres = ''
        ctx.update(items=q.order_by(PkjCheckPoint.order, PkjCheckPoint.id).all(), zakres=zakres,
                   operations=PkjOperation.query.order_by(PkjOperation.order).all(),
                   materials=PkjMaterial.query.order_by(PkjMaterial.order).all(),
                   groups=PkjMaterialGroup.query.order_by(PkjMaterialGroup.name).all())
    elif slug == 'przyczyny':
        ctx['items'] = PkjReason.query.order_by(PkjReason.order, PkjReason.id).all()
    elif slug == 'pracownicy':
        ctx['items'] = PkjEmployee.query.order_by(PkjEmployee.name).all()
    else:
        ctx.update(choices=SETTING_CHOICES, current={k: get_setting(k) for k in SETTING_DEFAULTS})
    return render_template('prestiz_kj/konfiguracja.html', **ctx)


@prestiz_kj_bp.route('/konfiguracja/<slug>/zapisz-wszystko', methods=['POST'])
@login_required
@admin_required
def config_save_all(slug):
    if slug not in APPLY:
        abort(404)
    changed, added, errors = APPLY[slug](request.form, request.files)
    db.session.commit()
    for e in errors:
        flash(e, 'warning')
    if changed or added:
        audit('pkj_config_save', None, f'{slug}: zmienione={changed} nowy={added}')
        parts = []
        if changed:
            parts.append(f'zapisano zmiany: {changed}')
        if added:
            parts.append('dodano nowy wpis')
        flash(', '.join(parts).capitalize() + '.', 'success')
    elif not errors:
        flash('Brak zmian do zapisania.', 'info')
    return _back(slug)


@prestiz_kj_bp.route('/konfiguracja/<slug>/<int:item_id>/przesun/<direction>', methods=['POST'])
@login_required
@admin_required
def config_move(slug, item_id, direction):
    model = ORDERED.get(slug)
    if model is None or direction not in ('up', 'down'):
        abort(404)
    if request.form.get('save_first'):
        _, _, errors = APPLY[slug](request.form, request.files)
        for e in errors:
            flash(e, 'warning')
    db.session.flush()
    items = model.query.order_by(model.order, model.id).all()
    idx = next((i for i, it in enumerate(items) if it.id == item_id), None)
    if idx is None:
        abort(404)
    other = idx - 1 if direction == 'up' else idx + 1
    if 0 <= other < len(items):
        items.insert(other, items.pop(idx))
    for pos, it in enumerate(items, 1):   # przenumerowanie usuwa ewentualne duplikaty kolejności
        it.order = pos
    db.session.commit()
    return _back(slug)


def _usage_block(slug, item):
    """Komunikat, gdy element jest w użyciu i nie można go usunąć (inaczej None)."""
    if slug == 'operacje':
        if PkjCheckPoint.query.filter_by(operation_id=item.id).count():
            return 'Operacja ma przypisane punkty kontrolne. Usuń je lub zmień ich zakres.'
        if PkjOrderOperation.query.filter_by(operation_id=item.id).count():
            return 'Operacja jest użyta w zleceniach demo. Ustaw ją jako nieaktywną.'
    elif slug == 'materialy':
        if PkjCheckPoint.query.filter_by(material_id=item.id).count():
            return 'Materiał ma przypisane punkty kontrolne. Usuń je lub zmień ich zakres.'
        if PkjOrderMaterial.query.filter_by(material_id=item.id).count():
            return 'Materiał jest użyty w zleceniach demo. Ustaw go jako nieaktywny.'
    elif slug == 'przyczyny':
        if PkjReturn.query.filter_by(reason_id=item.id).count():
            return 'Przyczyna jest użyta w zawrotach. Ustaw ją jako nieaktywną.'
    elif slug == 'pracownicy':
        if (PkjOrderOperation.query.filter_by(employee_id=item.id).count()
                or PkjInspection.query.filter_by(performer_id=item.id).count()):
            return 'Pracownik występuje w meldunkach lub kontrolach. Ustaw go jako nieaktywnego.'
    return None


@prestiz_kj_bp.route('/konfiguracja/<slug>/<int:item_id>/usun', methods=['POST'])
@login_required
@admin_required
def config_delete(slug, item_id):
    model = {'operacje': PkjOperation, 'materialy': PkjMaterial, 'punkty': PkjCheckPoint,
             'przyczyny': PkjReason, 'pracownicy': PkjEmployee}.get(slug)
    if model is None:
        abort(404)
    item = db.session.get(model, item_id)
    if item is None:
        abort(404)
    block = _usage_block(slug, item)
    if block:
        flash(block, 'warning')
        return _back(slug)
    if slug == 'punkty':   # historia wyników zostaje (ma kopię treści punktu)
        PkjResult.query.filter_by(point_id=item.id).update({'point_id': None})
        remove_upload(item.image)
    db.session.delete(item)
    db.session.commit()
    audit('pkj_config_delete', item_id, slug)
    flash('Usunięto wpis.', 'success')
    return _back(slug)


@prestiz_kj_bp.route('/konfiguracja/grupy/<int:group_id>/usun', methods=['POST'])
@login_required
@admin_required
def config_group_delete(group_id):
    group = db.session.get(PkjMaterialGroup, group_id) or abort(404)
    if (PkjMaterial.query.filter_by(group_id=group_id).count()
            or PkjCheckPoint.query.filter_by(group_id=group_id).count()):
        flash('Grupa ma przypisane materiały lub punkty. Odepnij je, zanim ją usuniesz.', 'warning')
    else:
        db.session.delete(group)
        db.session.commit()
        audit('pkj_config_delete', group_id, 'grupa')
        flash('Usunięto grupę.', 'success')
    return _back('materialy')
