"""AC-25: Given an xlsx written by run and its input folder, when `publish --xlsx <out> --input <dir>` runs with the model
and google_sheet.publish mocked, then the model is never called, publish gets the rows of each tab unchanged (reasons as a
list, pdf_path = <dir>/<file>), stdout has 'Google Sheet: <url>' and exit 0; a publish error prints only its type name and
exits 1; a missing xlsx exits 2 with 'no xlsx at <path>'."""

import importlib
from pathlib import Path
from typing import Any

import pytest


def test_ac_25_publish_from_xlsx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
) -> None:
    from invoice_pdf_to_sheet.schema import ResultRow
    from invoice_pdf_to_sheet.xlsx import write_xlsx

    folder = tmp_path / "in"
    good = text_pdf(folder / "a.pdf", consistent())
    bad = text_pdf(folder / "b.pdf", consistent())
    ready = ResultRow(
        file="a.pdf",
        pdf_path=good,
        supplier_name="Verdant Ridge Green Coffee Imports BV",
        supplier_vat_id="NL999000001B01",
        invoice_number="VR-2026-0101",
        invoice_date="2026-10-01",
        due_date="2026-10-31",
        currency="EUR",
        net_total=250.0,
        vat_total=52.5,
        gross_total=302.5,
        line_count=2,
        method="text",
        confidence="verified",
        evidence='invoice_number p1: "VR-2026-0101"',
    )
    review = ready.model_copy(
        update={
            "file": "b.pdf",
            "pdf_path": bad,
            "supplier_vat_id": "NL1; x",
            "confidence": "unverified",
            "reasons": ["invalid VAT id format: NL1; x", "duplicate of a.pdf"],
        }
    )
    out = tmp_path / "out" / "invoices.xlsx"
    write_xlsx([ready], [review], out)

    google_sheet = importlib.import_module("invoice_pdf_to_sheet.google_sheet")
    calls: list[tuple[list[Any], list[Any]]] = []

    def fake_publish(r: list[Any], v: list[Any]) -> str:
        calls.append((r, v))
        return "https://docs.google.com/spreadsheets/d/S1"

    monkeypatch.setattr(google_sheet, "publish", fake_publish)
    code, stdout, _ = run_cli(["publish", "--xlsx", str(out), "--input", str(folder)])

    assert code == 0
    assert "Google Sheet: https://docs.google.com/spreadsheets/d/S1" in stdout
    assert model.calls == []
    [(got_ready, got_review)] = calls
    assert [r.model_dump() for r in got_ready] == [
        ready.model_copy(update={"pdf_path": folder / "a.pdf"}).model_dump()
    ]
    assert [r.model_dump() for r in got_review] == [
        review.model_copy(update={"pdf_path": folder / "b.pdf"}).model_dump()
    ]

    def boom(r: list[Any], v: list[Any]) -> str:
        raise RuntimeError("secret provider detail")

    monkeypatch.setattr(google_sheet, "publish", boom)
    code, _, stderr = run_cli(["publish", "--xlsx", str(out), "--input", str(folder)])
    assert code == 1
    assert "Google Sheet upload failed: RuntimeError" in stderr
    assert "secret provider detail" not in stderr

    missing = tmp_path / "nope.xlsx"
    code, _, stderr = run_cli(
        ["publish", "--xlsx", str(missing), "--input", str(folder)]
    )
    assert code == 2
    assert f"no xlsx at {missing}" in stderr
