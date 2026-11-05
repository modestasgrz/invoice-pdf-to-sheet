"""AC-15: Given any run, when <out> is opened with openpyxl, then its sheets are exactly ["Ready", "Needs review"], row 1
of both is the DEC-07 columns in order, and every pdf_link cell has a hyperlink whose target is a relative path that,
resolved from <out>'s folder, is the existing source PDF."""

from pathlib import Path
from typing import Any

from openpyxl import load_workbook

COLUMNS = [
    "file",
    "supplier_name",
    "supplier_vat_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "net_total",
    "vat_total",
    "gross_total",
    "line_count",
    "method",
    "confidence",
    "evidence",
    "reasons",
    "pdf_link",
]


def test_ac_15_xlsx_layout(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    good = consistent(invoice_number="VR-2026-0501")
    bad = consistent(invoice_number="VR-2026-0502", supplier_vat_id=None)
    text_pdf(tmp_path / "in" / "good.pdf", good)
    text_pdf(tmp_path / "in" / "bad.pdf", bad)
    model.replies = {"VR-2026-0501": good, "VR-2026-0502": bad}
    out = tmp_path / "out" / "invoices.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    book = load_workbook(out)
    assert book.sheetnames == ["Ready", "Needs review"]
    for sheet in book.worksheets:
        assert [c.value for c in sheet[1]] == COLUMNS
        link_col = COLUMNS.index("pdf_link") + 1
        assert sheet.max_row == 2
        cell = sheet.cell(row=2, column=link_col)
        assert cell.hyperlink is not None
        target = Path(cell.hyperlink.target)
        assert not target.is_absolute()
        resolved = (out.parent / target).resolve()
        assert resolved.exists()
        assert (
            resolved == (tmp_path / "in" / sheet.cell(row=2, column=1).value).resolve()
        )
