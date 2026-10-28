"""Aplikacja KJ (widok mobilny): skan lub wybór zlecenia, operacja i materiały, ocena punktów w sekcjach,
zdjęcia, zakończenie lub przerwanie kontroli, historia. Strony są samodzielne (bez menu profit), więc wyglądają
tak samo na telefonie i w ramce telefonu na komputerze."""
import os
import uuid

from flask import (render_template, redirect, url_for, request, flash, abort, jsonify, current_app, make_response)
from flask_login import login_required, current_user
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from models import db
from . import prestiz_kj_bp
from .access import view_required, controller_required, audit
from .models import (PkjCheckPoint, PkjEmployee, PkjInspection, PkjInspectionMaterial, PkjOrder,
                     PkjOrderMaterial, PkjOrderOperation, PkjPhoto, PkjResult)
from . import inspections as insp_
from . import repairs
from . import orders as ord_
from .pdf import order_label_pdf
from .service import upload_folder, remove_upload


def _get(model, ident):
    return db.session.get(model, ident) or abort(404)


def _json_error(message, status=400):
    return jsonify({'ok': False, 'error': message}), status


# ── Ramka telefonu na komputerze, etykieta QR ─────────────────────────────────

@prestiz_kj_bp.route('/aplikacja')
@login_required
@view_required
def app_frame():
    """Aplikacja KJ w ramce telefonu (iframe 390 px, więc zapytania @media widzą szerokość telefonu)."""
    return render_template('prestiz_kj/aplikacja.html')


@prestiz_kj_bp.route('/zlecenia/<int:order_id>/etykieta.pdf')
@login_required
@view_required
def order_label(order_id):
    order = _get(PkjOrder, order_id)
    resp = make_response(order_label_pdf(order))
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'inline; filename=etykieta-{secure_filename(order.number)}.pdf'
    return resp


# ── 1. Start: skan albo wybór zlecenia ────────────────────────────────────────

@prestiz_kj_bp.route('/m/')
@login_required
@controller_required
def m_start():
    q = request.args.get('q', '').strip()
    if q:
        exact = PkjOrder.query.filter(db.func.lower(PkjOrder.number) == q.lower()).first()
        if exact is not None:
            return redirect(url_for('prestiz_kj.m_order', order_id=exact.id))
    query = PkjOrder.query
    if q:
        like = f'%{q}%'
        query = query.filter(or_(PkjOrder.number.ilike(like), PkjOrder.product.ilike(like),
                                 PkjOrder.client.ilike(like)))
    else:
        query = query.filter(PkjOrder.status == 'do_kontroli')
    orders = query.order_by(PkjOrder.created_at.desc()).limit(30).all()
    if q and not orders:
        flash(f'Nie znaleziono zlecenia: {q}', 'warning')
    return render_template('prestiz_kj/m/start.html', q=q, orders=orders, summaries=ord_.summaries(orders))


# ── 2. Operacja i materiały ───────────────────────────────────────────────────

@prestiz_kj_bp.route('/m/zlecenie/<int:order_id>')
@login_required
@controller_required
def m_order(order_id):
    order = _get(PkjOrder, order_id)
    blocks = []
    for oop in ord_.operations_of(order.id):
        if oop.status != 'do_kontroli':
            continue
        mats = (PkjOrderMaterial.query.filter_by(order_operation_id=oop.id)
                .order_by(PkjOrderMaterial.id).all())
        open_ret = {m.id: repairs.open_return_for(m.id) for m in mats}
        zns = {r.id: db.session.get(PkjOrder, r.repair_order_id) for r in open_ret.values()
               if r is not None and r.repair_order_id}
        blocks.append({'op': oop, 'pending': [m for m in mats if m.element_status == 'oczekuje'],
                       'recheck': [m for m in mats if m.element_status == 'do_ponownej_kontroli'],
                       'to_return': [m for m in mats if m.element_status == 'zawrocony' and open_ret[m.id] is None],
                       'others': [m for m in mats if m.element_status not in
                                  ('oczekuje', 'do_ponownej_kontroli')
                                  and not (m.element_status == 'zawrocony' and open_ret[m.id] is None)],
                       'returns': open_ret, 'zns': zns, 'running': insp_.in_progress_for(oop)})
    last = (PkjInspection.query.filter_by(order_id=order.id).count())
    return render_template('prestiz_kj/m/zlecenie.html', order=order, blocks=blocks, inspections=last)


