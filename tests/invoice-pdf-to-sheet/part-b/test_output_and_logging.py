import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from loguru import logger
from openpyxl import load_workbook
from reportlab.pdfgen import canvas

from invoice_pdf_to_sheet.checks import check_fields
from invoice_pdf_to_sheet.cli import main
from invoice_pdf_to_sheet.schema import InvoiceFields, ResultRow
from invoice_pdf_to_sheet.xlsx import write_xlsx

LEAKS = [
    "LEAKSUPPLIER",
    "LEAK-INV-777",
    "LEAKY-ERROR-MSG",
    "LEAKY-CONN",
    "LEAKY-REFUSAL",
    "LEAKY-BYTES",
    "LEAKY-INPUT-VALUE",
    "1234.56",
    "XX-LEAKY-VAT",
]
BASE: dict[str, Any] = {
    "supplier_name": "LEAKSUPPLIER Ltd",
    "supplier_vat_id": "NL999000001B01",
    "invoice_number": "LEAK-INV-777",
    "invoice_date": "2026-10-01",
    "due_date": "2026-10-31",
    "currency": "EUR",
    "net_total": 1000.0,
    "vat_total": 234.56,
    "gross_total": 1234.56,
    "lines": [
        {"description": "x", "quantity": 1, "unit_price": 1000.0, "line_total": 1000.0}
    ],
    "evidence": [
        {"field": "invoice_number", "page": 1, "quote": "LEAK-INV-777"},
        {"field": "invoice_date", "page": 1, "quote": "2026-10-01"},
        {"field": "gross_total", "page": 1, "quote": "1234.56"},
    ],
}


def write_pdf(path: Path, marker: str) -> None:
    pdf = canvas.Canvas(str(path), invariant=1)
    for i, line in enumerate(
        [
            f"LEAKSUPPLIER Ltd {marker}",
            "Invoice no: LEAK-INV-777",
            "Invoice date: 2026-10-01",
            "Total gross: 1234.56",
        ]
    ):
        pdf.drawString(40, 800 - 16 * i, line)
    pdf.save()


@pytest.fixture
def sink() -> Iterator[list[str]]:
    messages: list[str] = []
    handler = logger.add(
        lambda message: messages.append(str(message)),
        level="DEBUG",
        format="{level} {name}:{function} {message}\n{exception}",
    )
    yield messages
    logger.remove(handler)


def fake_model(system: str, content: list[dict[str, Any]]) -> InvoiceFields:
    text = content[0]["text"]
    if "marker-ok" in text:
        return InvoiceFields.model_validate(BASE)
    if "marker-badvat" in text:
        return InvoiceFields.model_validate(
            {
                **BASE,
                "invoice_number": "LEAK-INV-778",
                "supplier_vat_id": "XX-LEAKY-VAT",
            }
        )
    if "marker-validation" in text:
        return InvoiceFields.model_validate({"supplier_name": "LEAKY-INPUT-VALUE"})
    if "marker-conn" in text:
        raise anthropic.APIConnectionError(
            message="LEAKY-CONN", request=httpx2.Request("POST", "http://invalid.test")
        )
    raise anthropic.APIError(
        "LEAKY-REFUSAL", httpx2.Request("POST", "http://invalid.test"), body=None
    )


@pytest.fixture
def batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "in"
    folder.mkdir()
    for name in ("a_ok", "b_badvat", "c_validation", "d_conn", "e_api"):
        write_pdf(folder / f"{name}.pdf", f"marker-{name.split('_')[1]}")
    (folder / "f_corrupt.pdf").write_bytes(b"LEAKY-BYTES not a pdf")
    monkeypatch.setattr("invoice_pdf_to_sheet.extract.call_model", fake_model)
    return folder


