import os
from datetime import datetime
from io import BytesIO

from flask import make_response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, Image, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

NAVY  = colors.HexColor('#1a3a5c')
TEAL  = colors.HexColor('#1a6e8a')
LIGHT = colors.HexColor('#eaf2f8')
GRAY  = colors.HexColor('#f5f5f5')
RED   = colors.HexColor('#c0392b')
GREEN = colors.HexColor('#1e8449')
AMBER = colors.HexColor('#d97706')
WHITE = colors.white

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(BASE_DIR, 'fonts')
LOGO_PATH = os.path.join(BASE_DIR, 'static', 'k_logo.png')

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


def _status_color(status):
    return {
        'open':        AMBER,
        'in_progress': TEAL,
        'closed':      GREEN,
    }.get(status, NAVY)


def _status_label(status):
    return {
        'open':        'OTWARTY',
        'in_progress': 'W TOKU',
        'closed':      'ZAMKNIĘTY',
    }.get(status, status.upper())


def generate_qar_pdf(report, upload_folder):
    _register_fonts()
    FONT      = 'DejaVu'      if _FONTS_REGISTERED else 'Helvetica'
    FONT_BOLD = 'DejaVu-Bold' if _FONTS_REGISTERED else 'Helvetica-Bold'

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
    )

    def _p(text, font=FONT, size=9, color=colors.black, align='LEFT', leading=None):
        if leading is None:
            leading = size * 1.35
        alignment = {'LEFT': 0, 'CENTER': 1, 'RIGHT': 2}.get(align, 0)
        return Paragraph(
            str(text or '').replace('\n', '<br/>'),
            ParagraphStyle('x', fontName=font, fontSize=size,
                           textColor=color, alignment=alignment, leading=leading),
        )

    story = []
    page_w = A4[0] - 30 * mm

    # ── Nagłówek ──────────────────────────────────────────────────────────────
    logo_cell = ''
    if os.path.exists(LOGO_PATH):
        try:
            logo_cell = Image(LOGO_PATH, width=30 * mm, height=12 * mm,
                              kind='proportional')
        except Exception:
            pass

    status_color = _status_color(report.status)
    header_data = [[
        logo_cell,
        _p('RAPORT JAKOŚCI – QAR', FONT_BOLD, 14, NAVY, 'CENTER'),
        _p(_status_label(report.status), FONT_BOLD, 11, WHITE, 'CENTER'),
    ]]
    header_table = Table(header_data, colWidths=[35 * mm, page_w - 70 * mm, 35 * mm])
    header_table.setStyle(TableStyle([
        ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND',  (2, 0), (2, 0),   status_color),
        ('ROUNDEDCORNERS', [3]),
        ('TOPPADDING',  (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 4 * mm))

    # ── Informacje podstawowe ─────────────────────────────────────────────────
    info_rows = [
        [_p('Numer raportu', FONT_BOLD, 9, NAVY),  _p(report.number, FONT_BOLD, 10)],
    ]
    if report.zo_number:
        info_rows.append([_p('Numer ZO', FONT_BOLD, 9, NAVY), _p(report.zo_number, size=9)])
    if report.drawing_number:
        info_rows.append([_p('Numer rysunku', FONT_BOLD, 9, NAVY), _p(report.drawing_number, size=9)])
    info_rows += [
        [_p('Tytuł', FONT_BOLD, 9, NAVY),           _p(report.title, size=9)],
        [_p('Kategoria', FONT_BOLD, 9, NAVY),        _p(report.category or '—', size=9)],
        [_p('Lokalizacja / obiekt', FONT_BOLD, 9, NAVY), _p(report.location or '—', size=9)],
        [_p('Autor', FONT_BOLD, 9, NAVY),
         _p(f'{report.author.username}  ({report.created_at.strftime("%d.%m.%Y %H:%M")})', size=9)],
    ]
    if report.verified_by and report.verified_at:
        info_rows.append([
            _p('Weryfikacja', FONT_BOLD, 9, NAVY),
            _p(f'{report.verified_by.username}  ({report.verified_at.strftime("%d.%m.%Y %H:%M")})', size=9),
        ])
    if report.updated_at and report.updated_at != report.created_at:
        info_rows.append([
            _p('Ostatnia zmiana', FONT_BOLD, 9, NAVY),
            _p(report.updated_at.strftime('%d.%m.%Y %H:%M'), size=9),
        ])

    col1 = 42 * mm
    info_table = Table(info_rows, colWidths=[col1, page_w - col1])
    info_table.setStyle(TableStyle([
        ('BACKGROUND',  (0, 0), (0, -1), LIGHT),
        ('VALIGN',      (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',  (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('GRID',        (0, 0), (-1, -1), 0.4, colors.HexColor('#d0dce8')),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 5 * mm))

    # ── Sekcja: Opis problemu ─────────────────────────────────────────────────
    def _section(title_text, body_text):
        if not body_text:
            return
        sec = Table(
            [[_p(title_text, FONT_BOLD, 9, WHITE)],
             [_p(body_text, size=9)]],
            colWidths=[page_w],
        )
        sec.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (0, 0), NAVY),
            ('BACKGROUND',    (0, 1), (0, 1), WHITE),
            ('TOPPADDING',    (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING',   (0, 0), (-1, -1), 8),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
            ('BOX',           (0, 0), (-1, -1), 0.5, colors.HexColor('#d0dce8')),
        ]))
        story.append(sec)
        story.append(Spacer(1, 4 * mm))

    _section('OPIS PROBLEMU', report.description)
    _section('WNIOSKI', report.findings)
    _section('DZIAŁANIA NAPRAWCZE / ROZWIĄZANIE', report.resolution)

    # ── Zdjęcia ───────────────────────────────────────────────────────────────
    photos = report.photos.all()
    if photos:
        story.append(_p('DOKUMENTACJA FOTOGRAFICZNA', FONT_BOLD, 9, WHITE))
        # dummy — nagłówek sekcji jako osobna tabela
        hdr = Table([[_p('DOKUMENTACJA FOTOGRAFICZNA', FONT_BOLD, 9, WHITE)]],
                    colWidths=[page_w])
        hdr.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), NAVY),
            ('TOPPADDING',    (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ]))
        # Replace the wrong _p call above
        story.pop()
        story.append(hdr)
        story.append(Spacer(1, 3 * mm))

        IMG_W  = (page_w - 10 * mm) / 2
        IMG_H  = 60 * mm
        row_cells = []
        row_caps  = []

        for photo in photos:
            img_path = os.path.join(upload_folder, photo.filename)
            if os.path.exists(img_path):
                try:
                    img = Image(img_path, width=IMG_W, height=IMG_H, kind='proportional')
                except Exception:
                    img = _p('[błąd obrazu]', size=8, color=colors.grey)
            else:
                img = _p('[brak pliku]', size=8, color=colors.grey)
            row_cells.append(img)
            row_caps.append(_p(photo.caption or photo.original_name or '', size=8,
                               color=colors.grey, align='CENTER'))
            if len(row_cells) == 2:
                photo_table = Table(
                    [row_cells, row_caps],
                    colWidths=[IMG_W + 3 * mm, IMG_W + 3 * mm],
                )
                photo_table.setStyle(TableStyle([
                    ('ALIGN',       (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
                    ('TOPPADDING',  (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
                    ('INNERGRID',   (0, 0), (-1, -1), 0.3, GRAY),
                    ('BOX',         (0, 0), (-1, -1), 0.3, GRAY),
                ]))
                story.append(photo_table)
                story.append(Spacer(1, 3 * mm))
                row_cells = []
                row_caps  = []

        if row_cells:
            while len(row_cells) < 2:
                row_cells.append('')
                row_caps.append('')
            photo_table = Table(
                [row_cells, row_caps],
                colWidths=[IMG_W + 3 * mm, IMG_W + 3 * mm],
            )
            photo_table.setStyle(TableStyle([
                ('ALIGN',       (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING',  (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
                ('INNERGRID',   (0, 0), (-1, -1), 0.3, GRAY),
                ('BOX',         (0, 0), (-1, -1), 0.3, GRAY),
            ]))
            story.append(photo_table)
            story.append(Spacer(1, 3 * mm))

    # ── Stopka ────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#c0cfe0')))
    story.append(Spacer(1, 2 * mm))
    story.append(_p(
        f'Wygenerowano: {datetime.now().strftime("%d.%m.%Y %H:%M")}  |  '
        f'RP-Sys – System RP - Raportowanie produkcji',
        size=7, color=colors.grey, align='CENTER',
    ))

    doc.build(story)
    pdf_bytes = buf.getvalue()
    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    safe_num = report.number.replace('/', '-')
    response.headers['Content-Disposition'] = (
        f'inline; filename="QAR_{safe_num}.pdf"'
    )
    return response