@prestiz_kj_bp.route('/m/kontrola/start', methods=['POST'])
@login_required
@controller_required
def m_start_inspection():
    oop = _get(PkjOrderOperation, request.form.get('oop_id', type=int) or 0)
    ids = [int(i) for i in request.form.getlist('material') if i.isdigit()]
    try:
        insp = insp_.start_inspection(oop, ids, current_user)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
        return redirect(url_for('prestiz_kj.m_order', order_id=oop.order_id))
    db.session.commit()
    audit('pkj_inspection_start', insp.id, f'{oop.operation_name}')
    return redirect(url_for('prestiz_kj.m_inspection', insp_id=insp.id))


# ── 3. Punkty ─────────────────────────────────────────────────────────────────

@prestiz_kj_bp.route('/m/kontrola/<int:insp_id>')
@login_required
@controller_required
def m_inspection(insp_id):
    insp = _get(PkjInspection, insp_id)
    order = _get(PkjOrder, insp.order_id)
    oop = db.session.get(PkjOrderOperation, insp.order_operation_id)
    secs = insp_.sections(insp.id)
    point_ids = {r.point_id for s in secs for r in s['results'] if r.point_id}
    images = {p.id: p.image for p in PkjCheckPoint.query.filter(PkjCheckPoint.id.in_(point_ids)).all()
              if p.image} if point_ids else {}
    results = [r for s in secs for r in s['results']]
    photos = {}
    for ph in PkjPhoto.query.filter(PkjPhoto.result_id.in_([r.id for r in results])).order_by(PkjPhoto.id).all() \
            if results else []:
        photos.setdefault(ph.result_id, []).append(ph)
    done, total = insp_.progress(insp.id)
    imats = PkjInspectionMaterial.query.filter_by(inspection_id=insp.id).all()
    return render_template('prestiz_kj/m/kontrola.html', insp=insp, order=order, oop=oop, sections=secs,
                           images=images, photos=photos, done=done, total=total, imats=imats,
                           employees=ord_.active_employees(),
                           needs_return=[m.name for m in imats if m.verdict == 'NOK'],
                           to_return=[m for m in repairs.needs_return(order.id)
                                      if m.id in {im.order_material_id for im in imats}])


@prestiz_kj_bp.route('/m/kontrola/<int:insp_id>/punkt/<int:result_id>', methods=['POST'])
@login_required
@controller_required
def m_save_result(insp_id, result_id):
    result = _get(PkjResult, result_id)
    if result.inspection_id != insp_id:
        abort(404)
    data = request.get_json(silent=True) or {}
    value_given = 'value' in data
    value = None
    if value_given and str(data.get('value', '')).strip() != '':
        try:
            value = float(str(data['value']).strip().replace(',', '.'))
        except ValueError:
            return _json_error('Wartość musi być liczbą.')
    try:
        insp_.record_result(result, data.get('result') if 'result' in data else None, value,
                            data.get('notes') if 'notes' in data else None, value_given)
    except ValueError as exc:
        db.session.rollback()
        return _json_error(str(exc))
    db.session.commit()
    done, total = insp_.progress(insp_id)
    return jsonify({'ok': True, 'result': result.result, 'out_of_range': bool(result.out_of_range),
                    'done': done, 'total': total})


@prestiz_kj_bp.route('/m/kontrola/<int:insp_id>/punkt/<int:result_id>/zdjecie', methods=['POST'])
@login_required
@controller_required
def m_upload_photo(insp_id, result_id):
    result = _get(PkjResult, result_id)
    insp = _get(PkjInspection, insp_id)
    if result.inspection_id != insp_id:
        abort(404)
    if insp.status != 'w_toku':
        return _json_error('Kontrola jest zakończona.')
    file = request.files.get('photo')
    ext = file.filename.rsplit('.', 1)[1].lower() if file and file.filename and '.' in file.filename else ''
    if not file or ext not in current_app.config['ALLOWED_EXTENSIONS']:
        return _json_error('Dozwolone są tylko zdjęcia (jpg, png, gif, webp).')
    try:
        from PIL import Image
        Image.open(file).verify()
        file.seek(0)
    except Exception:
        return _json_error('Plik nie jest prawidłowym obrazem.')
    name = f'pkj_{uuid.uuid4().hex}.{ext}'
    file.save(os.path.join(upload_folder(), name))
    photo = PkjPhoto(result_id=result.id, filename=name)
    db.session.add(photo)
    db.session.commit()
    return jsonify({'ok': True, 'photo_id': photo.id,
                    'url': url_for('prestiz_kj.serve_upload', filename=name),
                    'delete_url': url_for('prestiz_kj.m_delete_photo', photo_id=photo.id)})


