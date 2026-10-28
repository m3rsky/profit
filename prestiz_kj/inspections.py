"""Kontrole KJ: start (sekcja Wspólne + sekcja każdego materiału z kopią treści punktów), zapis oceny, zakończenie
(werdykt per materiał, wydanie RW dla zgodnych) i przerwanie. Zawroty dla materiałów NOK dochodzą w etapie 4."""
from datetime import datetime, timezone

from sqlalchemy import func

from models import db
from .models import (PkjCheckPoint, PkjEmployee, PkjInspection, PkjInspectionMaterial, PkjMaterial,
                     PkjOrder, PkjOrderMaterial, PkjOrderOperation, PkjPhoto, PkjResult,
                     SCOPE_ALL, SCOPE_OPERATION, SCOPE_MATERIAL, SCOPE_GROUP)
from .orders import add_event, refresh_status
from . import repairs
from . import clock

UTC = timezone.utc
COMMON_SECTION = 'Wspólne'
RESULTS = ('OK', 'NOK', 'ND')


def _now():
    return clock.now()


def _copy_point(inspection, imat, section, point, position):
    return PkjResult(inspection_id=inspection.id, inspection_material_id=imat.id if imat else None,
                     point_id=point.id, position=position, section=section, title=point.title, kind=point.kind,
                     unit=point.unit, min_value=point.min_value, max_value=point.max_value,
                     photo_required_on_nok=point.photo_required_on_nok)


def pending_materials(oop):
    """Materiały operacji czekające na kontrolę (RW wstrzymane, jeszcze nie w kontroli)."""
    return (PkjOrderMaterial.query.filter_by(order_operation_id=oop.id, rw_status='wstrzymane',
                                             element_status='oczekuje')
            .order_by(PkjOrderMaterial.id).all())


def recheck_materials(oop):
    """Materiały po naprawie, czekające na kontrolę sprawdzającą."""
    return (PkjOrderMaterial.query.filter_by(order_operation_id=oop.id, element_status='do_ponownej_kontroli')
            .order_by(PkjOrderMaterial.id).all())


def _start_recheck(oop, mats, user):
    """Kontrola sprawdzająca: tylko punkty NOK z poprzedniej kontroli (wspólne i własne punkty materiału),
    kopiowane z poprzednich wyników, więc nadal obowiązuje treść z chwili pierwszej kontroli."""
    previous = {}
    for m in mats:
        pim = (PkjInspectionMaterial.query.filter_by(order_material_id=m.id, verdict='NOK')
               .order_by(PkjInspectionMaterial.id.desc()).first())
        if pim is None:
            raise ValueError(f'Brak poprzedniej kontroli materiału {m.material_name}.')
        previous[m.id] = pim
    prev_ids = {p.inspection_id for p in previous.values()}
    prev_insp = PkjInspection.query.filter(PkjInspection.id.in_(prev_ids)).all()
    common, seen = [], set()
    for r in (PkjResult.query.filter(PkjResult.inspection_id.in_(prev_ids), PkjResult.result == 'NOK',
                                     PkjResult.inspection_material_id.is_(None))
              .order_by(PkjResult.inspection_id, PkjResult.position).all()):
        if r.title not in seen:
            seen.add(r.title)
            common.append(r)
    per_material = {}
    for m in mats:
        pim = previous[m.id]
        rows = (PkjResult.query.filter_by(inspection_id=pim.inspection_id, inspection_material_id=pim.id)
                .order_by(PkjResult.position).all())
        failed = [r for r in rows if r.result == 'NOK']
        per_material[m.id] = failed or (rows if pim.interrupted else [])
    if not common and not any(per_material.values()):
        raise ValueError('Brak punktów do ponownej kontroli.')
    performer = db.session.get(PkjEmployee, oop.employee_id) if oop.employee_id else None
    insp = PkjInspection(order_id=oop.order_id, order_operation_id=oop.id, inspector_id=user.id,
                         inspector_name=user.username, performer_id=performer.id if performer else None,
                         performer_name=performer.name if performer else None,
                         round=max(i.round for i in prev_insp) + 1, previous_id=max(prev_ids))
    db.session.add(insp)
    db.session.flush()

    def copy(src, imat, section, pos):
        return PkjResult(inspection_id=insp.id, inspection_material_id=imat.id if imat else None,
                         point_id=src.point_id, position=pos, section=section, title=src.title, kind=src.kind,
                         unit=src.unit, min_value=src.min_value, max_value=src.max_value,
                         photo_required_on_nok=src.photo_required_on_nok)
    pos = 0
    for src in common:
        pos += 1
        db.session.add(copy(src, None, COMMON_SECTION, pos))
    for m in mats:
        imat = PkjInspectionMaterial(inspection_id=insp.id, order_material_id=m.id, name=m.material_name)
        db.session.add(imat)
        db.session.flush()
        for src in per_material[m.id]:
            pos += 1
            db.session.add(copy(src, imat, m.material_name, pos))
        m.element_status = 'w_kontroli'
    add_event(oop.order_id, 'kontrola_start',
              f'Rozpoczęto kontrolę sprawdzającą operacji {oop.operation_name}: '
              + ', '.join(m.material_name for m in mats), actor=user.username, ref_type='inspection', ref_id=insp.id)
    return insp


