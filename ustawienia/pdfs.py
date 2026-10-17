"""PDF: etykiety buforów (50x100 mm jak istniejące: QR, kod kreskowy, numer) i plakietki
pracowników działów (siatka naklejek A4 jak plakietki z Listy produkcyjnej)."""
import io

from .lookup import BADGE_PREFIX


def _fonts():
    from reportlab.pdfbase import pdfmetrics
    try:
        import pdf_generator
        pdf_generator._register_fonts()
        pdfmetrics.getFont('DVSans-Bold')
        return 'DVSans', 'DVSans-Bold'
    except Exception:
        return 'Helvetica', 'Helvetica-Bold'


def _draw_qr(c, text, x, y, size):
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF
    qr = QrCodeWidget(text)
    b = qr.getBounds()
    d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    d.add(qr)
    renderPDF.draw(d, c, x, y)


def buffer_labels_pdf(buffers):
    """Jedna strona 100x50 mm na bufor (drukarka etykiet): QR po lewej, kod dużą czcionką
    i kod kreskowy Code128 po prawej. Zawartość QR i kodu kreskowego to sam kod bufora."""
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.graphics.barcode import code128
    from reportlab.pdfbase import pdfmetrics

    font, bold = _fonts()
    buf = io.BytesIO()
    page_w, page_h = 100 * mm, 50 * mm
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    for i, b in enumerate(buffers):
        if i:
            c.showPage()
        qr_size = 38 * mm
        _draw_qr(c, b.code, 6 * mm, (page_h - qr_size) / 2, qr_size)

        area_x, area_w = 50 * mm, 46 * mm
        size = 34
        while pdfmetrics.stringWidth(b.code, bold, size) > area_w and size > 10:
            size -= 1
        c.setFillColorRGB(0, 0, 0)
        c.setFont(bold, size)
        c.drawCentredString(area_x + area_w / 2, 30 * mm, b.code)

        bar_w = 0.5 * mm
        bc = code128.Code128(b.code, barWidth=bar_w, barHeight=12 * mm, humanReadable=False)
        if bc.width > area_w:
            bc = code128.Code128(b.code, barWidth=bar_w * area_w / bc.width,
                                 barHeight=12 * mm, humanReadable=False)
        bc.drawOn(c, area_x + (area_w - bc.width) / 2, 9 * mm)
    c.save()
    buf.seek(0)
    return buf


def badges_pdf(employees):
    """Plakietki pracowników działów: siatka 2x5 na A4, QR z `PSH-DEP:<token>`, imię i dział."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics

    font, bold = _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    cw, ch = 90 * mm, 54 * mm
    cols, rows = 2, 5
    x0 = (page_w - cols * cw) / 2
    y0 = (page_h - rows * ch) / 2

    def fit(text, face, size, width):
        while pdfmetrics.stringWidth(text, face, size) > width and len(text) > 4:
            text = text[:-1]
        return text

    for i, emp in enumerate(employees):
        slot = i % (cols * rows)
        if i and slot == 0:
            c.showPage()
        x = x0 + (slot % cols) * cw
        y = page_h - y0 - (slot // cols + 1) * ch
        c.setStrokeColorRGB(.6, .6, .6)
        c.setDash(2, 2)
        c.rect(x, y, cw, ch)
        c.setDash()
        size = 38 * mm
        _draw_qr(c, BADGE_PREFIX + emp.token, x + 4 * mm, y + (ch - size) / 2, size)
        tx = x + 45 * mm
        text_w = cw - 49 * mm
        c.setFillColorRGB(0, 0, 0)
        c.setFont(bold, 13)
        words = emp.name.split()
        first, rest = (words[0], ' '.join(words[1:])) if len(words) > 1 else (emp.name, '')
        c.drawString(tx, y + ch - 20 * mm, fit(first, bold, 13, text_w))
        if rest:
            c.drawString(tx, y + ch - 26 * mm, fit(rest, bold, 13, text_w))
        c.setFont(font, 9)
        c.setFillColorRGB(.3, .3, .3)
        dept = emp.department.name if emp.department else ''
        c.drawString(tx, y + ch - 33 * mm, fit(dept, font, 9, text_w))
        c.setFont(font, 7)
        c.setFillColorRGB(.4, .4, .4)
        c.drawString(tx, y + 6 * mm, 'Skanuj przed wpisem etapu')
    c.save()
    buf.seek(0)
    return buf
