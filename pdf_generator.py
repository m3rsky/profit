import os
import re
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


def generate_pdf(report, items_by_category, upload_folder, stage_controls=None):
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

    # ── Jedna lista kontrolna (Końcowa + etapowe) ───────────────────────────
    _append_unified_controls(story, report, items_by_category, stage_controls,
                             content_width, upload_folder)

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


def _esc(text):
    return str(text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _kind_label(text):
    return Paragraph(text.upper(), _s(size=7, bold=True, color=colors.HexColor('#888888'), after=1))


def _block_header(story, kind, title, res, res_color, meta):
    """Nagłówek bloku kontroli (Końcowej albo etapowej): rodzaj, nazwa z wynikiem, linia meta."""
    story.append(Spacer(1, 3 * mm))
    story.append(_kind_label(kind))
    story.append(Paragraph(f'<b>{_esc(title)}</b> &nbsp; <font color="{res_color.hexval()}">'
                           f'<b>{res}</b></font>', _s(size=9.5, after=1)))
    story.append(Paragraph(meta, _s(size=7.5, color=SECONDARY, after=3)))


def _points_table(story, spec, content_width):
    """Tabela Punkt / Wynik / Wartość wspólna dla obu rodzajów kontroli.
    spec: ('group', nazwa) albo ('row', tytuł_html, wynik, kolor, wartość_html)."""
    head_style = _s(size=7, bold=True, color=colors.HexColor('#666666'))
    rows = [[Paragraph('PUNKT', head_style), Paragraph('WYNIK', _s(size=7, bold=True, color=colors.HexColor('#666666'), align=1)),
             Paragraph('WARTOŚĆ', head_style)]]
    group_rows = []
    for entry in spec:
        if entry[0] == 'group':
            group_rows.append(len(rows))
            rows.append([Paragraph(f'<b>{_esc(entry[1])}</b>', _s(size=8)), '', ''])
        else:
            _, title, sym, col, value = entry
            rows.append([Paragraph(title, _s(size=8.5)),
                         Paragraph(sym, _s(size=8.5, bold=True, color=col, align=1)),
                         Paragraph(value, _s(size=8))])
    if len(rows) == 1:
        return
    table = Table(rows, colWidths=[content_width * 0.52, 14 * mm, content_width * 0.48 - 14 * mm],
                  repeatRows=1)
    style = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('LINEBELOW', (0, 0), (-1, -2), 0.3, colors.HexColor('#e0e0e0')),
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, colors.HexColor('#bbbbbb')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f7f8fa')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
    ]
    for i in group_rows:
        style += [('SPAN', (0, i), (-1, i)), ('BACKGROUND', (0, i), (-1, i), NEUTRAL)]
    table.setStyle(TableStyle(style))
    story.append(table)


def _result_cell(result):
    """('OK'|'NG'|inne) -> (symbol, kolor)."""
    r = (result or '').upper()
    if r == 'OK':
        return 'OK', SUCCESS
    if r == 'NG':
        return 'NG', ERROR
    return '–', colors.HexColor('#aaaaaa')


def _final_value(item):
    unit = f' {_esc(item.task.unit)}' if item.task.unit else ''
    if item.task.task_type == 'installer' and item.installers.count():
        inst_rows = item.installers.all()
        parts = []
        for ri in inst_rows:
            label = _initials(ri.installer.name)
            if ri.role:
                label = f'{ri.role}: {label}'
            if item.result == 'ng' and len(inst_rows) > 1 and ri.is_at_fault:
                label += ' (zawinił)'
            parts.append(_esc(label))
        return ' / '.join(parts)
    if item.task.task_type in ('numeric', 'text') and item.value_text:
        return f'{_esc(item.value_text)}{unit}'
    if item.task.task_type == 'measurements' and item.value_text:
        vals = ' / '.join(v for v in item.value_text.split('|') if v)
        return f'{_esc(vals)}{unit}' if vals else ''
    return ''