@prestiz_kj_bp.route('/m/zdjecie/<int:photo_id>/usun', methods=['POST'])
@login_required
@controller_required
def m_delete_photo(photo_id):
    photo = _get(PkjPhoto, photo_id)
    result = _get(PkjResult, photo.result_id)
    if _get(PkjInspection, result.inspection_id).status != 'w_toku':
        return _json_error('Kontrola jest zakończona.')
    remove_upload(photo.filename)
    db.session.delete(photo)
    db.session.commit()
    return jsonify({'ok': True})


@prestiz_kj_bp.route('/m/kontrola/<int:insp_id>/reszta-ok', methods=['POST'])
@login_required
@controller_required
def m_rest_ok(insp_id):
    insp = _get(PkjInspection, insp_id)
    try:
        count = insp_.mark_rest_ok(insp)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    else:
        db.session.commit()
        flash(f'Oceniono jako OK punktów: {count}.' if count else 'Wszystkie punkty są już ocenione.', 'success')
    return redirect(url_for('prestiz_kj.m_inspection', insp_id=insp.id))


@prestiz_kj_bp.route('/m/kontrola/<int:insp_id>/wykonawca', methods=['POST'])
@login_required
@controller_required
def m_set_performer(insp_id):
    insp = _get(PkjInspection, insp_id)
    data = request.get_json(silent=True) or {}
    try:
        emp_id = int(data.get('employee_id'))
    except (TypeError, ValueError):
        return _json_error('Wybierz pracownika.')
    try:
        insp_.set_performer(insp, db.session.get(PkjEmployee, emp_id))
    except ValueError as exc:
        return _json_error(str(exc))
    db.session.commit()
    return jsonify({'ok': True})


# ── 4. Zakończenie, przerwanie ────────────────────────────────────────────────

@prestiz_kj_bp.route('/m/kontrola/<int:insp_id>/zakoncz', methods=['POST'])
@login_required
@controller_required
def m_finish(insp_id):
    insp = _get(PkjInspection, insp_id)
    try:
        insp_.finish_inspection(insp, current_user.username)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
        return redirect(url_for('prestiz_kj.m_inspection', insp_id=insp.id))
    db.session.commit()
    audit('pkj_inspection_finish', insp.id, insp.result)
    return redirect(url_for('prestiz_kj.m_inspection', insp_id=insp.id))


@prestiz_kj_bp.route('/m/kontrola/<int:insp_id>/przerwij', methods=['POST'])
@login_required
@controller_required
def m_interrupt(insp_id):
    insp = _get(PkjInspection, insp_id)
    try:
        insp_.interrupt_inspection(insp, current_user.username)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    else:
        db.session.commit()
        audit('pkj_inspection_interrupt', insp.id)
    return redirect(url_for('prestiz_kj.m_inspection', insp_id=insp.id))


# ── 5. Historia ───────────────────────────────────────────────────────────────

@prestiz_kj_bp.route('/m/historia/<int:order_id>')
@login_required
@controller_required
def m_history(order_id):
    order = _get(PkjOrder, order_id)
    items = []
    for insp in PkjInspection.query.filter_by(order_id=order.id).order_by(PkjInspection.id.desc()).all():
        items.append({'insp': insp, 'done': insp_.progress(insp.id),
                      'imats': PkjInspectionMaterial.query.filter_by(inspection_id=insp.id).all(),
                      'op': db.session.get(PkjOrderOperation, insp.order_operation_id)})
    return render_template('prestiz_kj/m/historia.html', order=order, items=items)
