"""Generuje plik PDF z wytycznymi importu CSV zamówień."""
import os
from io import BytesIO
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# ── Kolory ──────────────────────────────────────────────────────────────────
PRIMARY   = colors.HexColor('#1a5276')
ACCENT    = colors.HexColor('#1a6e8a')
SUCCESS   = colors.HexColor('#27ae60')
WARNING   = colors.HexColor('#e67e22')
ERROR     = colors.HexColor('#c0392b')
BG_HEAD   = colors.HexColor('#1a5276')
BG_ALT    = colors.HexColor('#eaf4fb')
BG_REQ    = colors.HexColor('#fef9e7')
BORDER    = colors.HexColor('#aed6f1')
DARK      = colors.HexColor('#1a1a1a')
WHITE     = colors.white
LIGHT_GREY= colors.HexColor('#f5f6fa')
CODE_BG   = colors.HexColor('#f0f3f4')

W, H   = A4
MARGIN = 18 * mm

# ── Czcionki ─────────────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_FONT_SEARCH = [
    os.path.join(_BASE_DIR, 'fonts'),
    'C:/Windows/Fonts',
    '/usr/share/fonts/truetype/dejavu',
]

def _register_fonts():
    for d in _FONT_SEARCH:
        ttf = os.path.join(d, 'DejaVuSans.ttf')
        if os.path.isfile(ttf):
            pdfmetrics.registerFont(TTFont('DVSans',       os.path.join(d, 'DejaVuSans.ttf')))
            pdfmetrics.registerFont(TTFont('DVSans-Bold',  os.path.join(d, 'DejaVuSans-Bold.ttf')))
            pdfmetrics.registerFontFamily('DVSans', normal='DVSans', bold='DVSans-Bold')
            return
    raise RuntimeError(f'Brak czcionki DejaVuSans.ttf — skopiuj pliki .ttf do: {_FONT_SEARCH[0]}')

def s(size=9, bold=False, color=DARK, align=TA_LEFT, leading=None, indent=0, space_after=2):
    return ParagraphStyle(
        name=f'_s_{size}_{bold}',
        fontName='DVSans-Bold' if bold else 'DVSans',
        fontSize=size,
        textColor=color,
        alignment=align,
        leading=leading or size * 1.35,
        leftIndent=indent,
        spaceAfter=space_after,
    )

def mono(size=8, color=DARK):
    return ParagraphStyle(
        name=f'_mono_{size}_{id(color)}',
        fontName='DVSans-Bold',
        fontSize=size,
        textColor=color,
        leading=size * 1.4,
        spaceAfter=1,
    )

# ── Pomocnik tabeli ──────────────────────────────────────────────────────────
def tbl_style(header_color=BG_HEAD, alt_color=BG_ALT):
    return TableStyle([
        ('BACKGROUND',  (0, 0), (-1,  0), header_color),
        ('TEXTCOLOR',   (0, 0), (-1,  0), WHITE),
        ('FONTNAME',    (0, 0), (-1,  0), 'DVSans-Bold'),
        ('FONTSIZE',    (0, 0), (-1,  0), 9),
        ('ALIGN',       (0, 0), (-1,  0), 'CENTER'),
        ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME',    (0, 1), (-1, -1), 'DVSans'),
        ('FONTSIZE',    (0, 1), (-1, -1), 8.5),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, alt_color]),
        ('GRID',        (0, 0), (-1, -1), 0.4, BORDER),
        ('TOPPADDING',  (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING',(0, 0), (-1, -1), 6),
    ])