def _append_unified_controls(story, report, items_by_category, stage_controls,
                             content_width, upload_folder):
    """Jedna lista w raporcie: Kontrola Końcowa, potem kontrole etapowe, w tym samym układzie
    (nagłówek z wynikiem, meta, tabela Punkt / Wynik / Wartość). Odpowiada widokowi ekranowemu."""
    all_items = [it for items in items_by_category.values() for it in items]
    n_stages = len(stage_controls['stages']) if stage_controls else 0
    summary = 'Końcowa' if not n_stages else (
        f"Końcowa + {n_stages} {'kontrola etapowa' if n_stages == 1 else 'kontrole etapowe'}")
    head = Table([[Paragraph('LISTA KONTROLNA', _s(size=10, bold=True, color=WHITE)),
                   Paragraph(summary, _s(size=8, color=WHITE, align=2))]],
                 colWidths=[content_width * 0.5, content_width * 0.5])
    head.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), PRIMARY), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(head)

    # Kontrola Końcowa
    if report.status != 'completed':
        res, res_color = 'W trakcie', colors.HexColor('#b7791f')
    elif any(it.result == 'ng' for it in all_items):
        res, res_color = 'NG', ERROR
    else:
        res, res_color = 'OK', SUCCESS
    when = _ldt(report.completed_at or report.created_at)
    _block_header(story, 'Kontrola Końcowa', report.title, res, res_color,
                  f'Kontroler: {_esc(report.author.username)} | {when}')
    spec = []
    for cat, items in items_by_category.items():
        spec.append(('group', cat.name))
        for item in items:
            sym, col = _result_cell(item.result)
            title = _esc(item.task.title)
            if item.notes:
                title += f'<br/><i>{_esc(item.notes)}</i>'
            if item.qar_report:
                title += f'<br/><font size="7" color="#888888">{_esc(item.qar_report.number)}</font>'
            spec.append(('row', title, sym, col, _final_value(item)))
    _points_table(story, spec, content_width)
    photo_table = _build_photo_table(all_items, upload_folder, content_width)
    if photo_table:
        story.append(Spacer(1, 2 * mm))
        story.append(photo_table)

    # Kontrole etapowe
    if stage_controls:
        _append_stage_controls(story, stage_controls, content_width)
    story.append(Spacer(1, 4 * mm))


def _append_stage_controls(story, data, content_width):
    """Ostrzeżenia, potem każdy etap z osobą odpowiedzialną i punktami (ten sam układ co Kontrola Końcowa)."""
    warn_colors = {'ng': ERROR, 'warn': colors.HexColor('#b7791f'), 'info': SECONDARY}
    if data['warnings']:
        story.append(Spacer(1, 2 * mm))
    for level, text in data['warnings']:
        story.append(Paragraph(_esc(text), _s(size=8.5, bold=True, color=warn_colors.get(level, DARK),
                                              after=2, indent=4)))
    for st in data['stages']:
        if st['status'] != 'completed':
            res, res_color = 'W trakcie', warn_colors['warn']
        elif st['result'] == 'NG':
            res, res_color = 'NG', ERROR
        else:
            res, res_color = 'OK', SUCCESS
        label = st['department'] + (f" ({st['elements']})" if st['elements'] else '')
        meta = (f"Osoba odpowiedzialna: {_esc(st['performer'] or 'nie wskazano')} | "
                f"Kontroler: {_esc(st['controller'] or '-')} | {_ldt(st['when'])}")
        if st['attempt'] > 1:
            meta += f" | kontrola nr {st['attempt']}"
        _block_header(story, 'Kontrola etapowa', label, res, res_color, meta)
        spec = []
        for g in st['groups']:
            if g['name']:
                spec.append(('group', g['name']))
            for r in g['results']:
                sym, col = _result_cell(r.result)
                value = ''
                if r.is_measure:
                    unit = f' {_esc(r.unit)}' if r.unit else ''
                    value = f'{_esc(r.value_text or "-")}{unit} (zakres {_esc(r.limits_label)})'
                title = _esc(r.title) + (f'<br/><i>{_esc(r.notes)}</i>' if r.notes else '')
                spec.append(('row', title, sym, col, value))
        _points_table(story, spec, content_width)


def _initials(name):
    """'Jan Kowalski' -> 'J.K.' — dane montera w PDF nie mogą zawierać pełnego imienia i nazwiska."""
    parts = [p for p in name.split() if p]
    if not parts:
        return name
    return '.'.join(p[0].upper() for p in parts) + '.'


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


