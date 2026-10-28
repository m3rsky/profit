"""Karta kontroli zlecenia w PDF (ReportLab, DejaVu Sans jak inne PDF w profit): trasa, wszystkie kontrole z punktami,
wartościami, uwagami i zdjęciami przy niezgodnościach oraz zawroty i zlecenia naprawcze."""
import os
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import clock
from .pdf import _fonts
from .reports_data import to_local
from .repairs import DECISIONS, RETURN_STATUS_LABELS

NAVY = colors.HexColor('#1a3a5c')
LIGHT = colors.HexColor('#eaf2f8')
REDBG = colors.HexColor('#fdecea')
GRID = colors.HexColor('#cfd8e3')
LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'k_logo.jpg')
OP_STATUS = {'oczekuje': 'Oczekuje', 'do_kontroli': 'Do kontroli', 'zakonczona': 'Zakończona'}


def _styles():
    _fonts()
    return {
        'body': ParagraphStyle('pkjb', fontName='PkjSans', fontSize=8.5, leading=11),
        'small': ParagraphStyle('pkjs', fontName='PkjSans', fontSize=7.5, leading=9.5,
                                textColor=colors.HexColor('#475569')),
        'bold': ParagraphStyle('pkjbb', fontName='PkjSans-Bold', fontSize=8.5, leading=11),
        'h1': ParagraphStyle('pkjh1', fontName='PkjSans-Bold', fontSize=17, leading=21, textColor=NAVY),
        'h2': ParagraphStyle('pkjh2', fontName='PkjSans-Bold', fontSize=11, leading=14, textColor=NAVY,
                             spaceBefore=8, spaceAfter=3),
        'th': ParagraphStyle('pkjth', fontName='PkjSans-Bold', fontSize=8, leading=10, textColor=colors.white),
    }


def _local(dt, fmt='%d.%m.%Y %H:%M'):
    return to_local(dt).strftime(fmt) if dt else '-'


def _value_text(r):
    if r.value is None:
        return '-'
    text = f'{r.value:g}' + (f' {r.unit}' if r.unit else '')
    if r.min_value is not None or r.max_value is not None:
        lo = f'{r.min_value:g}' if r.min_value is not None else '...'
        hi = f'{r.max_value:g}' if r.max_value is not None else '...'
        text += f' (zakres {lo} do {hi})'
    return text.replace('.', ',')