def test_nothing_from_pdf_text_or_exceptions_reaches_loguru(
    batch: Path,
    tmp_path: Path,
    sink: list[str],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    code = main(
        [
            "run",
            "--input",
            str(batch),
            "--xlsx",
            str(tmp_path / "o.xlsx"),
            "--workers",
            "4",
        ]
    )
    assert code == 0
    logged = "\n".join(sink) + "\n" + caplog.text
    for secret in LEAKS:
        assert secret not in logged
    assert "Traceback" not in logged
    for name in ("a_ok.pdf", "f_corrupt.pdf"):
        assert name in logged
    assert "run start: 6 files" in logged
    assert "run end: 1 ready, 5 review" in logged
    assert "1 invoices" not in capsys.readouterr().err


def test_failures_are_logged_by_type_name_only(
    batch: Path, tmp_path: Path, sink: list[str]
) -> None:
    main(["run", "--input", str(batch), "--xlsx", str(tmp_path / "o.xlsx")])
    logged = "\n".join(sink)
    for type_name in ("ValidationError", "APIConnectionError", "APIError"):
        assert type_name in logged


def test_dec_13_per_file_line_carries_reason_codes_not_values(
    batch: Path, tmp_path: Path, sink: list[str]
) -> None:
    """DEC-13: one line per file with file name + ready/review + reason codes (text before the first ':')."""
    main(["run", "--input", str(batch), "--xlsx", str(tmp_path / "o.xlsx")])
    lines = {
        name: line
        for line in sink
        for name in ("a_ok.pdf", "b_badvat.pdf", "d_conn.pdf")
        if f"{name}: " in line and ("ready" in line or "review" in line)
    }
    assert "ready" in lines["a_ok.pdf"]
    assert "review" in lines["b_badvat.pdf"]
    assert "invalid VAT id format" in lines["b_badvat.pdf"]
    assert "XX-LEAKY-VAT" not in lines["b_badvat.pdf"]
    assert "extraction failed" in lines["d_conn.pdf"]


@pytest.mark.parametrize(
    "field",
    [
        "supplier_name",
        "invoice_number",
        "supplier_vat_id",
        "currency",
        "invoice_date",
        "due_date",
    ],
)
def test_extracted_text_starting_with_equals_is_not_written_as_a_formula(
    tmp_path: Path, field: str
) -> None:
    payload = '=HYPERLINK("http://evil.invalid","click")'
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"")
    out = tmp_path / "o.xlsx"
    write_xlsx(
        [ResultRow.model_validate({"file": "x.pdf", "pdf_path": pdf, field: payload})],
        [],
        out,
    )
    sheet = load_workbook(out)["Ready"]
    assert all(cell.data_type != "f" for row in sheet.iter_rows() for cell in row)


def test_file_name_and_reason_with_leading_equals_are_not_formulas(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"")
    out = tmp_path / "o.xlsx"
    write_xlsx([ResultRow(file="=cmd.pdf", pdf_path=pdf, reasons=["=1+1"])], [], out)
    sheet = load_workbook(out)["Ready"]
    assert all(cell.data_type != "f" for row in sheet.iter_rows() for cell in row)


def test_hyperlink_survives_spaces_and_unicode_in_file_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "in dir"
    folder.mkdir()
    write_pdf(folder / "my invoice é 1.pdf", "marker-ok")
    monkeypatch.setattr("invoice_pdf_to_sheet.extract.call_model", fake_model)
    out = tmp_path / "out dir" / "o.xlsx"
    assert main(["run", "--input", str(folder), "--xlsx", str(out)]) == 0
    link = load_workbook(out)["Ready"].cell(row=2, column=16).hyperlink
    assert link is not None
    assert link.target is not None
    assert (out.parent / link.target).resolve() == (
        folder / "my invoice é 1.pdf"
    ).resolve()


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_totals_are_flagged_not_waved_through(bad: float) -> None:
    fields = InvoiceFields.model_validate({**BASE, "net_total": bad, "evidence": []})
    assert check_fields(fields) != []
