"""
Generator PDF dla kosztorysu — dokument wewnętrzny firmy.
Korzysta z reportlab (już zainstalowany w projekcie QA).
"""
import os
from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Kolory firmowe Kubiak
NAVY  = colors.HexColor('#1a5276')
TEAL  = colors.HexColor('#1a6e8a')
LIGHT = colors.HexColor('#eaf2f8')
GRAY  = colors.HexColor('#f2f3f4')
RED   = colors.HexColor('#c0392b')
WHITE = colors.white
BLACK = colors.black

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(BASE_DIR, 'fonts')

_FONTS_REGISTERED = False


def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    try:
        pdfmetrics.registerFont(TTFont('DejaVu',      os.path.join(FONT_DIR, 'DejaVuSans.ttf')))
        pdfmetrics.registerFont(TTFont('DejaVu-Bold', os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf')))
        _FONTS_REGISTERED = True
        return 'DejaVu', 'DejaVu-Bold'
    except Exception:
        return 'Helvetica', 'Helvetica-Bold'


def generate_pdf(quote) -> bytes:
    _register_fonts()
    FONT      = 'DejaVu'      if _FONTS_REGISTERED else 'Helvetica'
    FONT_BOLD = 'DejaVu-Bold' if _FONTS_REGISTERED else 'Helvetica-Bold'

    cfg  = quote.config
    calc = cfg.calculation or {}

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=14*mm, bottomMargin=14*mm,
        title=f'Kosztorys {quote.number}',
    )

    styles = getSampleStyleSheet()
    normal = ParagraphStyle('n', fontName=FONT, fontSize=8, leading=10)
    bold   = ParagraphStyle('b', fontName=FONT_BOLD, fontSize=8, leading=10)
    small  = ParagraphStyle('s', fontName=FONT, fontSize=7, leading=9, textColor=colors.grey)
    title  = ParagraphStyle('t', fontName=FONT_BOLD, fontSize=14, leading=18, textColor=WHITE)

    def P(text, style=None): return Paragraph(str(text), style or normal)
    def PLN(v): return f'{float(v):,.2f} PLN'.replace(',', ' ')
    def N2(v):  return f'{float(v):,.2f}'.replace(',', ' ')

    story = []

    # ── Nagłówek ─────────────────────────────────────────────────────────────
    hdr_data = [[
        P(f'KOSZTORYS  {quote.number}', title),
        P(f'{quote.cabinet_type.name}', title),
        P(datetime.now().strftime('%d.%m.%Y'), title),
    ]]
    hdr_tbl = Table(hdr_data, colWidths=['40%', '40%', '20%'])
    hdr_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), NAVY),
        ('TEXTCOLOR',  (0,0), (-1,-1), WHITE),
        ('ALIGN',      (2,0), (2,0), 'RIGHT'),
        ('PADDING',    (0,0), (-1,-1), 6),
    ]))
    story.append(hdr_tbl)
    story.append(Spacer(1, 4*mm))

    # ── Info podstawowe ───────────────────────────────────────────────────────
    info_data = [
        [P('Klient:', bold), P(quote.client_name),
         P('Wymiary:', bold), P(f'{cfg.width} × {cfg.height} × {cfg.depth} mm'),
         P('Objętość:', bold), P(f'{int(calc.get("volume",0))} dm³'),
         P('Status:', bold), P(quote.status_label)],
    ]
    info_tbl = Table(info_data, colWidths=[20*mm, 55*mm, 22*mm, 50*mm, 22*mm, 28*mm, 18*mm, 30*mm])
    info_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), LIGHT),
        ('FONTNAME', (0,0), (-1,-1), FONT),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('PADDING', (0,0), (-1,-1), 4),
        ('GRID', (0,0), (-1,-1), 0.3, colors.grey),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 4*mm))

    # ── Tabela elementów blaszanych ───────────────────────────────────────────
    el_hdr = [P(h, bold) for h in [
        'Element', 'Il.', 'Mat.', 'Dł', 'Szer', 'Pow.m²',
        'Gr.', 'Waga/szt', 'Waga łącz.', 'Blacha', 'Malow.', 'Koszt/szt', 'Koszt łącznie'
    ]]
    el_rows = [el_hdr]

    for e in calc.get('elements', []):
        if e.get('qty', 0) == 0:
            continue
        el_rows.append([
            P(e['name']),
            P(str(e['qty']), bold),
            P(e['material'], small),
            P(str(e['L'])),
            P(str(e['W'])),
            P(N2(e['area'])),
            P(str(e['thickness'])),
            P(N2(e['weight_per'])),
            P(N2(e['weight_total'])),
            P(N2(e['cost_sheet'])),
            P(N2(e['cost_paint']) if e['cost_paint'] else '—'),
            P(N2(e['cost_per'])),
            P(PLN(e['cost_total']), bold),
        ])

    waste = calc.get('waste', {})
    if waste:
        el_rows.append([
            P(waste.get('name', 'Odpad'), bold),
            P('1'), P(''), P(''), P(''), P(''), P(''), P(''),
            P(N2(waste.get('weight_total', 0))),
            P(''), P(''), P(''),
            P(PLN(waste.get('cost_total', 0)), bold),
        ])

    cw_el = [38*mm, 8*mm, 10*mm, 10*mm, 10*mm, 12*mm,
             8*mm, 16*mm, 16*mm, 18*mm, 16*mm, 18*mm, 24*mm]
    el_tbl = Table(el_rows, colWidths=cw_el, repeatRows=1)
    el_style = [
        ('BACKGROUND', (0,0), (-1,0), TEAL),
        ('TEXTCOLOR',  (0,0), (-1,0), WHITE),
        ('FONTNAME',   (0,0), (-1,0), FONT_BOLD),
        ('FONTSIZE',   (0,0), (-1,-1), 7.5),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, GRAY]),
        ('GRID', (0,0), (-1,-1), 0.3, colors.grey),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('ALIGN', (0,0), (0,-1), 'LEFT'),
        ('PADDING', (0,0), (-1,-1), 3),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        # Odpad row
        ('BACKGROUND', (0,-1), (-1,-1), LIGHT),
        ('FONTNAME', (0,-1), (-1,-1), FONT_BOLD),
    ]
    el_tbl.setStyle(TableStyle(el_style))
    story.append(P('Elementy blaszane', ParagraphStyle('sh', fontName=FONT_BOLD, fontSize=9,
                                                        textColor=NAVY, spaceBefore=4, spaceAfter=3)))
    story.append(el_tbl)
    story.append(Spacer(1, 4*mm))

    # ── Osprzęt ───────────────────────────────────────────────────────────────
    hw = calc.get('hardware', [])
    svc = calc.get('services', [])
    if hw or svc:
        hw_hdr = [P(h, bold) for h in ['Pozycja', 'Il.', 'j.m.', 'Cena/szt', 'Wartość']]
        hw_rows = [hw_hdr]
        for h in hw:
            hw_rows.append([
                P(h['name']), P(str(h['qty'])),
                P(h['unit']), P(PLN(h['unit_price'])),
                P(PLN(h['total']), bold)
            ])
        for s in svc:
            hw_rows.append([
                P(s['name']), P(str(s['qty'])),
                P(s['unit']), P(PLN(s['unit_price'])),
                P(PLN(s['total']), bold)
            ])

        hw_tbl = Table(hw_rows, colWidths=[90*mm, 14*mm, 16*mm, 35*mm, 35*mm], repeatRows=1)
        hw_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), TEAL),
            ('TEXTCOLOR',  (0,0), (-1,0), WHITE),
            ('FONTSIZE',   (0,0), (-1,-1), 7.5),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, GRAY]),
            ('GRID', (0,0), (-1,-1), 0.3, colors.grey),
            ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
            ('ALIGN', (0,0), (0,-1), 'LEFT'),
            ('PADDING', (0,0), (-1,-1), 3),
        ]))
        story.append(P('Osprzęt, robocizna i usługi',
                       ParagraphStyle('sh', fontName=FONT_BOLD, fontSize=9,
                                      textColor=NAVY, spaceBefore=4, spaceAfter=3)))
        story.append(hw_tbl)
        story.append(Spacer(1, 5*mm))

    # ── Podsumowanie cenowe ────────────────────────────────────────────────────
    sum_data = [
        [P('KOSZT WŁASNY', bold), P(PLN(calc.get('cost_total', 0)),
                                    ParagraphStyle('lrg', fontName=FONT_BOLD,
                                                   fontSize=12, textColor=NAVY))],
        [P(f'Marża ×{calc.get("margin", 2.15)}'), P(PLN(calc.get('price_catalog', 0)))],
        [P(f'Po rabacie {calc.get("discount_pct", 0):.1f}%'), P(PLN(calc.get('price_discount', 0)))],
        [P(f'Po bonusie {calc.get("bonus_pct", 0):.1f}%'), P(PLN(calc.get('price_bonus', 0)))],
        [P('Zyskowność', bold),
         P(f'{calc.get("profitability", 0):.2f}%',
           ParagraphStyle('pct', fontName=FONT_BOLD, fontSize=11,
                          textColor=colors.HexColor('#27ae60') if calc.get('profitability',0) > 0 else RED))],
    ]
    sum_tbl = Table(sum_data, colWidths=[60*mm, 60*mm])
    sum_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), LIGHT),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('GRID', (0,0), (-1,-1), 0.3, colors.grey),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('PADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(sum_tbl)

    # ── Stopka ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=colors.grey))
    story.append(Spacer(1, 2*mm))
    footer_txt = (f'Wycena wygenerowana: {datetime.now().strftime("%d.%m.%Y %H:%M")}  |  '
                  f'Autor: {quote.created_by.username}  |  '
                  f'Dokument wewnętrzny — nie przekazywać klientowi')
    story.append(P(footer_txt, small))

    doc.build(story)
    return buf.getvalue()
