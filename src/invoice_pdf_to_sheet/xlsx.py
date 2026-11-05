import os
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow


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
    book.save(path)
