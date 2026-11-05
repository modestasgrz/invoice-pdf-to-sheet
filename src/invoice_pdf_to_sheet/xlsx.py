import os
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill

from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow

WIDTHS = {
    "file": 30,
    "supplier_name": 34,
    "supplier_vat_id": 16,
    "invoice_number": 16,
    "invoice_date": 12,
    "due_date": 12,
    "currency": 9,
    "net_total": 12,
    "vat_total": 12,
    "gross_total": 12,
    "line_count": 10,
    "method": 9,
    "confidence": 12,
    "evidence": 60,
    "reasons": 48,
    "pdf_link": 30,
}
MONEY = {"net_total", "vat_total", "gross_total"}


def write_xlsx(ready: list[ResultRow], review: list[ResultRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    first = book.worksheets[0]
    first.title = "Ready"
    for sheet, rows in ((first, ready), (book.create_sheet("Needs review"), review)):
        sheet.append(COLUMNS)
        for row in rows:
            sheet.append(
                [
                    ILLEGAL_CHARACTERS_RE.sub("", value)
                    if isinstance(value, str)
                    else value
                    for value in [*row.cells(), row.file]
                ]
            )
            for cell in sheet[sheet.max_row]:
                if cell.data_type == "f":
                    cell.data_type = "s"
            link = sheet.cell(row=sheet.max_row, column=len(COLUMNS))
            link.hyperlink = os.path.relpath(
                row.pdf_path.resolve(), path.parent.resolve()
            )
            link.style = "Hyperlink"
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(
                "solid", fgColor="1F2937" if sheet.title == "Ready" else "B45309"
            )
            cell.alignment = Alignment(vertical="center")
        for index, column in enumerate(COLUMNS, start=1):
            letter = sheet.cell(row=1, column=index).column_letter
            sheet.column_dimensions[letter].width = WIDTHS[column]
            for cell in sheet[letter][1:]:
                if column in MONEY:
                    cell.number_format = "#,##0.00"
                if column in ("evidence", "reasons"):
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.freeze_panes = "B2"
        sheet.auto_filter.ref = sheet.dimensions
    book.save(path)
