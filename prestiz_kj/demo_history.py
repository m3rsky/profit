"""Dane historyczne do raportów KJ Prestiż: 11 zleceń (ZP-DEMO-101 do 111) z ostatnich 4 tygodni z prawdziwymi
kontrolami, zawrotami, złomami, kontrolami sprawdzającymi i zdjęciami przykładowymi.

Generator jest deterministyczny (random.Random(2026)) i korzysta z tych samych funkcji co ekrany, a zegar modułu
(`clock`) ustawia daty wstecz. Rozkład: ok. 4 na 5 materiałów zgodnych za pierwszym razem, reszta zawroty z poprawką,
2 złomy, 2 przerwane kontrole, 2 powtórne niezgodności, a najczęstsza przyczyna to "Spoina niepełna"."""
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from models import db
from . import clock, inspections as insp_, orders as ord_, repairs
from .models import (PkjEmployee, PkjMaterial, PkjOperation, PkjOrder, PkjPhoto, PkjReason, PkjResult)
from .service import get_setting, set_setting, upload_folder

UTC = timezone.utc
LOCAL_TZ = ZoneInfo('Europe/Warsaw')       # jak w filtrze localdt aplikacji
PREFIX = 'ZP-DEMO-1'
FONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fonts', 'DejaVuSans.ttf')

PRODUCTS = ['Szafa PSH 600x800x300', 'Szafa PSH 800x1000x300 IP65', 'Szafa PSH INOX 800x800x300',
            'Szafa PSH 600x600x250', 'Szafa PSH 1000x1200x400']
CLIENTS = ['Klient Alfa', 'Klient Beta', 'Klient Gamma', 'Klient Delta', 'Klient Epsilon']
INSPECTORS = ['Anna Kontroler', 'Piotr Kontroler']

# (operacja, materiał, punkt, przyczyna, waga) znane niezgodności z domyślnej konfiguracji
CATALOG = [
    ('Spawanie', 'Drzwi', 'Spoina narożna kompletna', 'Spoina niepełna', 4),
    ('Spawanie', 'Korpus', 'Brak odkształceń boków', 'Odkształcenie', 3),
    ('Gięcie', 'Korpus', 'Różnica przekątnych korpusu', 'Wymiar poza tolerancją', 4),
    ('Gięcie', 'Płyta montażowa', 'Rozmieszczenie otworów zgodne z rysunkiem', 'Brak otworu', 2),
    ('Malowanie', 'Korpus', 'Zgodność kolorów korpusu i drzwi', 'Rysa na powłoce', 3),
    ('Malowanie', 'Drzwi', 'Dopasowanie drzwi do korpusu', 'Zanieczyszczenie przed malowaniem', 2),
    ('Malowanie', 'Płyta montażowa', 'Powłoka ocynku bez uszkodzeń', 'Rysa na powłoce', 1),
    ('Składanie', 'Drzwi', 'Luz drzwi względem korpusu', 'Wymiar poza tolerancją', 3),
]
DESCRIPTIONS = {
    'Spoina niepełna': 'Dospawać narożnik i przeszlifować spoinę',
    'Odkształcenie': 'Wyprostować bok korpusu',
    'Wymiar poza tolerancją': 'Skorygować wymiar zgodnie z rysunkiem',
    'Brak otworu': 'Wykonać brakujący otwór wg rysunku',
    'Rysa na powłoce': 'Zaszpachlować i poprawić powłokę',
    'Zanieczyszczenie przed malowaniem': 'Oczyścić element i pomalować ponownie',
}
NOTES = {
    'Spoina niepełna': 'Spoina niepełna przy narożniku',
    'Odkształcenie': 'Widoczne odkształcenie boku',
    'Wymiar poza tolerancją': 'Wymiar poza tolerancją',
    'Brak otworu': 'Brak otworu w płycie',
    'Rysa na powłoce': 'Rysa na powłoce',
    'Zanieczyszczenie przed malowaniem': 'Zanieczyszczenie pod powłoką',
}
# (dni temu start, tryb). Tryby: done, albo (operacja, faza) zatrzymania zlecenia w trakcie
ORDER_PLAN = [(27, 'done'), (25, 'done'), (22, 'done'), (20, 'done'), (18, 'done'), (15, 'done'), (13, 'done'),
              (11, 'done'), (7, ('Składanie', 'reported')), (5, ('Spawanie', 'in_repair')),
              (3, ('Gięcie', 'to_recheck'))]