def start_inspection(oop, material_ids, user):
    """Zakłada kontrolę wybranych materiałów operacji (pierwszą albo sprawdzającą po naprawie).
    Rzuca ValueError z komunikatem dla kontrolera."""
    if oop.status != 'do_kontroli':
        raise ValueError('Ta operacja nie czeka na kontrolę KJ.')
    wanted = {int(i) for i in material_ids}
    again = {m.id: m for m in recheck_materials(oop)}
    if wanted & set(again):
        if not wanted <= set(again):
            raise ValueError('Nie łącz kontroli sprawdzającej z pierwszą kontrolą. Wybierz jedną grupę materiałów.')
        return _start_recheck(oop, [again[i] for i in sorted(wanted)], user)
    mats = [m for m in pending_materials(oop) if m.id in wanted]
    if not mats or len(mats) != len(wanted):
        raise ValueError('Zaznacz co najmniej jeden materiał czekający na kontrolę.')
    common = (PkjCheckPoint.query.filter(PkjCheckPoint.is_active.is_(True))
              .filter((PkjCheckPoint.scope == SCOPE_ALL) |
                      ((PkjCheckPoint.scope == SCOPE_OPERATION) & (PkjCheckPoint.operation_id == oop.operation_id)))
              .order_by(PkjCheckPoint.order, PkjCheckPoint.id).all())
    per_material = {}
    for m in mats:
        base = db.session.get(PkjMaterial, m.material_id) if m.material_id else None
        cond = (PkjCheckPoint.scope == SCOPE_MATERIAL) & (PkjCheckPoint.material_id == m.material_id)
        if base is not None and base.group_id:
            cond = cond | ((PkjCheckPoint.scope == SCOPE_GROUP) & (PkjCheckPoint.group_id == base.group_id))
        per_material[m.id] = (PkjCheckPoint.query.filter(PkjCheckPoint.is_active.is_(True)).filter(cond)
                              .order_by(PkjCheckPoint.order, PkjCheckPoint.id).all())
    if not common and not any(per_material.values()):
        raise ValueError('Brak punktów kontrolnych dla tej operacji i materiałów. Uzupełnij konfigurację.')
    performer = db.session.get(PkjEmployee, oop.employee_id) if oop.employee_id else None
    insp = PkjInspection(order_id=oop.order_id, order_operation_id=oop.id, inspector_id=user.id,
                         inspector_name=user.username, performer_id=performer.id if performer else None,
                         performer_name=performer.name if performer else None, round=1)
    db.session.add(insp)
    db.session.flush()
    pos = 0
    for point in common:
        pos += 1
        db.session.add(_copy_point(insp, None, COMMON_SECTION, point, pos))
    for m in mats:
        imat = PkjInspectionMaterial(inspection_id=insp.id, order_material_id=m.id, name=m.material_name)
        db.session.add(imat)
        db.session.flush()
        for point in per_material[m.id]:
            pos += 1
            db.session.add(_copy_point(insp, imat, m.material_name, point, pos))
        m.element_status = 'w_kontroli'
    add_event(oop.order_id, 'kontrola_start',
              f'Rozpoczęto kontrolę KJ operacji {oop.operation_name}: ' + ', '.join(m.material_name for m in mats),
              actor=user.username, ref_type='inspection', ref_id=insp.id)
    refresh_status(db.session.get(PkjOrder, oop.order_id))
    return insp


