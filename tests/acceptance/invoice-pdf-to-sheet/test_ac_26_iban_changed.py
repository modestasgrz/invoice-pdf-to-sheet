"""AC-26: an invoice asking to be paid to a different IBAN than the supplier's usual one goes to Needs review with
'IBAN changed: <iban> != <usual> on <first file with usual>', even when it sorts first; with no usual IBAN (a tie) every
row is flagged; an IBAN the model returns that isn't printed gives 'evidence not found: supplier_iban'; a row with no
IBAN is never compared."""

from pathlib import Path
from typing import Any

NL = "NL00 MOCK 0999 0000 01"
LT = "LT00 9990 0000 0000 0091"


def run_batch(
    tmp_path: Path,
    folder: str,
    invoices: list[tuple[str, str | None, str | None]],
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> dict[str, list[dict[str, Any]]]:
    """invoices: (file stem, IBAN printed, IBAN the model returns)."""
    model.replies = {}
    for n, (stem, printed, returned) in enumerate(invoices):
        number = f"VR-2026-01{n + 10}"
        reply = consistent(invoice_number=number, supplier_iban=returned)
        extra = [f"IBAN: {printed}"] if printed else []
        text_pdf(tmp_path / folder / f"{stem}.pdf", reply, extra=extra)
        model.replies[number] = reply
    out = tmp_path / f"{folder}.xlsx"
    run_cli(["run", "--input", str(tmp_path / folder), "--xlsx", str(out)])
    tabs: dict[str, list[dict[str, Any]]] = read_xlsx(out)
    return tabs


def test_ac_26_iban_changed(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    fixtures = (consistent, text_pdf, model, run_cli, read_xlsx)

    tabs = run_batch(
        tmp_path,
        "usual",
        [
            ("a", LT, LT),
            ("b", NL, NL),
            ("c", "nl00mock0999000001", "IBAN nl00mock0999000001"),
            ("d", None, None),
        ],
        *fixtures,
    )
    assert sorted(r["file"] for r in tabs["Ready"]) == ["b.pdf", "c.pdf", "d.pdf"]
    [changed] = tabs["Needs review"]
    assert changed["file"] == "a.pdf"
    assert changed["reasons"] == (
        "IBAN changed: LT009990000000000091 != NL00MOCK0999000001 on b.pdf"
    )

    tabs = run_batch(tmp_path, "tie", [("x", NL, NL), ("y", LT, LT)], *fixtures)
    assert tabs["Ready"] == []
    assert {r["file"]: r["reasons"] for r in tabs["Needs review"]} == {
        "x.pdf": "IBAN changed: NL00MOCK0999000001 != LT009990000000000091 on y.pdf",
        "y.pdf": "IBAN changed: LT009990000000000091 != NL00MOCK0999000001 on x.pdf",
    }

    tabs = run_batch(
        tmp_path, "unprinted", [("e", NL, "NL00 MOCK 0999 0000 77")], *fixtures
    )
    [row] = tabs["Needs review"]
    assert row["confidence"] == "unverified"
    assert row["reasons"] == "evidence not found: supplier_iban"