FAIL_COUNT = 22


def _normalise(t, now):
    """Czas w godzinach pracy (6:00 do 17:00 czasu lokalnego, dni robocze), nigdy później niż chwilę temu."""
    local = t.astimezone(LOCAL_TZ)
    while True:
        if local.weekday() >= 5:
            local = (local + timedelta(days=1)).replace(hour=6, minute=15)
        elif local.hour >= 17:
            local = (local + timedelta(days=1)).replace(hour=6, minute=20)
        elif local.hour < 6:
            local = local.replace(hour=6, minute=10)
        else:
            break
    return min(local.astimezone(UTC), now - timedelta(minutes=3))


class _Builder:
    def __init__(self, now):
        self.now = now
        self.rng = random.Random(2026)
        self.reasons = {r.name: r for r in PkjReason.query.all()}
        self.employees = {}
        for e in PkjEmployee.query.filter_by(is_active=True).order_by(PkjEmployee.id).all():
            self.employees.setdefault(e.department, []).append(e)
        self.any_employee = PkjEmployee.query.filter_by(is_active=True).order_by(PkjEmployee.id).first()
        self.plan = {}
        self.k = 0
        self.t = now

    # czas
    def advance(self, lo, hi):
        self.t = _normalise(self.t + timedelta(minutes=self.rng.randint(lo, hi)), self.now)
        clock.set_time(self.t)
        return self.t

    def employee(self, op_name):
        pool = self.employees.get(op_name) or [self.any_employee]
        return self.rng.choice(pool)

    def inspector(self):
        return SimpleNamespace(id=None, username=self.rng.choice(INSPECTORS))

    # zdjęcia przykładowe
    def photo(self, result, caption):
        img = Image.new('RGB', (360, 240), (58, 64, 74))
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype(FONT, 17)
        draw.rectangle([14, 14, 346, 226], outline=(220, 70, 60), width=3)
        draw.text((28, 28), 'PRZYKŁAD WADY (demo)', fill=(240, 240, 240), font=font)
        draw.text((28, 62), caption[:34], fill=(250, 190, 60), font=font)
        draw.text((28, 92), result.title[:34], fill=(200, 200, 200), font=font)
        name = f'pkj_{uuid.uuid4().hex}.jpg'
        img.save(os.path.join(upload_folder(), name), 'JPEG', quality=70)
        db.session.add(PkjPhoto(result_id=result.id, filename=name))

    # kontrola
    def inspect(self, oop, mats, fails, interrupt=False):
        self.advance(5, 25)
        insp = insp_.start_inspection(oop, [m.id for m in mats], self.inspector())
        results = PkjResult.query.filter_by(inspection_id=insp.id).order_by(PkjResult.position).all()
        limit = len(results) if not interrupt else max(2, len(results) // 2)
        for r in results[:limit]:
            spec = fails.get(r.section) if r.inspection_material_id else None
            if spec and r.title == spec['point']:
                if r.kind == 'value':
                    span = (r.max_value or 0) - (r.min_value or 0)
                    bad = (r.max_value + max(0.5, span * 0.25)) if self.rng.random() < 0.5 \
                        else (r.min_value - max(0.5, span * 0.25))
                    insp_.record_result(r, value=round(bad, 1), value_given=True, notes=NOTES[spec['reason']])
                else:
                    insp_.record_result(r, 'NOK', notes=NOTES[spec['reason']])
                db.session.flush()
                if r.photo_required_on_nok or self.rng.random() < 0.5:
                    self.photo(r, spec['reason'])
            elif r.kind == 'value':
                span = (r.max_value - r.min_value)
                insp_.record_result(r, value=round(self.rng.uniform(r.min_value + 0.1 * span,
                                                                    r.max_value - 0.1 * span), 1), value_given=True)
            else:
                insp_.record_result(r, 'ND' if (r.section == 'Wspólne' and self.rng.random() < 0.03) else 'OK')
        db.session.flush()
        self.advance(4, 15)
        if interrupt:
            insp_.interrupt_inspection(insp, 'Dane demo')
        else:
            insp_.finish_inspection(insp, 'Dane demo')
        return insp

    def recheck(self, oop, om, fail_again, reason_name):
        self.advance(60, 240)
        insp = insp_.start_inspection(oop, [om.id], self.inspector())
        for r in PkjResult.query.filter_by(inspection_id=insp.id).all():
            if fail_again:
                insp_.record_result(r, 'NOK', notes=NOTES[reason_name])
                db.session.flush()
                self.photo(r, reason_name)
            elif r.kind == 'value':
                insp_.record_result(r, value=round((r.min_value + r.max_value) / 2, 1), value_given=True)
            else:
                insp_.record_result(r, 'OK')
        db.session.flush()
        self.advance(3, 10)
        insp_.finish_inspection(insp, 'Dane demo')

    def report_repair(self, zn, only_first=False):
        for i, zop in enumerate(ord_.operations_of(zn.id)):
            self.advance(90, 420)
            ord_.report_operation(zop, self.employee(zop.operation_name), actor='Dane demo', when=self.t)
            if only_first:
                break

    def handle_return(self, order, oop, om, spec, stop):
        """Zawrót, naprawa i kontrola sprawdzająca (z ewentualnym drugim cyklem). None, gdy zlecenie ma tu stanąć."""
        szl = PkjOperation.query.filter_by(name='Szlifowanie').first()
        for attempt in (1, 2):
            self.advance(15, 90)
            decision = spec['decision'] if attempt == 1 else 'poprawka'
            in_repair = stop == (oop.operation_name, 'in_repair')
            extra = [szl.id] if (decision == 'poprawka' and oop.operation_name == 'Spawanie' and szl
                                 and (in_repair or self.rng.random() < 0.7)) else []
            reason = self.reasons[spec['reason']]
            ret = repairs.create_return(om, reason.id, DESCRIPTIONS[spec['reason']], decision, oop.id, extra,
                                        actor=self.inspector().username)
            zn = db.session.get(PkjOrder, ret.repair_order_id)
            if stop == (oop.operation_name, 'in_repair'):
                self.report_repair(zn, only_first=True)
                return False
            self.report_repair(zn)
            if stop == (oop.operation_name, 'to_recheck'):
                return False
            fail_again = attempt == 1 and spec['second_nok']
            self.recheck(oop, om, fail_again, spec['reason'])
            if not fail_again:
                return True
        return True

    def control_cycle(self, order, oop, stop):
        mats = insp_.pending_materials(oop)
        fails = {m.material_name: self.plan[(self.k, oop.operation_name, m.material_name)] for m in mats
                 if (self.k, oop.operation_name, m.material_name) in self.plan}
        broken = [m for m in mats if fails.get(m.material_name, {}).get('interrupted')]
        rest = [m for m in mats if m not in broken]
        for m in broken:
            self.inspect(oop, [m], fails, interrupt=True)
        if rest:
            self.inspect(oop, rest, fails)
        for om in repairs.needs_return(order.id):
            spec = fails.get(om.material_name)
            if spec is None:
                continue
            if not self.handle_return(order, oop, om, spec, stop):
                return False
        return True

    def run_order(self, index, days_ago, mode):
        self.k = index
        start = _normalise((self.now - timedelta(days=days_ago)).astimezone(LOCAL_TZ).replace(hour=6, minute=30), self.now)
        self.t = start
        clock.set_time(start)
        number = f'{PREFIX}{index + 1:02d}'
        order = ord_.create_order(number, PRODUCTS[index % len(PRODUCTS)], CLIENTS[index % len(CLIENTS)],
                                  1 + index % 3, actor='Dane demo', when=start)
        db.session.flush()
        stop = None if mode == 'done' else mode
        for oop in ord_.operations_of(order.id):
            self.advance(120, 300)
            ord_.report_operation(oop, self.employee(oop.operation_name), actor='Dane demo', when=self.t)
            if not oop.kj_required:
                continue
            if stop == (oop.operation_name, 'reported'):
                return order
            self.advance(40, 180)
            if not self.control_cycle(order, oop, stop):
                return order
        return order


def _plan_failures(builder, order_ids):
    """Wybiera niezgodności: wagi wg katalogu, wymuszone przypadki dla zleceń w trakcie, 2 złomy, 2 przerwane
    kontrole i 2 powtórne niezgodności (poza zleceniami w trakcie)."""
    rng = builder.rng
    by_key = {(op, mat): (point, reason, w) for op, mat, point, reason, w in CATALOG}
    forced = {(order_ids[9], 'Spawanie', 'Drzwi'), (order_ids[10], 'Gięcie', 'Korpus')}
    slots = [(oid, op, mat) for oid in order_ids[:9] for (op, mat) in by_key]
    # zlecenie nr 9 (indeks 8) zatrzymuje się przed kontrolą Składania, więc nie ma tam slotu na tę operację
    slots = [s for s in slots if not (s[0] == order_ids[8] and s[1] == 'Składanie')]
    # "Spoina niepełna" ma być wyraźnym liderem Pareto: 7 z 9 spoin drzwi zawsze niezgodnych
    must = rng.sample([s for s in slots if (s[1], s[2]) == ('Spawanie', 'Drzwi')], 7)
    chosen = list(forced) + must
    pool = [s for s in slots if s not in forced and s not in must]
    for _ in range(FAIL_COUNT - len(must)):
        weights = [by_key[(s[1], s[2])][2] for s in pool]
        pick = rng.choices(pool, weights=weights, k=1)[0]
        pool.remove(pick)
        chosen.append(pick)
    specs = {}
    for slot in chosen:
        point, reason, _ = by_key[(slot[1], slot[2])]
        specs[slot] = {'point': point, 'reason': reason, 'decision': 'poprawka', 'interrupted': False,
                       'second_nok': False}
    free = [s for s in chosen if s not in forced]
    scrap = [s for s in free if specs[s]['reason'] in ('Odkształcenie', 'Spoina niepełna')]
    for s in rng.sample(scrap, min(2, len(scrap))):
        specs[s]['decision'] = 'zlom'
    broken = [s for s in free if specs[s]['reason'] in ('Odkształcenie', 'Wymiar poza tolerancją')
              and specs[s]['decision'] != 'zlom']
    for s in rng.sample(broken, min(2, len(broken))):
        specs[s]['interrupted'] = True
    again = [s for s in free if specs[s]['decision'] == 'poprawka' and not specs[s]['interrupted']
             and specs[s]['reason'] != 'Odkształcenie']
    for s in rng.sample(again, min(2, len(again))):
        specs[s]['second_nok'] = True
    return specs


def seed_history(now=None):
    """Zakłada zlecenia historyczne. Zwraca False (bez zmian), gdy już istnieją albo brakuje konfiguracji."""
    if PkjOrder.query.filter(PkjOrder.number.like(PREFIX + '%')).first() is not None:
        return False
    if PkjOperation.query.first() is None or PkjMaterial.query.first() is None:
        return False
    now = now or datetime.now(UTC)
    builder = _Builder(now)
    saved_mode = get_setting('approval_mode')
    set_setting('approval_mode', 'auto')          # dane historyczne zawsze z automatycznym tworzeniem ZN
    try:
        builder.plan = _plan_failures(builder, list(range(len(ORDER_PLAN))))      # klucz: (indeks zlecenia, operacja, materiał)
        for k, (days_ago, mode) in enumerate(ORDER_PLAN):
            builder.run_order(k, days_ago, mode)
            db.session.flush()
        db.session.commit()
    finally:
        clock.clear()
        set_setting('approval_mode', saved_mode)
        db.session.commit()
    return True
