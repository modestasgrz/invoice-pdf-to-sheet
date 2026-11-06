import os
from datetime import date, datetime
from pathlib import Path

from loguru import logger
from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill

from invoice_pdf_to_sheet.schema import COLUMNS, SEPARATOR, ResultRow

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


# a hand-edited cell may hold a number or a typed date where the row model wants text
TEXT = {
    "file",
    "supplier_name",
    "supplier_vat_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "evidence",
    "reasons",
}


def inside(pdf_dir: Path, name: str) -> Path:
    """The PDF for a file name taken from a (maybe hand-edited) xlsx: a plain name inside pdf_dir, else pdf_dir itself,
    which is not a file, so nothing outside the folder is ever uploaded."""
    candidate = pdf_dir / name
    if Path(name).name != name or "\\" in name or not name.lower().endswith(".pdf"):
        logger.warning("xlsx row with an unsafe file name: not uploaded")
        return pdf_dir
    if not candidate.resolve().is_relative_to(pdf_dir.resolve()):
        logger.warning("xlsx row pointing outside the PDF folder: not uploaded")
        return pdf_dir
    return candidate


def read_xlsx(path: Path, pdf_dir: Path) -> tuple[list[ResultRow], list[ResultRow]]:
    """Rows of the Ready and Needs review tabs of an xlsx written by write_xlsx, each pointing at its PDF in pdf_dir."""
    book = load_workbook(path)
    tabs = []
    for name in ("Ready", "Needs review"):
        rows = [
            [
                v.date().isoformat()
                if isinstance(v, datetime)
                else v.isoformat()
                if isinstance(v, date)
                else str(v)
                if column in TEXT and v is not None
                else v
                for column, v in zip(COLUMNS, cells, strict=False)
            ]
            for cells in book[name].iter_rows(values_only=True)
            if any(v is not None for v in cells)
        ]
        tabs.append(
            [
                ResultRow.model_validate(
                    {
                        **{
                            k: v
                            for k, v in zip(COLUMNS[:-1], cells, strict=False)
                            if v is not None
                        },
                        "evidence": cells[COLUMNS.index("evidence")] or "",
                        "reasons": str(cells[COLUMNS.index("reasons")]).split(SEPARATOR)
                        if cells[COLUMNS.index("reasons")]
                        else [],
                        "pdf_path": inside(pdf_dir, str(cells[0])),
                    }
                )
                for cells in rows[1:]
            ]
        )
    return tabs[0], tabs[1]
