from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from reportlab.pdfgen import canvas

from invoice_pdf_to_sheet import cli


def corrupt_media_box(path: Path) -> None:
    pdf = canvas.Canvas(str(path), invariant=1)
    pdf.drawString(40, 800, "Invoice no: X-1 from a supplier with plenty of text")
    pdf.save()
    data = path.read_bytes()
    digit = data.index(b"8", data.index(b"/MediaBox"))
    path.write_bytes(data[:digit] + b"\xe7" + data[digit + 1 :])


def test_corrupt_media_box_becomes_review_row_and_run_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    corrupt_media_box(folder / "a_corrupt.pdf")
    good = canvas.Canvas(str(folder / "b_good.pdf"), invariant=1)
    good.drawString(40, 800, "Invoice no: X-2 from a supplier with plenty of text")
    good.save()
    calls: list[Any] = []

    def fake_call_model(system: str, content: list[dict[str, Any]]) -> Any:
        calls.append(content)
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("invoice_pdf_to_sheet.extract.call_model", fake_call_model)
    out = tmp_path / "out.xlsx"

    assert cli.main(["run", "--input", str(folder), "--xlsx", str(out)]) == 0

    rows = {
        row[0]: row
        for row in load_workbook(out)["Needs review"].iter_rows(
            min_row=2, values_only=True
        )
    }
    assert rows["a_corrupt.pdf"][14] == "not a readable PDF"
    assert rows["a_corrupt.pdf"][11] == "none"
    assert rows["b_good.pdf"][14] == "extraction failed: RuntimeError"
    assert len(calls) == 2
    assert all("X-2" in str(c) for c in calls)
