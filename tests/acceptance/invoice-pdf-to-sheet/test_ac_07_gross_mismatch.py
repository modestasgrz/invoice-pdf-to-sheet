"""AC-07: Given a consistent invoice whose model output has gross 310.00, when run runs, then the row is in
Needs review with the reason 'net + VAT 302.50 != gross 310.00'."""

from pathlib import Path
from typing import Any


def test_ac_07_gross_mismatch(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    reply = consistent(gross_total=310.00)
    text_pdf(tmp_path / "in" / "a.pdf", reply)
    model.default = reply
    out = tmp_path / "invoices.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    [row] = read_xlsx(out)["Needs review"]
    assert "net + VAT 302.50 != gross 310.00" in row["reasons"].split("; ")