def in_progress_for(oop):
    return (PkjInspection.query.filter_by(order_operation_id=oop.id, status='w_toku')
            .order_by(PkjInspection.id).all())


def is_out_of_range(result):
    v = result.value
    if result.kind != 'value' or v is None:
        return False
    return (result.min_value is not None and v < result.min_value) or \
           (result.max_value is not None and v > result.max_value)


def record_result(result, outcome=None, value=None, notes=None, value_given=False):
    """Zapis oceny punktu. Wartość poza zakresem nie może być OK. Rzuca ValueError dla kontroli zamkniętej."""
    insp = db.session.get(PkjInspection, result.inspection_id)
    if insp.status != 'w_toku':
        raise ValueError('Kontrola jest zakończona.')
    if outcome is not None:
        if outcome not in RESULTS and outcome != '':
            raise ValueError('Nieprawidłowa ocena.')
        result.result = outcome or None
    if value_given:
        result.value = value
        result.out_of_range = is_out_of_range(result)
        if result.value is not None and result.result in (None, 'OK', 'NOK'):
            result.result = 'NOK' if result.out_of_range else 'OK'
    result.out_of_range = is_out_of_range(result)
    if result.out_of_range and result.result == 'OK':
        result.result = 'NOK'
    if notes is not None:
        result.notes = notes.strip()[:2000] or None
    return result


def mark_rest_ok(insp):
    """Ustawia OK we wszystkich nieocenionych punktach (B8, minimum kroków). Punkt z wartością dostaje środek zakresu,
    a gdy zakres ma tylko jedną granicę, tę granicę. Zwraca liczbę ocenionych punktów."""
    if insp.status != 'w_toku':
        raise ValueError('Kontrola jest zakończona.')
    count = 0
    for r in PkjResult.query.filter_by(inspection_id=insp.id).filter(PkjResult.result.is_(None)).all():
        if r.kind == 'value':
            bounds = [b for b in (r.min_value, r.max_value) if b is not None]
            if bounds:
                record_result(r, value=round(sum(bounds) / len(bounds), 2), value_given=True)
                count += 1
                continue
        record_result(r, 'OK')
        count += 1
    return count


def progress(insp_id):
    total = PkjResult.query.filter_by(inspection_id=insp_id).count()
    done = PkjResult.query.filter(PkjResult.inspection_id == insp_id, PkjResult.result.isnot(None)).count()
    return done, total


def photo_counts(insp_id):
    rows = (db.session.query(PkjPhoto.result_id, func.count(PkjPhoto.id)).join(PkjResult)
            .filter(PkjResult.inspection_id == insp_id).group_by(PkjPhoto.result_id).all())
    return dict(rows)


