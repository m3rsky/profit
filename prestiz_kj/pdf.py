"""PDF modułu KJ Prestiż: etykieta ze skanowalnym kodem QR zlecenia (ReportLab, DejaVu Sans jak inne PDF w profit)."""
import json
import os
from io import BytesIO

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fonts')
_FONTS = False


def _fonts():
    global _FONTS
    if not _FONTS:
        pdfmetrics.registerFont(TTFont('PkjSans', os.path.join(FONT_DIR, 'DejaVuSans.ttf')))
        pdfmetrics.registerFont(TTFont('PkjSans-Bold', os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf')))
        _FONTS = True


def qr_payload(order):
    """Treść kodu QR w formacie etykiet profit (pola p, c, o, q), czytana też przez Kontrolę etapową."""
    return json.dumps({'p': order.product or '', 'c': order.client or '', 'o': order.number,
                       'q': order.quantity or 1}, ensure_ascii=False)


def _fit(text, font, size, width):
    while len(text) > 3 and pdfmetrics.stringWidth(text, font, size) > width:
        text = text[:-2].rstrip() + '…'
    return text


def order_label_pdf(order):
    """Etykieta A6 pionowo: duży QR, numer, produkt, klient, ilość."""
    _fonts()
    buf = BytesIO()
    w, h = 105 * mm, 148 * mm
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(f'Etykieta {order.number}')
    size = 80 * mm
    widget = QrCodeWidget(qr_payload(order))
    x0, y0, x1, y1 = widget.getBounds()
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(widget)
    renderPDF.draw(d, c, (w - size) / 2, h - size - 14 * mm)
    inner = w - 16 * mm
    c.setFont('PkjSans-Bold', 17)
    c.drawCentredString(w / 2, h - size - 24 * mm, _fit(order.number, 'PkjSans-Bold', 17, inner))
    c.setFont('PkjSans', 11)
    c.drawCentredString(w / 2, h - size - 32 * mm, _fit(order.product or '', 'PkjSans', 11, inner))
    c.drawCentredString(w / 2, h - size - 38 * mm, _fit(order.client or '', 'PkjSans', 11, inner))
    c.drawCentredString(w / 2, h - size - 44 * mm, f'Ilość: {order.quantity or 1} szt.')
    c.setFont('PkjSans', 7)
    c.drawCentredString(w / 2, 6 * mm, 'KJ Prestiż (pogląd), etykieta demonstracyjna')
    c.showPage()
    c.save()
    return buf.getvalue()
