import os
from io import BytesIO
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import make_response

_LOCAL_TZ = ZoneInfo('Europe/Warsaw')

def _ldt(dt, fmt='%d.%m.%Y %H:%M'):
    if dt is None:
        return ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_LOCAL_TZ).strftime(fmt)
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, Image as RLImage, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

PRIMARY   = colors.HexColor('#1a5276')
SECONDARY = colors.HexColor('#1a6e8a')
SUCCESS   = colors.HexColor('#27ae60')
ERROR     = colors.HexColor('#c0392b')
NEUTRAL   = colors.HexColor('#f5f5f5')
WHITE     = colors.white
DARK      = colors.HexColor('#1a1a1a')

W, H   = A4
MARGIN = 18 * mm

_FONTS_REGISTERED = False
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_FONT_SEARCH = [
    os.path.join(_BASE_DIR, 'fonts'),
    '/usr/share/fonts/truetype/dejavu',
    '/usr/share/fonts/dejavu',
    'C:/Windows/Fonts',
]


def _find_fonts_dir():
    for d in _FONT_SEARCH:
        if os.path.isfile(os.path.join(d, 'DejaVuSans.ttf')):
            return d
    raise RuntimeError(
        f'DejaVu Sans fonts not found. Copy DejaVuSans*.ttf to: {_FONT_SEARCH[0]}'
    )


def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    d = _find_fonts_dir()
    pdfmetrics.registerFont(TTFont('DVSans',        os.path.join(d, 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont('DVSans-Bold',   os.path.join(d, 'DejaVuSans-Bold.ttf')))
    pdfmetrics.registerFont(TTFont('DVSans-Italic', os.path.join(d, 'DejaVuSans-Oblique.ttf')))
    pdfmetrics.registerFont(TTFont('DVSans-BoldIt', os.path.join(d, 'DejaVuSans-BoldOblique.ttf')))
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFontFamily(
        'DVSans',
        normal='DVSans',
        bold='DVSans-Bold',
        italic='DVSans-Italic',
        boldItalic='DVSans-BoldIt',
    )
    _FONTS_REGISTERED = True


def _s(font='DVSans', size=9, color=DARK, bold=False, italic=False,
       align=0, leading=None, indent=0, after=0):
    """Create a one-off ParagraphStyle with DejaVu font."""
    _register_fonts()
    if bold and italic:
        fn = 'DVSans-BoldIt'
    elif bold:
        fn = 'DVSans-Bold'
    elif italic:
        fn = 'DVSans-Italic'
    else:
        fn = 'DVSans'
    import random, string
    name = 'S_' + ''.join(random.choices(string.ascii_lowercase, k=8))
    return ParagraphStyle(
        name,
        fontName=fn,
        fontSize=size,
        textColor=color,
        leading=leading or size * 1.45,
        alignment=align,
        leftIndent=indent,
        spaceAfter=after,
    )


def generate_pdf(report, items_by_category, upload_folder):
    _register_fonts()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=report.title,
    )

    content_width = W - 2 * MARGIN
    story = []

    # ── Logo + tytuł (tabela 2-kolumnowa) ────────────────────────────────────
    logo_path = os.path.join(os.path.dirname(__file__), 'static', 'k_logo.jpg')
    logo_h = 20 * mm + 10
    logo_w = logo_h  # fallback: square
    if os.path.exists(logo_path):
        try:
            from PIL import Image as PILImage
            with PILImage.open(logo_path) as im:
                iw, ih = im.size
                logo_w = logo_h * (iw / ih)
            logo_cell = RLImage(logo_path, width=logo_w, height=logo_h)
        except Exception:
            logo_cell = Paragraph('', _s())
    else:
        logo_cell = Paragraph('', _s())

    title_block = [
        Paragraph('System RP - Raportowanie produkcji',
                  _s(size=7, color=SECONDARY, after=2)),
        Paragraph(report.title,
                  _s(size=14, bold=True, color=PRIMARY, after=3)),
    ]

    meta_lines = [
        f'Operator: {report.author.username}',
        f'Utworzono: {_ldt(report.created_at)}',
    ]
    if report.completed_at:
        meta_lines.append(f'Zamknięto: {_ldt(report.completed_at)}')
    status_label = 'Zamknięty' if report.status == 'completed' else 'W trakcie'
    meta_lines.append(f'Status: {status_label}  |  Ukończono: {report.completion_percent}%')
    for line in meta_lines:
        title_block.append(Paragraph(line, _s(size=8, color=SECONDARY, after=1)))

    logo_col_w = logo_w + 4 * mm
    header_table = Table(
        [[logo_cell, title_block]],
        colWidths=[logo_col_w, content_width - logo_col_w],
    )
    header_table.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width='100%', thickness=1.5, color=PRIMARY,
                            spaceBefore=6, spaceAfter=10))

    # ── Kategorie ────────────────────────────────────────────────────────────
    for cat, items in items_by_category.items():
        _append_category(story, cat, items, content_width, upload_folder)
        story.append(Spacer(1, 6 * mm))

    # ── Stopka ───────────────────────────────────────────────────────────────
    story.append(HRFlowable(width='100%', thickness=0.5, color=colors.grey,
                            spaceBefore=4, spaceAfter=4))
    story.append(Paragraph(
        f'Wygenerowano: {_ldt(datetime.now(timezone.utc))} '
        f'| System RP - Raportowanie produkcji – Firma Kubiak',
        _s(size=7, color=colors.grey, align=1)))

    doc.build(story)
    buf.seek(0)

    response = make_response(buf.read())
    import unicodedata
    from urllib.parse import quote
    # ASCII fallback for old clients
    ascii_name = (unicodedata.normalize('NFKD', report.title)
                  .encode('ascii', 'ignore').decode('ascii')
                  .replace(' ', '_').strip('_') or 'raport')[:40]
    # RFC 5987 UTF-8 filename for modern clients
    utf8_name = quote(f'raport_{report.title[:60]}.pdf', safe=' -()')
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = (
        f"attachment; filename=\"raport_{ascii_name}.pdf\"; "
        f"filename*=UTF-8''{utf8_name}"
    )
    return response