def finish_inspection(insp, actor):
    """Kończy kontrolę: zgodne materiały dostają RW, materiały NOK zostają zawrócone (wstrzymane RW)."""
    if insp.status != 'w_toku':
        raise ValueError('Kontrola jest już zakończona.')
    results = PkjResult.query.filter_by(inspection_id=insp.id).all()
    missing = [r for r in results if r.result is None]
    if missing:
        raise ValueError(f'Oceń wszystkie punkty. Pozostało: {len(missing)}.')
    photos = photo_counts(insp.id)
    lacking = [r for r in results if r.result == 'NOK' and r.photo_required_on_nok and not photos.get(r.id)]
    if lacking:
        raise ValueError('Dodaj zdjęcie przy punkcie NOK: ' + '; '.join(r.title for r in lacking[:3]))
    common_nok = any(r.result == 'NOK' for r in results if r.inspection_material_id is None)
    ok_names, nok_names = [], []
    for imat in PkjInspectionMaterial.query.filter_by(inspection_id=insp.id).all():
        nok = common_nok or any(r.result == 'NOK' for r in results if r.inspection_material_id == imat.id)
        imat.verdict = 'NOK' if nok else 'OK'
        om = db.session.get(PkjOrderMaterial, imat.order_material_id)
        repairs.close_after_recheck(om, not nok, actor)
        if nok:
            om.element_status = 'zawrocony'
            nok_names.append(imat.name)
        else:
            om.element_status, om.rw_status = 'zgodny', 'wydane'
            ok_names.append(imat.name)
    insp.status, insp.finished_at = 'zakonczona', _now()
    insp.result = 'NOK' if nok_names else 'OK'
    oop = db.session.get(PkjOrderOperation, insp.order_operation_id)
    add_event(insp.order_id, 'kontrola',
              f'Kontrola KJ operacji {oop.operation_name} zakończona: ' + insp.result
              + (f'. Zgodne: {", ".join(ok_names)}' if ok_names else '')
              + (f'. NOK: {", ".join(nok_names)}' if nok_names else ''),
              actor=actor, ref_type='inspection', ref_id=insp.id)
    if ok_names:
        add_event(insp.order_id, 'rw_wydane', 'RW na materiały wydane (symulacja): ' + ', '.join(ok_names),
                  actor=actor, ref_type='inspection', ref_id=insp.id)
    if nok_names:
        add_event(insp.order_id, 'wymagany_zawrot', 'Wymagany zawrót: ' + ', '.join(nok_names),
                  actor=actor, ref_type='inspection', ref_id=insp.id)
    _close_operation_if_done(oop, actor)
    return {'ok': ok_names, 'nok': nok_names}


def interrupt_inspection(insp, actor):
    """Przerwanie kontroli: błąd wyklucza element (B7). Materiały trafiają do zawrotu, RW zostaje wstrzymane."""
    if insp.status != 'w_toku':
        raise ValueError('Kontrola jest już zakończona.')
    names = []
    for imat in PkjInspectionMaterial.query.filter_by(inspection_id=insp.id).all():
        imat.verdict, imat.interrupted = 'NOK', True
        om = db.session.get(PkjOrderMaterial, imat.order_material_id)
        repairs.close_after_recheck(om, False, actor)
        om.element_status = 'zawrocony'
        names.append(imat.name)
    insp.status, insp.result, insp.finished_at = 'przerwana', 'NOK', _now()
    add_event(insp.order_id, 'kontrola_przerwana',
              'Kontrola przerwana, błąd wyklucza element: ' + ', '.join(names), actor=actor,
              ref_type='inspection', ref_id=insp.id)
    add_event(insp.order_id, 'wymagany_zawrot', 'Wymagany zawrót: ' + ', '.join(names), actor=actor,
              ref_type='inspection', ref_id=insp.id)
    refresh_status(db.session.get(PkjOrder, insp.order_id))


def _close_operation_if_done(oop, actor):
    mats = PkjOrderMaterial.query.filter_by(order_operation_id=oop.id).all()
    if mats and all(m.element_status == 'zgodny' for m in mats):
        oop.status = 'zakonczona'
        add_event(oop.order_id, 'operacja_zakonczona', f'Operacja {oop.operation_name} zakończona po kontroli KJ',
                  actor=actor, ref_type='operation', ref_id=oop.id)
    refresh_status(db.session.get(PkjOrder, oop.order_id))


def set_performer(insp, employee):
    if insp.status != 'w_toku':
        raise ValueError('Kontrola jest zakończona.')
    if employee is None or not employee.is_active:
        raise ValueError('Wybierz aktywnego pracownika.')
    insp.performer_id, insp.performer_name = employee.id, employee.name


def sections(insp_id):
    """Wyniki kontroli w sekcjach: najpierw Wspólne, potem materiały w kolejności kontroli."""
    results = (PkjResult.query.filter_by(inspection_id=insp_id)
               .order_by(PkjResult.position, PkjResult.id).all())
    out, index = [], {}
    for r in results:
        key = r.section or COMMON_SECTION
        if key not in index:
            index[key] = {'name': key, 'common': r.inspection_material_id is None, 'results': []}
            out.append(index[key])
        index[key]['results'].append(r)
    out.sort(key=lambda s: not s['common'])
    return out
