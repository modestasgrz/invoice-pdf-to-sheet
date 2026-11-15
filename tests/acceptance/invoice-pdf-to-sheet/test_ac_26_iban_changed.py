"""AC-26: a later invoice from the same supplier asking to be paid to a different IBAN goes to Needs review with
'IBAN changed: <new> != <first> on <first file>'; an IBAN the model returns that isn't printed gives
'evidence not found: supplier_iban'; a row with no IBAN is never compared."""

from pathlib import Path
from typing import Any


def test_ac_26_iban_changed(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    ibans = {
        "a": "NL00 MOCK 0999 0000 01",
        "b": "nl00mock0999000001",
        "c": "LT00 9990 0000 0000 0091",
    }
    for n, (name, iban) in enumerate(ibans.items()):
        reply = consistent(invoice_number=f"VR-2026-010{n + 1}", supplier_iban=iban)
        text_pdf(tmp_path / "in" / f"{name}.pdf", reply, extra=[f"IBAN: {iban}"])
        model.replies[f"VR-2026-010{n + 1}"] = reply
    no_iban = consistent(invoice_number="VR-2026-0104", supplier_iban=None)
    text_pdf(tmp_path / "in" / "d.pdf", no_iban)
    model.replies["VR-2026-0104"] = no_iban
    out = tmp_path / "invoices.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    tabs = read_xlsx(out)
    assert sorted(r["file"] for r in tabs["Ready"]) == ["a.pdf", "b.pdf", "d.pdf"]
    [changed] = tabs["Needs review"]
    assert changed["file"] == "c.pdf"
    assert changed["reasons"] == (
        "IBAN changed: LT009990000000000091 != NL00MOCK0999000001 on a.pdf"
    )

    unprinted = consistent(supplier_iban="NL00 MOCK 0999 0000 77")
    text_pdf(
        tmp_path / "in2" / "e.pdf", unprinted, extra=["IBAN: NL00 MOCK 0999 0000 01"]
    )
    model.replies = {"VR-2026-0101": unprinted}
    out2 = tmp_path / "invoices2.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in2"), "--xlsx", str(out2)])

    [row] = read_xlsx(out2)["Needs review"]
    assert row["confidence"] == "unverified"
    assert row["reasons"] == "evidence not found: supplier_iban"
