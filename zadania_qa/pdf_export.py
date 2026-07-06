import os
from datetime import datetime
from io import BytesIO

from flask import make_response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

NAVY  = colors.HexColor('#1a3a5c')
LIGHT = colors.HexColor('#eaf2f8')
GRAY  = colors.HexColor('#f5f5f5')
GREEN = colors.HexColor('#1e8449')
WHITE = colors.white

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
    except Exception:
        pass


def generate_qa_tasks_pdf(tasks, header):
    _register_fonts()
    FONT      = 'DejaVu'      if _FONTS_REGISTERED else 'Helvetica'
    FONT_BOLD = 'DejaVu-Bold' if _FONTS_REGISTERED else 'Helvetica-Bold'

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
    )

    def _p(text, font=FONT, size=9, color=colors.black, align='LEFT', leading=None):
        if leading is None:
            leading = size * 1.3
        alignment = {'LEFT': 0, 'CENTER': 1, 'RIGHT': 2}.get(align, 0)
        return Paragraph(
            str(text or '').replace('\n', '<br/>'),
            ParagraphStyle('x', fontName=font, fontSize=size,
                           textColor=color, alignment=alignment, leading=leading),
        )

    story = []
    page_w = A4[0] - 24 * mm

    # ── Nagłówek ──────────────────────────────────────────────────────────────
    title_table = Table(
        [[_p('KONTROLA JAKOŚCI', FONT_BOLD, 12, WHITE, 'CENTER'),
          _p('Karta wykonania zadań - produkcja rozdzielnic', FONT_BOLD, 11, WHITE, 'CENTER')]],
        colWidths=[page_w * 0.3, page_w * 0.7],
    )
    title_table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), NAVY),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(title_table)
    story.append(Spacer(1, 3 * mm))

    half = page_w / 2
    meta_rows = [[
        _p('Data:', FONT_BOLD, 9, NAVY), _p(header['date'], size=9),
        _p('Zmiana:', FONT_BOLD, 9, NAVY), _p(header['shift'] or '', size=9),
    ], [
        _p('Kontroler:', FONT_BOLD, 9, NAVY), _p(header['controller'], size=9),
        _p('Obszar:', FONT_BOLD, 9, NAVY), _p(header['area'], size=9),
    ]]
    meta_table = Table(meta_rows, colWidths=[22 * mm, half - 22 * mm, 22 * mm, half - 22 * mm])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (0, -1), LIGHT),
        ('BACKGROUND',    (2, 0), (2, -1), LIGHT),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ('GRID',          (0, 0), (-1, -1), 0.4, colors.HexColor('#d0dce8')),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 4 * mm))

    # ── Tabela zadań ──────────────────────────────────────────────────────────
    header_row = [
        _p('Lp.', FONT_BOLD, 8, WHITE, 'CENTER'),
        _p('Zadanie do wykonania', FONT_BOLD, 8, WHITE, 'CENTER'),
        _p('Wykonano', FONT_BOLD, 8, WHITE, 'CENTER'),
        _p('Uwagi', FONT_BOLD, 8, WHITE, 'CENTER'),
        _p('Podpis', FONT_BOLD, 8, WHITE, 'CENTER'),
    ]
    rows = [header_row]
    for i, task in enumerate(tasks, start=1):
        text = f'<b>{task.title}</b>'
        if task.description:
            text += f'<br/>{task.description}'
        if task.is_done:
            done_cell = _p('TAK', FONT_BOLD, 9, GREEN, 'CENTER')
        else:
            done_cell = _p('NIE', FONT_BOLD, 9, colors.grey, 'CENTER')
        rows.append([
            _p(str(i), size=9, align='CENTER'),
            _p(text, size=8),
            done_cell,
            _p(task.notes or '', size=8),
            _p(''),
        ])

    lp_w, done_w, sig_w, notes_w = 9 * mm, 20 * mm, 26 * mm, 42 * mm
    task_w = page_w - lp_w - done_w - sig_w - notes_w
    task_table = Table(rows, colWidths=[lp_w, task_w, done_w, notes_w, sig_w], repeatRows=1)
    task_table.setStyle(TableStyle([
        ('BACKGROUND',     (0, 0), (-1, 0), NAVY),
        ('VALIGN',         (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',     (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING',  (0, 0), (-1, -1), 5),
        ('LEFTPADDING',    (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',   (0, 0), (-1, -1), 4),
        ('GRID',           (0, 0), (-1, -1), 0.4, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, GRAY]),
    ]))
    story.append(task_table)
    story.append(Spacer(1, 6 * mm))

    # ── Podsumowanie / podpisy ────────────────────────────────────────────────
    story.append(_p('Podsumowanie zmiany / uwagi dodatkowe:', FONT_BOLD, 9, NAVY))
    story.append(Spacer(1, 12 * mm))
    story.append(HRFlowable(width='100%', thickness=0.4, color=colors.HexColor('#cccccc')))
    story.append(Spacer(1, 10 * mm))

    sig_table = Table(
        [[_p('Podpis kontrolera jakości:', size=9), _p('Podpis przełożonego:', size=9)]],
        colWidths=[half, half],
    )
    sig_table.setStyle(TableStyle([
        ('LINEABOVE',  (0, 0), (0, 0), 0.5, colors.black),
        ('LINEABOVE',  (1, 0), (1, 0), 0.5, colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(sig_table)

    story.append(Spacer(1, 6 * mm))
    story.append(_p(
        f'Wygenerowano: {datetime.now().strftime("%d.%m.%Y %H:%M")}  |  '
        f'PS QCP – System Kontroli Jakości',
        size=7, color=colors.grey, align='CENTER',
    ))

    doc.build(story)
    pdf_bytes = buf.getvalue()
    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename="karta_zadan_QA.pdf"'
    return response
