"""AC-04: Given one text PDF of a consistent invoice, when `run --input <dir> --xlsx <out>` runs, then Ready has 1 row
with those exact field values, line_count 2, method text, confidence verified, empty reasons; Needs review has 0 rows."""

from pathlib import Path
from typing import Any


def test_ac_04_text_invoice_ready(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    reply = consistent()
    text_pdf(tmp_path / "in" / "vr.pdf", reply)
    model.default = reply
    out = tmp_path / "out" / "invoices.xlsx"

    code, _, _ = run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    assert code == 0
    tabs = read_xlsx(out)
    assert tabs["Needs review"] == []
    [row] = tabs["Ready"]
    assert row["file"] == "vr.pdf"
    assert row["supplier_name"] == "Verdant Ridge Green Coffee Imports BV"
    assert row["supplier_vat_id"] == "NL999000001B01"
    assert row["invoice_number"] == "VR-2026-0101"
    assert str(row["invoice_date"])[:10] == "2026-10-01"
    assert str(row["due_date"])[:10] == "2026-10-31"
    assert row["currency"] == "EUR"
    assert float(row["net_total"]) == 250.00
    assert float(row["vat_total"]) == 52.50
    assert float(row["gross_total"]) == 302.50
    assert int(row["line_count"]) == 2
    assert row["method"] == "text"
    assert row["confidence"] == "verified"
    assert row["reasons"] in (None, "")
