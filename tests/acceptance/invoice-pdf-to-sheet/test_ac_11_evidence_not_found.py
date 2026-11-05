"""AC-11: Given a text PDF where the model's evidence quote for invoice_number is XX-9999 (not in the PDF text), when run
runs, then the row is in Needs review with confidence unverified and the reason 'evidence not found: invoice_number'."""

from pathlib import Path
from typing import Any


def test_ac_11_evidence_not_found(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    reply = consistent()
    text_pdf(tmp_path / "in" / "a.pdf", reply)
    reply["evidence"] = [
        {"field": "invoice_number", "page": 1, "quote": "XX-9999"}
        if e["field"] == "invoice_number"
        else e
        for e in reply["evidence"]
    ]
    model.default = reply
    out = tmp_path / "invoices.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    [row] = read_xlsx(out)["Needs review"]
    assert row["confidence"] == "unverified"
    assert "evidence not found: invoice_number" in row["reasons"].split("; ")
