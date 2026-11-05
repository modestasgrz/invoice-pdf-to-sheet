"""AC-10: Given a.pdf and b.pdf whose model output has the same supplier VAT id and invoice number, when run runs,
then a.pdf is in Ready and b.pdf is in Needs review with the reason 'duplicate of a.pdf'."""

from pathlib import Path
from typing import Any


def test_ac_10_duplicate(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    reply = consistent()
    text_pdf(tmp_path / "in" / "a.pdf", reply, extra=["MARK-FIRST"])
    text_pdf(tmp_path / "in" / "b.pdf", reply, extra=["MARK-SECOND"])
    model.replies = {"MARK-FIRST": reply, "MARK-SECOND": reply}
    out = tmp_path / "invoices.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    tabs = read_xlsx(out)
    assert [r["file"] for r in tabs["Ready"]] == ["a.pdf"]
    [dup] = tabs["Needs review"]
    assert dup["file"] == "b.pdf"
    assert "duplicate of a.pdf" in dup["reasons"].split("; ")
