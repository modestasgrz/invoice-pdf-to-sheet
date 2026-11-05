import json
from pathlib import Path
from typing import Any

import anthropic
import pytest
from openpyxl import Workbook
from reportlab.pdfgen import canvas

from invoice_pdf_to_sheet import extract
from invoice_pdf_to_sheet.evaluate import evaluate, same
from invoice_pdf_to_sheet.schema import InvoiceFields


def make_pdf(path: Path, lines: list[str]) -> Path:
    pdf = canvas.Canvas(str(path), invariant=1)
    for i, line in enumerate(lines):
        pdf.drawString(40, 800 - 16 * i, line)
    pdf.save()
    return path


def test_validation_error_is_retried_then_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def broken(system: str, content: list[dict[str, Any]]) -> InvoiceFields:
        calls.append(1)
        return InvoiceFields.model_validate({"supplier_name": "only"})

    monkeypatch.setattr(extract, "call_model", broken)
    row = extract.extract_file(
        make_pdf(tmp_path / "a.pdf", ["Invoice no: A-1 with enough text here"])
    )
    assert len(calls) == 2
    assert row.reasons == ["extraction failed: ValidationError"]
    assert row.confidence == "none"


def test_refusal_raises_extraction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        stop_reason = "refusal"
        parsed_output = None

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            self.beta = self
            self.messages = self

        def parse(self, **kwargs: Any) -> Response:
            return Response()

    monkeypatch.setattr(anthropic, "Anthropic", Client)
    with pytest.raises(extract.ExtractionError):
        extract.call_model("s", [])


def test_escape_only_angle_brackets() -> None:
    assert extract.escape("a < b & c > d") == "a &lt; b & c &gt; d"


def test_same_rules() -> None:
    assert same("net_total", "302.504", 302.50)
    assert not same("net_total", "302.51", 302.50)
    assert same("due_date", "", None)
    assert same("due_date", None, None)
    assert not same("due_date", "2026-10-01", None)
    assert not same("supplier_vat_id", None, "LT999000003")
    assert same("supplier_vat_id", "lt 999 000 003", "LT999000003")
    assert same("supplier_name", "  Clovermere  dairy co-op", "Clovermere Dairy Co-op")
    assert not same("line_count", "two", 2)


def test_missing_row_scores_zero_and_routing(tmp_path: Path) -> None:
    book = Workbook()
    ready = book.worksheets[0]
    ready.title = "Ready"
    ready.append(["file", "supplier_name"])
    ready.append(["a.pdf", "X"])
    book.create_sheet("Needs review").append(["file", "supplier_name"])
    xlsx = tmp_path / "o.xlsx"
    book.save(xlsx)
    entry = dict.fromkeys(
        [
            "supplier_vat_id",
            "invoice_number",
            "invoice_date",
            "due_date",
            "currency",
            "net_total",
            "vat_total",
            "gross_total",
            "line_count",
        ]
    )
    key = {
        "a.pdf": {**entry, "supplier_name": "X", "expected_tab": "Needs review"},
        "b.pdf": {**entry, "supplier_name": "Y", "expected_tab": "Ready"},
    }
    key_path = tmp_path / "k.json"
    key_path.write_text(json.dumps(key))
    lines = evaluate(xlsx, key_path)
    assert lines[0] == "field accuracy: 10/20 = 50.0%"
    assert lines[1] == "routing: 0/2 files in the expected tab"
