"""Scenariusz pokazu (5 do 7 minut) na zleceniu ZP-DEMO-001: kroki, podpowiedzi dla prezentującego i stan każdego
kroku wyliczany z danych (gotowe, teraz, czeka)."""
from flask import url_for

from models import db
from . import orders as ord_
from .models import (PkjCheckPoint, PkjInspection, PkjInspectionMaterial, PkjOperation, PkjOrder, PkjOrderMaterial,
                     PkjReturn)

STEPS = [
    ('zlecenie', 'Zlecenie i trasa',
     'ZP-DEMO-001, szafa PSH 600x800x300 dla klienta demo. Trasa: Wycinanie, Gięcie, Spawanie, Szlifowanie, '
     'Malowanie, Składanie. Materiały podlegające KJ: Korpus, Drzwi, Płyta montażowa.',
     ['Pokaż trasę i materiały KJ na stronie zlecenia.',
      'Wskaż, że Wycinanie i Gięcie są już za zleceniem, a Spawanie czeka na meldunek.']),
    ('konfiguracja', 'Konfiguracja K1 do K4',
     'Operacje z flagą KJ (K1), materiały z flagą KJ i grupą (K2, K4), punkty wspólne, operacji, materiału i grupy (K3).',
     ['Operacje: Gięcie, Spawanie, Malowanie i Składanie mają flagę KJ.',
      'Materiały: Korpus i Drzwi tworzą grupę Obudowa, Aparatura nie ma flagi, więc nie trafi do aplikacji.',
      'Punkty: pokaż punkt z wartością (min, max, jednostka) i wymóg zdjęcia przy NOK.']),
    ('meldunek', 'Meldunek operacji Spawanie, RW wstrzymane',
     'Meldunek operacji z flagą KJ nie wydaje RW na materiały. Do zakończenia kontroli RW jest wstrzymane.',
     ['Na stronie zlecenia kliknij "Melduj operację" przy Spawaniu (wykonawca ze Spawalni, np. Cezary Lis).',
      'Zwróć uwagę na status "RW wstrzymane" przy trzech materiałach i wpis na osi czasu.']),
    ('kontrola', 'Aplikacja KJ: kontrola ze zdjęciem',
     'Kontroler skanuje kod QR zlecenia, wybiera operację Spawanie i materiały Korpus oraz Drzwi, ocenia punkty.',
     ['Pokaż etykietę QR i zeskanuj ją telefonem albo wpisz numer w ramce telefonu.',
      'Odznacz Płytę montażową, zaznacz Korpus i Drzwi, rozpocznij kontrolę.',
      'Użyj "Pozostałe punkty: OK", potem przy Drzwiach oceń "Spoina narożna kompletna" jako NOK, dodaj zdjęcie i uwagę.',
      'Zakończ: Korpus dostaje RW, Drzwi zostają zawrócone.']),
    ('zawrot', 'Zawrót i zlecenie naprawcze',
     'Dla Drzwi: operacja docelowa Spawanie, ręcznie dodane Szlifowanie, przyczyna "Spoina niepełna", decyzja poprawka.',
     ['Na ekranie wyniku dotknij "Zgłoś zawrót: Drzwi".',
      'Wybierz Spawanie, dodaj Szlifowanie, przyczynę i opis, utwórz zlecenie naprawcze.',
      'Powstaje ZN-ZP-DEMO-001/1 powiązane ze zleceniem, materiałem i wynikiem kontroli.']),
    ('naprawa', 'Naprawa, kontrola sprawdzająca, RW zwolnione',
     'Produkcja melduje operacje ZN, potem element wraca do kontroli sprawdzającej. Wynik OK zamyka ZN i zwalnia RW.',
     ['Na stronie ZN zamelduj Spawanie i Szlifowanie.',
      'W aplikacji KJ rozpocznij kontrolę sprawdzającą Drzwi: widać tylko punkt, który był NOK.',
      'Po OK: element "zgodny", RW wydane, ZN zamknięte, operacja Spawanie zakończona.']),
    ('raporty', 'Raporty',
     'Karta kontroli w PDF, rejestr niezgodności, FPY, Pareto, koszt zawrotów, wyniki wg pracownika i czas kontroli.',
     ['Karta kontroli ZP-DEMO-001: wszystkie kontrole, wartości i zdjęcie.',
      'FPY i Pareto z danych 11 zleceń historycznych: lider to "Spoina niepełna".',
      'Każdy raport ma filtry i eksport do Excela.']),
]


def _scenario_order():
    return PkjOrder.query.filter_by(is_demo_scenario=True, type='ZP').order_by(PkjOrder.id).first()