# ── Budowanie dokumentu ──────────────────────────────────────────────────────
def build_pdf(output_path: str):
    _register_fonts()

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
        title='Wytyczne importu CSV — Zamówienia',
        author='PSH QC System',
    )

    CW = W - 2 * MARGIN  # szerokość kolumny treści

    story = []

    # ── Nagłówek ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph('PSH QC — System kontroli jakości', s(9, color=colors.HexColor('#7f8c8d'), align=TA_CENTER)))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph('Wytyczne importu zamówień z pliku CSV', s(20, bold=True, color=PRIMARY, align=TA_CENTER, space_after=4)))
    story.append(Paragraph(
        f'Dokument wygenerowany: {datetime.now().strftime("%d.%m.%Y %H:%M")}',
        s(8, color=colors.HexColor('#95a5a6'), align=TA_CENTER)
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width=CW, thickness=2, color=PRIMARY, spaceAfter=6 * mm))

    # ── 1. Format pliku ───────────────────────────────────────────────────────
    story.append(Paragraph('1. Format pliku', s(13, bold=True, color=PRIMARY, space_after=4)))
    story.append(Spacer(1, 1 * mm))

    fmt_data = [
        [Paragraph('Parametr', s(9, bold=True, color=WHITE)),
         Paragraph('Wymagana wartość', s(9, bold=True, color=WHITE))],
        [Paragraph('Separator kolumn', s(9)),  Paragraph('Średnik  ;', s(9, bold=True))],
        [Paragraph('Kodowanie znaków', s(9)),  Paragraph('UTF-8, UTF-8 z BOM lub CP1250 (Windows)', s(9))],
        [Paragraph('Rozszerzenie pliku', s(9)), Paragraph('.csv', s(9, bold=True))],
        [Paragraph('Pierwszy wiersz', s(9)),   Paragraph('Nagłówki kolumn (wymagane)', s(9))],
    ]
    fmt_tbl = Table(fmt_data, colWidths=[CW * 0.38, CW * 0.62])
    fmt_tbl.setStyle(tbl_style())
    story.append(fmt_tbl)
    story.append(Spacer(1, 5 * mm))

    # ── 2. Kolumny ────────────────────────────────────────────────────────────
    story.append(Paragraph('2. Wymagane kolumny', s(13, bold=True, color=PRIMARY, space_after=4)))
    story.append(Paragraph(
        'Nazwy kolumn w pliku CSV muszą być dokładnie takie jak poniżej (wielkość liter ma znaczenie).',
        s(9, color=colors.HexColor('#555555'), space_after=3)
    ))
    story.append(Spacer(1, 1 * mm))

    def req(text):
        return Paragraph(text, s(8, bold=True, color=ERROR))

    def opt(text):
        return Paragraph(text, s(8, color=SUCCESS))

    col_data = [
        [Paragraph('Nazwa kolumny', s(9, bold=True, color=WHITE)),
         Paragraph('Wymagana', s(9, bold=True, color=WHITE)),
         Paragraph('Opis', s(9, bold=True, color=WHITE))],

        [Paragraph('Numer wew.', mono(9)),
         req('TAK'),
         Paragraph('Wewnętrzny numer zamówienia. Gdy ten sam numer pojawia się kilka razy, '
                   'system uzupełnia go o Lp (np. ZAM-001-2).', s(8))],

        [Paragraph('Lp', mono(9)),
         opt('nie'),
         Paragraph('Liczba porządkowa wiersza — używana do rozróżnienia zamówień '
                   'o tym samym numerze wewnętrznym.', s(8))],

        [Paragraph('Identyfikator', mono(9)),
         req('TAK'),
         Paragraph('Nazwa / symbol produktu. Na jej podstawie system automatycznie '
                   'dobiera szablon kontroli QA i montażu.', s(8))],

        [Paragraph('Kontrahent', mono(9)),
         req('TAK'),
         Paragraph('Pełna nazwa klienta / kontrahenta.', s(8))],

        [Paragraph('Ilość', mono(9)),
         opt('nie'),
         Paragraph('Liczba sztuk (domyślnie: 1). Akceptuje przecinek lub kropkę '
                   'jako separator dziesiętny.', s(8))],

        [Paragraph('Termin dostawy', mono(9)),
         opt('nie'),
         Paragraph('Data dostawy. Akceptowane formaty:\n'
                   '• RRRR-MM-DD  (np. 2026-06-30)\n'
                   '• DD.MM.RRRR  (np. 30.06.2026)\n'
                   '• DD-MM-RRRR  (np. 30-06-2026)', s(8))],

        [Paragraph('Numer zew.', mono(9)),
         opt('nie'),
         Paragraph('Zewnętrzny numer zamówienia (np. z systemu klienta / ERP). '
                   'Zapisywany jako notatki do zamówienia.', s(8))],
    ]

    col_style = tbl_style()
    col_style.add('VALIGN', (0, 0), (-1, -1), 'TOP')
    col_style.add('BACKGROUND', (1, 2), (1, 2), colors.HexColor('#fdf2f2'))
    col_style.add('BACKGROUND', (1, 4), (1, 4), colors.HexColor('#fdf2f2'))
    col_style.add('BACKGROUND', (1, 5), (1, 5), colors.HexColor('#fdf2f2'))

    col_tbl = Table(col_data, colWidths=[CW * 0.22, CW * 0.13, CW * 0.65])
    col_tbl.setStyle(col_style)
    story.append(col_tbl)

    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        '<font color="#c0392b">■</font>  Wymagana — brak tej kolumny lub pustej wartości powoduje pominięcie wiersza.',
        s(8, color=DARK)
    ))
    story.append(Paragraph(
        '<font color="#27ae60">■</font>  Opcjonalna — można zostawić pustą.',
        s(8, color=DARK)
    ))
    story.append(Spacer(1, 5 * mm))

    # ── 3. Przykład ───────────────────────────────────────────────────────────
    story.append(Paragraph('3. Przykład poprawnego pliku', s(13, bold=True, color=PRIMARY, space_after=4)))
    story.append(Spacer(1, 1 * mm))

    example_lines = [
        'Lp;Numer wew.;Identyfikator;Kontrahent;Ilość;Termin dostawy;Numer zew.',
        '1;ZAM-001;RU-200 PRAWA;ABC Sp. z o.o.;10;30.06.2026;EXT-9901',
        '2;ZAM-002;RU-200 LEWA;ABC Sp. z o.o.;5;15.07.2026;',
        '3;ZAM-002;RZ-100;XYZ S.A.;20;2026-07-20;EXT-9902',
        '4;ZAM-003;RU-300;;8;;',
    ]

    code_rows = []
    for i, line in enumerate(example_lines):
        fg = WHITE if i == 0 else DARK
        code_rows.append([Paragraph(line, ParagraphStyle(
            name=f'code_{i}',
            fontName='DVSans-Bold',
            fontSize=7.5,
            textColor=fg,
            leading=11,
        ))])

    code_tbl = Table(code_rows, colWidths=[CW])
    code_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3a4a')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, CODE_BG]),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cccccc')),
        ('LEFTPADDING',  (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING',   (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(code_tbl)

    story.append(Spacer(1, 2 * mm))

    notes_data = [
        [Paragraph('Uwaga', s(8, bold=True, color=WARNING)),
         Paragraph('Wiersz 3 i 4 mają ten sam Numer wew. (ZAM-002) — system '
                   'utworzy ZAM-002-3 i ZAM-002-4 używając kolumny Lp.', s(8))],
        [Paragraph('Uwaga', s(8, bold=True, color=WARNING)),
         Paragraph('Wiersz 5 (ZAM-003) ma pustą kolumnę Kontrahent — zostanie '
                   'oznaczony jako błąd i pominięty podczas importu.', s(8))],
    ]
    notes_tbl = Table(notes_data, colWidths=[CW * 0.12, CW * 0.88])
    notes_tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, -1), colors.HexColor('#fef9e7')),
        ('GRID',         (0, 0), (-1, -1), 0.4, colors.HexColor('#f0d080')),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',   (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
        ('LEFTPADDING',  (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(notes_tbl)
    story.append(Spacer(1, 5 * mm))

    # ── 4. Zachowanie systemu ─────────────────────────────────────────────────
    story.append(Paragraph('4. Zachowanie systemu po imporcie', s(13, bold=True, color=PRIMARY, space_after=4)))
    story.append(Spacer(1, 1 * mm))

    behavior_data = [
        [Paragraph('Sytuacja', s(9, bold=True, color=WHITE)),
         Paragraph('Działanie systemu', s(9, bold=True, color=WHITE))],
        [Paragraph('Wiersz poprawny, nowe zamówienie', s(9)),
         Paragraph('Tworzone jest zamówienie + automatycznie generowane listy kontrolne '
                   '(QA i montaż) jeśli znaleziono pasujący szablon.', s(9))],
        [Paragraph('Zamówienie o tym numerze już istnieje', s(9)),
         Paragraph('Wiersz oznaczony jako duplikat — pomijany przy imporcie.', s(9))],
        [Paragraph('Brak wymaganego pola (numer / produkt / klient)', s(9)),
         Paragraph('Wiersz oznaczony jako błąd — pomijany przy imporcie.', s(9))],
        [Paragraph('Brak pasującego szablonu do produktu', s(9)),
         Paragraph('Zamówienie jest tworzone, ale bez listy kontrolnej. '
                   'Listę można przypisać ręcznie z poziomu szczegółów zamówienia.', s(9))],
        [Paragraph('Podgląd przed importem', s(9)),
         Paragraph('Po wgraniu pliku wyświetlany jest podgląd wszystkich wierszy. '
                   'Można odznaczyć wybrane pozycje przed zatwierdzeniem importu.', s(9))],
    ]
    beh_tbl = Table(behavior_data, colWidths=[CW * 0.38, CW * 0.62])
    beh_style = tbl_style()
    beh_style.add('VALIGN', (0, 0), (-1, -1), 'TOP')
    beh_tbl.setStyle(beh_style)
    story.append(beh_tbl)
    story.append(Spacer(1, 5 * mm))

    # ── 5. Najczęstsze błędy ──────────────────────────────────────────────────
    story.append(Paragraph('5. Najczęstsze błędy', s(13, bold=True, color=PRIMARY, space_after=4)))
    story.append(Spacer(1, 1 * mm))

    errors_data = [
        [Paragraph('Problem', s(9, bold=True, color=WHITE)),
         Paragraph('Rozwiązanie', s(9, bold=True, color=WHITE))],
        [Paragraph('Polskie znaki wyświetlają się nieprawidłowo', s(9)),
         Paragraph('Zapisz plik w kodowaniu UTF-8 lub UTF-8 z BOM '
                   '(w Excelu: Plik → Zapisz jako → CSV UTF-8 z BOM).', s(9))],
        [Paragraph('Kolumny nie są rozpoznawane', s(9)),
         Paragraph('Sprawdź separator — musi to być średnik (;), '
                   'nie przecinek ani tabulator.', s(9))],
        [Paragraph('Data nie jest importowana', s(9)),
         Paragraph('Upewnij się, że format daty to DD.MM.RRRR, RRRR-MM-DD '
                   'lub DD-MM-RRRR. Inne formaty są ignorowane.', s(9))],
        [Paragraph('Brak list kontrolnych po imporcie', s(9)),
         Paragraph('Nazwa produktu (Identyfikator) nie pasuje do żadnego '
                   'szablonu. Sprawdź nazwy szablonów w panelu administratora.', s(9))],
    ]
    err_tbl = Table(errors_data, colWidths=[CW * 0.38, CW * 0.62])
    err_style = tbl_style(header_color=colors.HexColor('#922b21'))
    err_style.add('VALIGN', (0, 0), (-1, -1), 'TOP')
    err_tbl.setStyle(err_style)
    story.append(err_tbl)
    story.append(Spacer(1, 8 * mm))

    # ── Stopka ────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width=CW, thickness=1, color=colors.HexColor('#cccccc'), spaceBefore=2))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        'PSH QC System  ·  Import CSV  ·  '
        f'Wygenerowano: {datetime.now().strftime("%d.%m.%Y")}',
        s(7.5, color=colors.HexColor('#95a5a6'), align=TA_CENTER)
    ))

    doc.build(story)

    with open(output_path, 'wb') as f:
        f.write(buf.getvalue())
    print(f'PDF zapisany: {output_path}')


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wytyczne_import_csv.pdf')
    build_pdf(out)