def _append_category(story, cat, items, content_width, upload_folder):
    # Nagłówek kategorii
    cat_table = Table(
        [[Paragraph(cat.name.upper(), _s(size=10, bold=True, color=WHITE))]],
        colWidths=[content_width],
    )
    cat_table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), PRIMARY),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
    ]))
    story.append(cat_table)

    # Wiersze zadań – każda komórka to pojedynczy Paragraph
    rows = []
    for item in items:
        if item.result == 'ok':
            sym, sym_color = 'OK', SUCCESS
        elif item.result == 'ng':
            sym, sym_color = 'NG', ERROR
        else:
            sym, sym_color = '–', colors.HexColor('#aaaaaa')
        check_cell = Paragraph(sym, _s(size=9, bold=True, color=sym_color, align=1))

        lines = [f'<b>{item.task.title}</b>']
        if item.task.task_type in ('numeric', 'text', 'installer') and item.value_text:
            unit = f' {item.task.unit}' if item.task.unit else ''
            lines.append(f'{item.value_text}{unit}')
        elif item.task.task_type == 'measurements' and item.value_text:
            unit = f' {item.task.unit}' if item.task.unit else ''
            vals = ' / '.join(v for v in item.value_text.split('|') if v)
            if vals:
                lines.append(f'{vals}{unit}')
        if item.notes:
            lines.append(f'<i>Odnotuj: {item.notes}</i>')
        if item.checked_at:
            ts = _ldt(item.checked_at)
            lines.append(f'<font size="7"><font color="#888888">Oceniono: {ts}</font></font>')
        task_cell = Paragraph('<br/>'.join(lines), _s(size=9))
        rows.append([check_cell, task_cell])

    task_table = Table(rows, colWidths=[14 * mm, content_width - 14 * mm])
    task_table.setStyle(TableStyle([
        ('VALIGN',         (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',     (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING',  (0, 0), (-1, -1), 5),
        ('LEFTPADDING',    (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',   (0, 0), (-1, -1), 5),
        ('LINEBELOW',      (0, 0), (-1, -2), 0.3, colors.HexColor('#e0e0e0')),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [WHITE, NEUTRAL]),
        ('BOX',            (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
    ]))
    story.append(task_table)

    # Zdjęcia
    photo_table = _build_photo_table(items, upload_folder, content_width)
    if photo_table:
        story.append(Spacer(1, 2 * mm))
        story.append(photo_table)


def _build_photo_table(items, upload_folder, content_width):
    per_row   = 3
    gap       = 3 * mm
    photo_w   = (content_width - (per_row - 1) * gap) / per_row
    photo_h   = photo_w * 0.75
    col_w     = [photo_w] * per_row
    cap_style = _s(size=7, color=SECONDARY, align=1)

    collected = []
    for item in items:
        for photo in item.photos.all():
            path = os.path.join(upload_folder, photo.filename)
            if not os.path.exists(path):
                continue
            try:
                img = RLImage(path, width=photo_w, height=photo_h)
            except Exception:
                img = Paragraph('[błąd zdjęcia]', cap_style)
            collected.append((img, item.task.title[:40]))

    if not collected:
        return None

    all_rows = []
    for i in range(0, len(collected), per_row):
        chunk   = collected[i:i + per_row]
        img_row = [img  for img, _   in chunk]
        cap_row = [Paragraph(cap, cap_style) for _, cap in chunk]
        while len(img_row) < per_row:
            img_row.append(Spacer(photo_w, photo_h))
            cap_row.append(Paragraph('', cap_style))
        all_rows.append(img_row)
        all_rows.append(cap_row)

    table = Table(all_rows, colWidths=col_w, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING',   (0, 0), (-1, -1), 2),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 2),
    ]))
    return table