def _links(key, order):
    oid = order.id if order else None
    out = {
        'zlecenie': [('prestiz_kj.order_page', {'order_id': oid}, 'Strona zlecenia', 'bi-list-ul')] if oid else [],
        'konfiguracja': [('prestiz_kj.config_page', {'slug': 'operacje'}, 'Operacje', 'bi-diagram-3'),
                         ('prestiz_kj.config_page', {'slug': 'materialy'}, 'Materiały', 'bi-boxes'),
                         ('prestiz_kj.config_page', {'slug': 'punkty'}, 'Punkty', 'bi-list-check')],
        'meldunek': [('prestiz_kj.order_page', {'order_id': oid}, 'Melduj na stronie zlecenia', 'bi-box-arrow-in-right')]
        if oid else [],
        'kontrola': ([('prestiz_kj.app_frame', {}, 'Aplikacja KJ w ramce', 'bi-phone')]
                     + ([('prestiz_kj.order_label', {'order_id': oid}, 'Etykieta QR (PDF)', 'bi-qr-code')]
                        if oid else [])),
        'zawrot': [('prestiz_kj.returns_list', {}, 'Rejestr zawrotów', 'bi-arrow-return-left')],
        'naprawa': [('prestiz_kj.returns_list', {}, 'Rejestr zawrotów', 'bi-arrow-return-left'),
                    ('prestiz_kj.app_frame', {}, 'Aplikacja KJ w ramce', 'bi-phone')],
        'raporty': ([('prestiz_kj.report_card', {'order_id': oid}, 'Karta kontroli ZP-DEMO-001', 'bi-file-earmark-text')]
                    if oid else []) + [
            ('prestiz_kj.report_page', {'slug': 'fpy'}, 'FPY', 'bi-bullseye'),
            ('prestiz_kj.report_page', {'slug': 'pareto'}, 'Pareto', 'bi-bar-chart-line'),
            ('prestiz_kj.report_page', {'slug': 'niezgodnosci'}, 'Rejestr niezgodności', 'bi-exclamation-octagon'),
            ('prestiz_kj.report_page', {'slug': 'koszt-zawrotow'}, 'Koszt zawrotów', 'bi-cash-coin'),
            ('prestiz_kj.reports_index', {}, 'Wszystkie raporty', 'bi-graph-up')],
    }[key]
    return [{'url': url_for(ep, **kw), 'label': label, 'icon': icon} for ep, kw, label, icon in out]


def scenario_state():
    """Kroki scenariusza z wyliczonym stanem: done (bool), detail (tekst), state: done | now | todo."""
    order = _scenario_order()
    configured = PkjOperation.query.first() is not None and PkjCheckPoint.query.first() is not None
    done, detail = {}, {}
    if order is not None:
        ops = {o.operation_name: o for o in ord_.operations_of(order.id)}
        spawanie = ops.get('Spawanie')
        mats = {m.material_name: m for m in PkjOrderMaterial.query.filter_by(order_id=order.id,
                                                                            order_operation_id=spawanie.id).all()} \
            if spawanie else {}
        drzwi = mats.get('Drzwi')
        inspections = (PkjInspection.query.filter_by(order_id=order.id, order_operation_id=spawanie.id)
                       .order_by(PkjInspection.id).all()) if spawanie else []
        first = next((i for i in inspections if i.round == 1 and i.status in ('zakonczona', 'przerwana')), None)
        ret = (PkjReturn.query.filter_by(order_material_id=drzwi.id).order_by(PkjReturn.id).first()
               if drzwi else None)
        zn = db.session.get(PkjOrder, ret.repair_order_id) if ret and ret.repair_order_id else None
        done['zlecenie'] = True
        done['konfiguracja'] = configured
        done['meldunek'] = bool(spawanie and spawanie.status != 'oczekuje')
        done['kontrola'] = first is not None
        done['zawrot'] = ret is not None
        done['naprawa'] = bool(drzwi and drzwi.element_status == 'zgodny' and ret is not None)
        done['raporty'] = done['naprawa']
        if spawanie and spawanie.employee_name:
            detail['meldunek'] = f'Zameldowano: {spawanie.employee_name}. RW: ' + (
                'wstrzymane' if any(m.rw_status == 'wstrzymane' for m in mats.values()) else 'wydane')
        if first is not None:
            names = ', '.join(f'{m.name}: {m.verdict}' for m in
                              PkjInspectionMaterial.query.filter_by(inspection_id=first.id).all())
            detail['kontrola'] = f'Wynik kontroli: {first.result}. {names}'
        if ret is not None:
            detail['zawrot'] = f'{zn.number if zn else "czeka na zatwierdzenie"}, decyzja: {ret.decision}'
        if drzwi is not None and ret is not None:
            detail['naprawa'] = f'Drzwi: {drzwi.element_status.replace("_", " ")}'
    else:
        done['konfiguracja'] = configured
    steps, current_set = [], False
    for key, title, text, tips in STEPS:
        is_done = bool(done.get(key))
        if is_done:
            state = 'done'
        elif not current_set:
            state, current_set = 'now', True
        else:
            state = 'todo'
        steps.append({'key': key, 'title': title, 'text': text, 'tips': tips, 'state': state,
                      'detail': detail.get(key), 'links': _links(key, order)})
    return {'order': order, 'steps': steps, 'all_done': all(s['state'] == 'done' for s in steps)}
