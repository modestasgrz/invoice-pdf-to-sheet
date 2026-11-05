"""AC-08: Given one consistent invoice with supplier_vat_id null and one with NL12345, when run runs, then the first is in
Needs review with 'missing supplier VAT id' and the second with 'invalid VAT id format: NL12345'."""

from pathlib import Path
from typing import Any


def test_ac_08_vat_id_checks(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    missing = consistent(supplier_vat_id=None, invoice_number="VR-2026-0201")
    invalid = consistent(supplier_vat_id="NL12345", invoice_number="VR-2026-0202")
    text_pdf(tmp_path / "in" / "missing.pdf", missing)
    text_pdf(tmp_path / "in" / "invalid.pdf", invalid)
    model.replies = {"VR-2026-0201": missing, "VR-2026-0202": invalid}
    out = tmp_path / "invoices.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    review = {
        r["file"]: r["reasons"].split("; ") for r in read_xlsx(out)["Needs review"]
    }
    assert "missing supplier VAT id" in review["missing.pdf"]
    assert "invalid VAT id format: NL12345" in review["invalid.pdf"]
