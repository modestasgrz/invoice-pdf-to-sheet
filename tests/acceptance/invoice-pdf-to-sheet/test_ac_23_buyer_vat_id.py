"""AC-23: Given INVOICE_BUYER_VAT_ID unset and a consistent invoice whose model output has supplier_vat_id 'lt 999-000-099'
(the buyer's default LT999000099 once normalized), when run runs, then the row is in Needs review with the reason
"supplier VAT id is buyer's own: LT999000099". Given INVOICE_BUYER_VAT_ID=NL999000001B01 and supplier_vat_id NL999000001B01,
the same reason names NL999000001B01."""

from pathlib import Path
from typing import Any

import pytest


def test_ac_23_buyer_vat_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    monkeypatch.delenv("INVOICE_BUYER_VAT_ID", raising=False)
    reply = consistent(supplier_vat_id="lt 999-000-099")
    text_pdf(tmp_path / "in" / "own.pdf", reply)
    model.default = reply
    out = tmp_path / "default.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    [row] = read_xlsx(out)["Needs review"]
    assert "supplier VAT id is buyer's own: LT999000099" in row["reasons"].split("; ")

    monkeypatch.setenv("INVOICE_BUYER_VAT_ID", "NL999000001B01")
    reply = consistent()
    text_pdf(tmp_path / "in2" / "nl.pdf", reply)
    model.default = reply
    out = tmp_path / "configured.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in2"), "--xlsx", str(out)])

    [row] = read_xlsx(out)["Needs review"]
    assert "supplier VAT id is buyer's own: NL999000001B01" in row["reasons"].split(
        "; "
    )