_BRIEFING_SECTION_COLORS = {
    'Podsumowanie':       PRIMARY,
    'Anomalie i trendy':  colors.HexColor('#b7791f'),
    'Priorytety':         SUCCESS,
}


def _md_inline(text):
    """'**bold**' / '*italic*' -> znaczniki obsługiwane natywnie przez reportlab Paragraph."""
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<i>\1</i>', text)
    return text


def _md_block_flowables(md_text, body_style, bullet_style):
    """Minimalny konwerter Markdown -> flowables reportlab: akapity + listy '- '/'* '."""
    flowables = []
    para_buf  = []

    def flush_para():
        if para_buf:
            joined = ' '.join(para_buf).strip()
            if joined:
                flowables.append(Paragraph(_md_inline(joined), body_style))
            para_buf.clear()

    for raw_line in md_text.strip().splitlines():
        line = raw_line.strip()
        if not line:
            flush_para()
            continue
        m = re.match(r'^[-*]\s+(.+)$', line)
        if m:
            flush_para()
            flowables.append(Paragraph('•&nbsp;&nbsp;' + _md_inline(m.group(1)), bullet_style))
        else:
            para_buf.append(line)
    flush_para()
    return flowables


def build_briefing_pdf_bytes(briefing):
    """Renderuje zapisany 'Poranny Briefing' (analiza AI) do bajtów PDF.
    Zwraca surowe bajty (nie odpowiedź Flask), żeby wołający mógł je zarówno
    zwrócić klientowi jak i zapisać na dysku pod stałą nazwą pliku."""
    _register_fonts()
    buf = BytesIO()
    period_title = f'Briefing Kontroli Jakości {briefing.start_date} – {briefing.end_date}'
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=period_title,
    )

    content_width = W - 2 * MARGIN
    story = []

    # ── Logo + tytuł (tabela 2-kolumnowa, jak w raporcie checklisty) ──────────
    logo_path = os.path.join(os.path.dirname(__file__), 'static', 'k_logo.jpg')
    logo_h = 20 * mm + 10
    logo_w = logo_h
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
        Paragraph('System RP - Raportowanie produkcji', _s(size=7, color=SECONDARY, after=2)),
        Paragraph('Briefing Kontroli Jakości (analiza AI)', _s(size=14, bold=True, color=PRIMARY, after=3)),
        Paragraph(f'Okres: {briefing.start_date.strftime("%d.%m.%Y")} – {briefing.end_date.strftime("%d.%m.%Y")}',
                  _s(size=8, color=SECONDARY, after=1)),
        Paragraph(f'Wygenerowano: {_ldt(briefing.generated_at)} przez {briefing.generated_by.username} '
                  f'| model: {briefing.model_used}',
                  _s(size=8, color=SECONDARY, after=1)),
    ]

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

    # ── Sekcje briefingu (## Podsumowanie / ## Anomalie i trendy / ## Priorytety) ──
    body_style   = _s(size=9.5, after=6)
    bullet_style = _s(size=9.5, after=4, indent=4 * mm)

    parts = re.split(r'(?m)^##\s+(.+)$', briefing.content)
    if parts[0].strip():
        story.extend(_md_block_flowables(parts[0], body_style, bullet_style))
        story.append(Spacer(1, 3 * mm))
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body  = parts[i + 1] if i + 1 < len(parts) else ''
        color = _BRIEFING_SECTION_COLORS.get(title, SECONDARY)
        story.append(Paragraph(title, _s(size=11.5, bold=True, color=color, after=6)))
        story.append(HRFlowable(width='25%', thickness=1, color=color, hAlign='LEFT',
                                spaceBefore=0, spaceAfter=4))
        story.extend(_md_block_flowables(body, body_style, bullet_style))
        story.append(Spacer(1, 5 * mm))

    # ── Stopka ───────────────────────────────────────────────────────────────
    story.append(HRFlowable(width='100%', thickness=0.5, color=colors.grey,
                            spaceBefore=4, spaceAfter=4))
    story.append(Paragraph(
        f'Wygenerowano: {_ldt(datetime.now(timezone.utc))} '
        f'| System RP - Raportowanie produkcji – Firma Kubiak',
        _s(size=7, color=colors.grey, align=1)))

    doc.build(story)
    buf.seek(0)
    return buf.read()
    return table