def order_card_pdf(card, upload_dir):
    st = _styles()
    order = card['order']
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm,
                            bottomMargin=16 * mm, title=f'Karta kontroli {order.number}')
    width = A4[0] - 28 * mm
    story = []

    logo = RLImage(LOGO, width=24 * mm, height=12 * mm, kind='proportional') if os.path.isfile(LOGO) else ''
    head = Table([[Paragraph('Karta kontroli zlecenia', st['h1']), logo],
                  [Paragraph(f'{order.number} · {order.product or ""}', st['bold']), '']],
                 colWidths=[width - 30 * mm, 30 * mm])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('ALIGN', (1, 0), (1, 0), 'RIGHT')]))
    story += [head, HRFlowable(width='100%', thickness=1, color=NAVY, spaceBefore=3, spaceAfter=6)]

    info = Table([[Paragraph('Klient', st['small']), Paragraph(order.client or '-', st['body']),
                   Paragraph('Ilość', st['small']), Paragraph(f'{order.quantity or 1} szt.', st['body'])],
                  [Paragraph('Utworzono', st['small']), Paragraph(_local(order.created_at), st['body']),
                   Paragraph('Wydruk', st['small']), Paragraph(_local(clock.now()), st['body'])]],
                 colWidths=[24 * mm, width / 2 - 24 * mm, 24 * mm, width / 2 - 24 * mm])
    info.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), LIGHT), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 3), ('TOPPADDING', (0, 0), (-1, -1), 3)]))
    story.append(info)

    story.append(Paragraph('Trasa operacji', st['h2']))
    rows = [[Paragraph(h, st['th']) for h in ('#', 'Operacja', 'KJ', 'Status', 'Zameldowano', 'Wykonawca')]]
    for o in card['ops']:
        rows.append([Paragraph(str(o.position), st['body']), Paragraph(o.operation_name, st['body']),
                     Paragraph('tak' if o.kj_required else '-', st['body']),
                     Paragraph(OP_STATUS.get(o.status, o.status), st['body']),
                     Paragraph(_local(o.reported_at), st['body']), Paragraph(o.employee_name or '-', st['body'])])
    t = Table(rows, colWidths=[8 * mm, 40 * mm, 12 * mm, 28 * mm, 36 * mm, width - 124 * mm], repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), NAVY), ('GRID', (0, 0), (-1, -1), 0.4, GRID),
                           ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
    story.append(t)

    if not card['blocks']:
        story += [Spacer(1, 6), Paragraph('To zlecenie nie ma jeszcze żadnych kontroli KJ.', st['body'])]
    for n, block in enumerate(card['blocks'], 1):
        insp = block['insp']
        label = 'Kontrola sprawdzająca' if insp.round > 1 else 'Kontrola'
        verdict = {'zakonczona': insp.result or '-', 'przerwana': 'PRZERWANA', 'w_toku': 'W TOKU'}[insp.status]
        story.append(Paragraph(f'{n}. {label}: {block["op"]} ({verdict})', st['h2']))
        meta = (f'Kontroler: {insp.inspector_name or "-"} · Wykonawca: {insp.performer_name or "-"} · '
                f'Start: {_local(insp.started_at)} · Koniec: {_local(insp.finished_at)} · Materiały: '
                + ', '.join(f'{m.name} ({m.verdict or "-"})' for m in block['materials']))
        story += [Paragraph(meta, st['small']), Spacer(1, 3)]
        data = [[Paragraph(h, st['th']) for h in ('Punkt', 'Wynik', 'Wartość', 'Uwagi')]]
        style = [('BACKGROUND', (0, 0), (-1, 0), NAVY), ('GRID', (0, 0), (-1, -1), 0.4, GRID),
                 ('VALIGN', (0, 0), (-1, -1), 'TOP')]
        thumbs = []
        for sec in block['sections']:
            data.append([Paragraph(sec['name'], st['bold']), '', '', ''])
            style += [('SPAN', (0, len(data) - 1), (-1, len(data) - 1)),
                      ('BACKGROUND', (0, len(data) - 1), (-1, len(data) - 1), LIGHT)]
            for r in sec['results']:
                data.append([Paragraph(r.title, st['body']), Paragraph(r.result or '-', st['bold']),
                             Paragraph(_value_text(r), st['body']), Paragraph(r.notes or '', st['body'])])
                if r.result == 'NOK':
                    style.append(('BACKGROUND', (0, len(data) - 1), (-1, len(data) - 1), REDBG))
                    for ph in card['photos'].get(r.id, []):
                        path = os.path.join(upload_dir, os.path.basename(ph.filename))
                        if os.path.isfile(path):
                            thumbs.append((r.title, path))
        t = Table(data, colWidths=[72 * mm, 16 * mm, 46 * mm, width - 134 * mm], repeatRows=1)
        t.setStyle(TableStyle(style))
        story.append(t)
        cells = []
        for title, path in thumbs[:6]:
            try:
                cells.append((RLImage(path, width=38 * mm, height=26 * mm, kind='proportional'),
                              Paragraph(title[:40], st['small'])))
            except Exception:
                continue
        if cells:
            story += [Spacer(1, 3), Paragraph('Zdjęcia przy niezgodnościach', st['small'])]
            tbl = Table([[c[0] for c in cells], [c[1] for c in cells]], colWidths=[42 * mm] * len(cells))
            tbl.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
            story.append(tbl)

    if card['returns']:
        story.append(Paragraph('Zawroty i zlecenia naprawcze', st['h2']))
        rows = [[Paragraph(h, st['th']) for h in ('Materiał', 'Przyczyna', 'Decyzja', 'Zlecenie naprawcze', 'Status')]]
        for r in card['returns']:
            zn = card['zns'].get(r.repair_order_id)
            rows.append([Paragraph(card['material_names'].get(r.order_material_id, '-'), st['body']),
                         Paragraph(r.reason_name or '-', st['body']),
                         Paragraph(DECISIONS.get(r.decision, ''), st['body']),
                         Paragraph(zn.number if zn else '-', st['body']),
                         Paragraph(RETURN_STATUS_LABELS.get(r.status, r.status), st['body'])])
        t = Table(rows, colWidths=[34 * mm, 48 * mm, 24 * mm, 46 * mm, width - 152 * mm], repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), NAVY), ('GRID', (0, 0), (-1, -1), 0.4, GRID)]))
        story.append(t)

    def footer(canvas_, doc_):
        canvas_.saveState()
        canvas_.setFont('PkjSans', 7)
        canvas_.setFillColor(colors.HexColor('#64748b'))
        canvas_.drawString(14 * mm, 9 * mm, 'KJ Prestiż (pogląd), dane demonstracyjne')
        canvas_.drawRightString(A4[0] - 14 * mm, 9 * mm, f'Strona {doc_.page}')
        canvas_.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
