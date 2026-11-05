"""AC-24: the evidence quote must contain the extracted value for invoice_number (case/space-insensitive) and gross_total
(decimal comma/dot and thousands separators normalized); otherwise 'evidence does not match: <field>', confidence unverified."""

from pathlib import Path
from typing import Any


def quotes(reply: dict[str, Any], field: str, quote: str) -> dict[str, Any]:
    reply["evidence"] = [
        {**e, "quote": quote} if e["field"] == field else e for e in reply["evidence"]
    ]
    return reply


def test_ac_24_evidence_matches_value(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    wrong_number = quotes(
        consistent(invoice_number="VR-2026-0999"), "invoice_number", "VR-2026-0101"
    )
    text_pdf(tmp_path / "in" / "number.pdf", consistent(), extra=["MARK-NUMBER"])
    comma = quotes(
        consistent(invoice_number="VR-2026-0102"), "gross_total", "Brutto: 302,50"
    )
    text_pdf(tmp_path / "in" / "comma.pdf", comma, extra=["Brutto: 302,50"])
    wrong_gross = quotes(
        consistent(invoice_number="VR-2026-0103"), "gross_total", "Total gross: 310.00"
    )
    text_pdf(tmp_path / "in" / "gross.pdf", wrong_gross, extra=["Total gross: 310.00"])
    model.replies = {
        "MARK-NUMBER": wrong_number,
        "VR-2026-0102": comma,
        "VR-2026-0103": wrong_gross,
    }
    out = tmp_path / "invoices.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    tabs = read_xlsx(out)
    ready = {r["file"]: r for r in tabs["Ready"]}
    review = {r["file"]: r for r in tabs["Needs review"]}
    assert ready["comma.pdf"]["confidence"] == "verified"
    assert review["number.pdf"]["confidence"] == "unverified"
    assert "evidence does not match: invoice_number" in review["number.pdf"][
        "reasons"
    ].split("; ")
    assert review["gross.pdf"]["confidence"] == "unverified"
    assert "evidence does not match: gross_total" in review["gross.pdf"][
        "reasons"
    ].split("; ")
