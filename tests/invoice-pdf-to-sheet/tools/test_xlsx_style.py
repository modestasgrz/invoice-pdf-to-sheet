from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow
from invoice_pdf_to_sheet.xlsx import write_xlsx

EXPECTED_WIDTHS = {
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
MONEY = ("net_total", "vat_total", "gross_total")


def row(pdf: Path, **fields: Any) -> ResultRow:
    base: dict[str, Any] = {
        "supplier_name": "Verdant Ridge BV",
        "supplier_vat_id": "NL999000001B01",
        "invoice_number": "VR-1",
        "invoice_date": "2026-10-01",
        "due_date": "2026-10-31",
        "currency": "EUR",
        "net_total": 1234567.5,
        "vat_total": 0.5,
        "gross_total": 1234568.0,
        "line_count": 2,
        "method": "text",
        "confidence": "verified",
        "evidence": 'invoice_number p1: "VR-1"',
    }
    return ResultRow(file=pdf.name, pdf_path=pdf, **{**base, **fields})


def build(
    tmp_path: Path, n_ready: int = 2, n_review: int = 1
) -> tuple[Path, list[ResultRow], list[ResultRow]]:
    pdf_dir = tmp_path / "in"
    pdf_dir.mkdir(exist_ok=True)
    pdfs = []
    for i in range(n_ready + n_review):
        pdf = pdf_dir / f"f{i}.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        pdfs.append(pdf)
    ready = [row(p) for p in pdfs[:n_ready]]
    review = [
        row(p, reasons=["missing supplier VAT id", "not a readable PDF"])
        for p in pdfs[n_ready:]
    ]
    out = tmp_path / "out" / "invoices.xlsx"
    write_xlsx(ready, review, out)
    return out, ready, review


def column_of(name: str) -> int:
    return COLUMNS.index(name) + 1


def sheets(path: Path) -> list[Worksheet]:
    return list(load_workbook(path).worksheets)


def test_freeze_panes_keeps_header_row_and_first_column(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path)

    assert [s.freeze_panes for s in sheets(out)] == ["B2", "B2"]


def test_freeze_panes_also_set_on_an_empty_tab(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path, n_ready=1, n_review=0)

    assert [s.freeze_panes for s in sheets(out)] == ["B2", "B2"]
    assert sheets(out)[1].max_row == 1


def test_autofilter_covers_header_and_all_rows(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path, n_ready=3, n_review=2)

    ready, review = sheets(out)
    last = get_last_column_letter(ready)
    assert ready.auto_filter.ref == f"A1:{last}4"
    assert review.auto_filter.ref == f"A1:{last}3"


def test_autofilter_on_empty_tab_covers_the_header_row(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path, n_ready=0, n_review=1)

    ready, _ = sheets(out)
    assert ready.auto_filter.ref == f"A1:{get_last_column_letter(ready)}1"


def get_last_column_letter(sheet: Worksheet) -> str:
    return str(sheet.cell(row=1, column=len(COLUMNS)).column_letter)


@pytest.mark.parametrize("column", COLUMNS)
def test_column_width_is_set_per_column_on_both_tabs(
    tmp_path: Path, column: str
) -> None:
    out, _, _ = build(tmp_path)

    for sheet in sheets(out):
        letter = sheet.cell(row=1, column=column_of(column)).column_letter
        assert sheet.column_dimensions[letter].width == EXPECTED_WIDTHS[column]


def test_evidence_and_reasons_are_wider_than_numeric_columns(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path)

    sheet = sheets(out)[0]
    widths = {
        c: sheet.column_dimensions[
            sheet.cell(row=1, column=column_of(c)).column_letter
        ].width
        for c in COLUMNS
    }
    assert widths["evidence"] > widths["reasons"] > widths["gross_total"]


def test_money_columns_use_thousands_and_two_decimals(tmp_path: Path) -> None:
    out, ready, review = build(tmp_path)

    for sheet, rows in zip(sheets(out), (ready, review), strict=True):
        for r in range(2, 2 + len(rows)):
            for column in MONEY:
                cell = sheet.cell(row=r, column=column_of(column))
                assert cell.number_format == "#,##0.00"
                assert isinstance(cell.value, int | float)
            assert sheet.cell(row=r, column=column_of("net_total")).value == 1234567.5


def test_non_money_columns_keep_general_format(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path)

    sheet = sheets(out)[0]
    for column in COLUMNS:
        if column in MONEY:
            continue
        for r in range(1, 4):
            assert (
                sheet.cell(row=r, column=column_of(column)).number_format == "General"
            )


def test_header_money_cells_are_not_number_formatted(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path)

    sheet = sheets(out)[0]
    assert sheet.cell(row=1, column=column_of("net_total")).number_format == "General"


def test_empty_money_cell_is_formatted_but_stays_empty(tmp_path: Path) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    out = tmp_path / "out.xlsx"
    write_xlsx(
        [], [ResultRow(file="x.pdf", pdf_path=pdf, reasons=["not a readable PDF"])], out
    )

    cell = sheets(out)[1].cell(row=2, column=column_of("gross_total"))
    assert cell.value is None
    assert cell.number_format == "#,##0.00"


def test_header_font_is_bold_white_on_every_header_cell(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path)

    for sheet in sheets(out):
        for cell in sheet[1]:
            assert cell.font.b is True
            assert str(cell.font.color.rgb).endswith("FFFFFF")


def test_header_fill_is_dark_on_ready_and_amber_on_needs_review(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path)

    ready, review = sheets(out)
    for cell in ready[1]:
        assert cell.fill.fill_type == "solid"
        assert str(cell.fill.fgColor.rgb).endswith("1F2937")
    for cell in review[1]:
        assert cell.fill.fill_type == "solid"
        assert str(cell.fill.fgColor.rgb).endswith("B45309")


def test_data_rows_are_not_header_styled(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path)

    for sheet in sheets(out):
        for cell in sheet[2]:
            assert not cell.font.b
            assert cell.fill.fill_type is None


def test_wrap_text_only_on_evidence_and_reasons_data_cells(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path)

    for sheet in sheets(out):
        for r in range(2, sheet.max_row + 1):
            for column in COLUMNS:
                cell = sheet.cell(row=r, column=column_of(column))
                wrapped = bool(cell.alignment.wrap_text)
                assert wrapped is (column in ("evidence", "reasons")), (column, r)
                if wrapped:
                    assert cell.alignment.vertical == "top"


def test_styling_keeps_the_ac15_layout_exact_header_and_relative_hyperlinks(
    tmp_path: Path,
) -> None:
    out, ready, review = build(tmp_path)

    book = load_workbook(out)
    assert book.sheetnames == ["Ready", "Needs review"]
    for sheet, rows in zip(book.worksheets, (ready, review), strict=True):
        assert [c.value for c in sheet[1]] == COLUMNS
        assert sheet.max_row == 1 + len(rows)
        for r, source in enumerate(rows, start=2):
            cell = sheet.cell(row=r, column=column_of("pdf_link"))
            assert cell.hyperlink is not None
            target = Path(cell.hyperlink.target)
            assert not target.is_absolute()
            assert (out.parent / target).resolve() == source.pdf_path.resolve()
            assert cell.value == source.file
            assert cell.style == "Hyperlink"


def test_styling_does_not_turn_formula_looking_text_into_a_formula(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    out = tmp_path / "out.xlsx"
    hostile = row(pdf, supplier_name='=HYPERLINK("http://evil","x")', evidence="=1+1")

    write_xlsx([hostile], [], out)

    ready = sheets(out)[0]
    name = ready.cell(row=2, column=column_of("supplier_name"))
    evidence = ready.cell(row=2, column=column_of("evidence"))
    assert name.data_type == "s"
    assert name.value == '=HYPERLINK("http://evil","x")'
    assert evidence.data_type == "s"
    assert evidence.alignment.wrap_text


def test_reasons_cell_keeps_every_reason_joined_with_semicolons(tmp_path: Path) -> None:
    out, _, _ = build(tmp_path)

    cell = sheets(out)[1].cell(row=2, column=column_of("reasons"))
    assert cell.value == "missing supplier VAT id; not a readable PDF"
