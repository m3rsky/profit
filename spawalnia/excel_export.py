from io import BytesIO
from datetime import datetime

from models import INTERSTAGE_STAGES

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

C_HEADER = 'FF1A5276'
C_SUBHEAD = 'FF1A6E8A'
C_OK     = 'FF1E8449'
C_NG     = 'FFC0392B'
C_GRAY   = 'FFF2F3F4'
C_WHITE  = 'FFFFFFFF'
FMT_NUM  = '0.00'


def _border():
    s = Side(style='thin', color='FFB2BABB')
    return Border(left=s, right=s, top=s, bottom=s)


def generate_excel(records, zo_filter='') -> BytesIO:
    if not HAS_OPENPYXL:
        raise RuntimeError('Biblioteka openpyxl nie jest zainstalowana.')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Kontrola etapów'

    n_cols = 1 + 2 * len(INTERSTAGE_STAGES) + 2
    col_widths = [18] + [12, 20] * len(INTERSTAGE_STAGES) + [22, 14]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    def cell(row, col, value=None, bold=False, bg=None, fg='FF000000',
             fmt=None, align='center'):
        c = ws.cell(row=row, column=col, value=value)
        c.font = Font(bold=bold, color=fg, size=10)
        if bg:
            c.fill = PatternFill('solid', fgColor=bg)
        c.alignment = Alignment(horizontal=align, vertical='center', wrap_text=True)
        if fmt:
            c.number_format = fmt
        c.border = _border()
        return c

    # ── Nagłówek dokumentu ────────────────────────────────────────────────────
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    title_cell = ws['A1']
    title_cell.value = 'KONTROLA MIĘDZYETAPOWA'
    title_cell.font = Font(bold=True, color='FFFFFFFF', size=13)
    title_cell.fill = PatternFill('solid', fgColor=C_HEADER)
    title_cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 24

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=6)
    sub = ws['A2']
    sub.value = f'Filtr ZO: {zo_filter}' if zo_filter else 'Wszystkie rekordy'
    sub.font = Font(color='FFFFFFFF', size=9)
    sub.fill = PatternFill('solid', fgColor=C_SUBHEAD)
    sub.alignment = Alignment(horizontal='left', vertical='center')
    ws.merge_cells(start_row=2, start_column=7, end_row=2, end_column=n_cols)
    date_cell = ws['G2']
    date_cell.value = datetime.now().strftime('%d.%m.%Y %H:%M')
    date_cell.font = Font(color='FFFFFFFF', size=9)
    date_cell.fill = PatternFill('solid', fgColor=C_SUBHEAD)
    date_cell.alignment = Alignment(horizontal='right', vertical='center')
    ws.row_dimensions[2].height = 18

    # ── Nagłówki kolumn ───────────────────────────────────────────────────────
    headers = ['NR ZO']
    for st in INTERSTAGE_STAGES:
        headers += [st['label'], 'OSOBA']
    headers += ['QAR', 'DATA']
    for col, h in enumerate(headers, 1):
        cell(4, col, h, bold=True, bg=C_SUBHEAD, fg='FFFFFFFF')
    ws.row_dimensions[4].height = 30

    # ── Dane ──────────────────────────────────────────────────────────────────
    def ok_ng_fg(val):
        if val == 'OK':
            return C_OK
        if val == 'NG':
            return C_NG
        return 'FF555555'

    for i, rec in enumerate(records):
        row = 5 + i
        bg = C_WHITE if i % 2 == 0 else C_GRAY
        cmap = rec.check_map

        cell(row, 1, rec.zo_number, bold=True, bg=bg, align='left')
        col = 2
        for st in INTERSTAGE_STAGES:
            chk = cmap.get(st['key'])
            res = chk.result if chk else None
            c = cell(row, col, res or '—', bg=bg, fg=ok_ng_fg(res))
            c.font = Font(bold=True, color=ok_ng_fg(res), size=10)
            cell(row, col + 1, chk.employee.name if chk and chk.employee else '—', bg=bg)
            col += 2
        qars = ', '.join(c.qar_report.number for c in rec.checks if c.qar_report)
        cell(row, col, qars or '—', bg=bg)
        cell(row, col + 1, rec.created_at.strftime('%d.%m.%Y') if rec.created_at else '—', bg=bg)

    # ── Stopka ────────────────────────────────────────────────────────────────
    footer_row = 5 + len(records) + 1
    ws.merge_cells(start_row=footer_row, start_column=1, end_row=footer_row, end_column=n_cols)
    fc = ws[f'A{footer_row}']
    fc.value = (f'Wygenerowano: {datetime.now().strftime("%d.%m.%Y %H:%M")}  |  '
                f'Liczba rekordów: {len(records)}')
    fc.font = Font(italic=True, color='FF888888', size=8)
    fc.alignment = Alignment(horizontal='left', vertical='center')

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
