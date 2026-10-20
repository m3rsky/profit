"""Dane testowe do ręcznego sprawdzenia etapu 5b (decyzje o ilości NG) w WERSJI DEWELOPERSKIEJ.

Uruchomienie z katalogu projektu:  python tools/seed_dev_5b.py --dev

Skrypt jest idempotentny. Zakłada w lokalnej bazie (instance/psh_qc.db):
- wózki D001 do D004 (zwykłe bufory bez etapu),
- pracowników testowych w działach etapów (nazwy zaczynają się od „DEV 5b”),
- zlecenie 5B-DEV-1 (q=4) z elementami Drzwi (trasa Wycinanie, Gięcie, Kompletacja) i Korpus (Wycinanie,
  Kompletacja); kontrola obowiązkowa jest na Wycinaniu i Gięciu (ustawienia etapów),
- dodatkowy punkt kontrolny „DEV Krawędzie” dla Wycinania, jeśli etap ma mniej niż 2 punkty.
Na dysku zapisuje kody do wydruku w D:/Dane/Desktop/KODY QR TESTOWE/5b/: QR zlecenia, etykiety wózków,
plakietki pracowników i opis scenariusza testu. Nie uruchamiaj na produkcji."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

if '--dev' not in sys.argv:
    sys.exit('Podaj --dev, aby potwierdzić, że to lokalna baza deweloperska.')

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app import app, init_db
from models import (db, Buffer, DepartmentEmployee, ProductionStage, StageControlPoint, User, WorkOrder,
                    WorkOrderComponent, WorkOrderComponentStage)
from ustawienia.pdfs import badges_pdf, buffer_labels_pdf, _fonts

OUT = r'D:/Dane/Desktop/KODY QR TESTOWE/5b'
ZO, PRODUCT, CLIENT, QTY = '5B-DEV-1', 'PSH-DEV-5B', 'WEG', 4
ROUTES = {'Drzwi': (2, 8, ['Wycinanie', 'Giecie', 'Kompletacja']),
          'Korpus': (1, 4, ['Wycinanie', 'Kompletacja'])}


PL = str.maketrans('ąćęłńóśźż', 'acelnoszz')


def find_stage(prefix):
    """Etap po początku nazwy, z pominięciem polskich liter (Gięcie, Składanie itd.)."""
    prefix = prefix.lower()[:3]
    for s in ProductionStage.query.order_by(ProductionStage.order).all():
        if s.name.lower().translate(PL).startswith(prefix):
            return s
    raise SystemExit(f'Brak etapu zaczynającego się od „{prefix}” w bazie. Uruchom aplikację raz, aby zasiać etapy.')


init_db()
os.makedirs(OUT, exist_ok=True)
with app.app_context():
    admin = User.query.filter_by(username='admin').first()

    # wózki
    carts = []
    for n in range(1, 5):
        code = f'D{n:03d}'
        b = Buffer.query.filter_by(code=code).first() or Buffer(code=code, name=f'Wózek testowy {n}')
        db.session.add(b)
        carts.append(b)

    # pracownicy po jednym na dział etapów wykorzystywanych w scenariuszu
    badges = []
    rows = []
    for prefix in ('Wycinanie', 'Giecie', 'Kompletacja', 'Skladanie', 'Kontrola', 'Pakowanie'):
        st = find_stage(prefix)
        if st.department_id is None:
            continue
        name = f'DEV 5b {st.name}'[:64]
        e = DepartmentEmployee.query.filter_by(name=name).first()
        if e is None:
            e = DepartmentEmployee(department_id=st.department_id, name=name)
            db.session.add(e)
        badges.append(e)
        rows.append((st.name, st.department.name, name))

    # punkty kontrolne Wycinania
    cut = find_stage('Wycinanie')
    if StageControlPoint.query.filter_by(stage_id=cut.id, is_active=True).count() < 2:
        db.session.add(StageControlPoint(stage_id=cut.id, order=99, title='DEV Krawędzie'))

    # zlecenie
    order = WorkOrder.query.filter_by(zo_number=ZO).first()
    if order is None:
        order = WorkOrder(zo_number=ZO, product_name=PRODUCT, client=CLIENT, quantity=QTY,
                          created_by_id=admin.id)
        db.session.add(order)
        db.session.flush()
        for i, (name, (per, req, route)) in enumerate(ROUTES.items(), 1):
            c = WorkOrderComponent(order_id=order.id, name=name, per_unit=per, required_qty=req, position=i)
            db.session.add(c)
            db.session.flush()
            for r in route:
                db.session.add(WorkOrderComponentStage(component_id=c.id, stage_id=find_stage(r).id))
    db.session.commit()
    carts = Buffer.query.filter(Buffer.code.in_([c.code for c in carts])).order_by(Buffer.code).all()

    with open(os.path.join(OUT, 'etykiety_wozkow_D001_D004.pdf'), 'wb') as fh:
        fh.write(buffer_labels_pdf(carts).read())
    with open(os.path.join(OUT, 'plakietki_5b.pdf'), 'wb') as fh:
        fh.write(badges_pdf(badges).read())

    # QR zlecenia (ten sam format co w module: p, c, o, q)
    payload = json.dumps({'p': PRODUCT, 'c': CLIENT, 'o': ZO, 'q': QTY}, ensure_ascii=False)
    font, bold = _fonts()
    c = canvas.Canvas(os.path.join(OUT, 'zlecenie_5B-DEV-1.pdf'), pagesize=A4)
    w, h = A4
    qr = QrCodeWidget(payload)
    b = qr.getBounds()
    size = 110 * mm
    d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    d.add(qr)
    renderPDF.draw(d, c, (w - size) / 2, h - 40 * mm - size)
    y = h - 40 * mm - size - 15 * mm
    c.setFont(bold, 22)
    c.drawCentredString(w / 2, y, 'ZLECENIE TESTOWE 5b')
    c.setFont(font, 16)
    for k, line in enumerate([f'ZO: {ZO}', f'Produkt: {PRODUCT}', f'Klient: {CLIENT}', f'Ilość: {QTY} szt.'], 1):
        c.drawCentredString(w / 2, y - k * 9 * mm, line)
    c.save()

    steps = f"""SCENARIUSZ TESTU ETAPU 5b (wersja deweloperska http://localhost:5000)
Zlecenie {ZO}: Drzwi (2 na sztukę, wymagane 8, trasa Wycinanie, Gięcie, Kompletacja), Korpus (wymagane 4,
trasa Wycinanie, Kompletacja). Wózki D001 do D004. Kontrola obowiązkowa po Wycinaniu i po Gięciu.
Pracownicy (plakietki w plakietki_5b.pdf):
""" + '\n'.join(f'  - {s}: dział {d_}, {n}' for s, d_, n in rows) + """

1. Stanowisko (/kontrolaprodukcji/stanowisko), konto oper: zeskanuj plakietkę „Wycinanie”, QR zlecenia i wózek D001.
   Wpisz Drzwi 6 szt. (czeka na Wycinanie).
2. Przejmij etap Wycinanie (6 szt.), odstaw na wózek D002 (czeka na Gięcie). Powstaje kontrola po Wycinaniu.
3. Zakładka Kontrole: oceń 6 szt. jako 3 zgodne i 3 niezgodne, w punkcie „Krawędzie” NG. Powstaje raport QAR,
   zgodne 3 szt. leżą na D002 jako „gotowe”, niezgodne 3 szt. jako „wstrzymane”.
4. Zakładka Wstrzymane (licznik 1):
   a) zwolnij 1 szt. z uwagą,
   b) do poprawki 1 szt. na etap Wycinanie, wózek D003, uwaga „dociąć zadziory”,
   c) zezłomuj 1 szt. z powodem „pęknięta blacha” (zakładka Złom, na zleceniu wymagane Drzwi rośnie o 1).
5. Stanowisko, plakietka „Wycinanie”, wózek D003: przycisk „Popraw”, odstaw na D004 (czeka na Gięcie).
   Powstaje kontrola Ponowna z punktami Wycinania. Oceń ją na zgodną.
6. Strona wózka D002 (konto oper): w kolumnie „Kontrola doraźna” skieruj 1 szt. z punktami etapu Gięcie
   i oceń ją w kolejce kontroli.
7. Cofanie: na stanowisku cofnij ostatnie przejęcie poprawki (przycisk „Cofnij”), status wraca do „do poprawki”.
"""
    with open(os.path.join(OUT, 'scenariusz_5b.txt'), 'w', encoding='utf-8') as fh:
        fh.write(steps)
    print(steps)
    print('PLIKI w', OUT)
