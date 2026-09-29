import os
from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from models import INTERSTAGE_STAGES

NAVY  = colors.HexColor('#1a5276')
TEAL  = colors.HexColor('#1a6e8a')
LIGHT = colors.HexColor('#eaf2f8')
GRAY  = colors.HexColor('#f2f3f4')
RED   = colors.HexColor('#c0392b')
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


def _ok_ng_color(val):
    if val == 'OK':
        return GREEN
    if val == 'NG':
        return RED
    return colors.grey


def generate_pdf(records, zo_filter='') -> bytes:
    _register_fonts()
    FONT      = 'DejaVu'      if _FONTS_REGISTERED else 'Helvetica'
    FONT_BOLD = 'DejaVu-Bold' if _FONTS_REGISTERED else 'Helvetica-Bold'

    def P(text, style=None):
        return Paragraph(str(text) if text is not None else '—', style or normal)

    def status_p(val):
        c = _ok_ng_color(val)
        st = ParagraphStyle('s', fontName=FONT_BOLD, fontSize=8, textColor=c, leading=10)
        return P(val if val else '—', st)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=14*mm, bottomMargin=14*mm,
        title='Kontrola międzyetapowa',
    )

    normal = ParagraphStyle('n', fontName=FONT, fontSize=8, leading=10)
    bold   = ParagraphStyle('b', fontName=FONT_BOLD, fontSize=8, leading=10)
    small  = ParagraphStyle('s', fontName=FONT, fontSize=7, leading=9, textColor=colors.grey)
    title  = ParagraphStyle('t', fontName=FONT_BOLD, fontSize=13, leading=17, textColor=WHITE)

    story = []

    # ── Nagłówek ──────────────────────────────────────────────────────────────
    subtitle = f'Filtr ZO: {zo_filter}' if zo_filter else 'Wszystkie rekordy'
    hdr_data = [[
        P('KONTROLA MIĘDZYETAPOWA', title),
        P(subtitle, title),
        P(datetime.now().strftime('%d.%m.%Y'), title),
    ]]
    hdr_tbl = Table(hdr_data, colWidths=['45%', '35%', '20%'])
    hdr_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('TEXTCOLOR',  (0, 0), (-1, -1), WHITE),
        ('ALIGN',      (2, 0), (2,  0), 'RIGHT'),
        ('PADDING',    (0, 0), (-1, -1), 6),
    ]))
    story.append(hdr_tbl)
    story.append(Spacer(1, 5*mm))

    # ── Tabela danych ─────────────────────────────────────────────────────────
    col_headers = [P('NR ZO', bold)] + [P(st['label'], bold) for st in INTERSTAGE_STAGES] + [
        P('QAR', bold), P('DATA', bold),
    ]
    rows = [col_headers]

    def stage_cell(chk):
        if not chk or not chk.result:
            return P('—')
        who = chk.employee.name if chk.employee else ''
        st = ParagraphStyle('s', fontName=FONT_BOLD, fontSize=8, textColor=_ok_ng_color(chk.result), leading=10)
        return [Paragraph(chk.result, st), Paragraph(who, small)] if who else Paragraph(chk.result, st)

    for rec in records:
        cmap = rec.check_map
        qars = ', '.join(c.qar_report.number for c in rec.checks if c.qar_report)
        date_label = rec.created_at.strftime('%d.%m.%Y') if rec.created_at else '—'
        rows.append(
            [P(rec.zo_number, bold)]
            + [stage_cell(cmap.get(st['key'])) for st in INTERSTAGE_STAGES]
            + [P(qars or '—'), P(date_label)]
        )

    col_widths = [32*mm] + [27*mm] * len(INTERSTAGE_STAGES) + [30*mm, 22*mm]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), TEAL),
        ('TEXTCOLOR',  (0, 0), (-1, 0), WHITE),
        ('FONTNAME',   (0, 0), (-1, 0), FONT_BOLD),
        ('FONTSIZE',   (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, GRAY]),
        ('GRID',  (0, 0), (-1, -1), 0.3, colors.grey),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('PADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(tbl)

    # ── Stopka ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=colors.grey))
    story.append(Spacer(1, 2*mm))
    story.append(P(
        f'Wygenerowano: {datetime.now().strftime("%d.%m.%Y %H:%M")}  |  '
        f'Liczba rekordów: {len(records)}',
        small
    ))

    doc.build(story)
    return buf.getvalue()
