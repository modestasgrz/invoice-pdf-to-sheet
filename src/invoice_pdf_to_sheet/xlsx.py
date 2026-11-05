import os
from pathlib import Path

from openpyxl import Workbook

from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow


def write_xlsx(ready: list[ResultRow], review: list[ResultRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    first = book.worksheets[0]
    first.title = "Ready"
    for sheet, rows in ((first, ready), (book.create_sheet("Needs review"), review)):
        sheet.append(COLUMNS)
        for row in rows:
            sheet.append([*row.cells(), row.file])
            link = sheet.cell(row=sheet.max_row, column=len(COLUMNS))
            link.hyperlink = os.path.relpath(
                row.pdf_path.resolve(), path.parent.resolve()
            )
            link.style = "Hyperlink"
    book.save(path)
