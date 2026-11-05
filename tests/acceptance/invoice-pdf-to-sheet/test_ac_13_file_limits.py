"""AC-13: Given a PDF over 10 MB, a valid 6-page PDF and a .pdf file containing 'not a pdf', when run runs, then
call_model is never called; reasons are 'file too large: <MB, 1 decimal> MB > 10 MB', 'too many pages: 6 > 5', 'not a readable PDF'."""

from pathlib import Path
from typing import Any


def test_ac_13_file_limits(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    reply = consistent()
    big = text_pdf(tmp_path / "in" / "big.pdf", reply)
    with big.open("ab") as f:
        f.write(b"%" + b"0" * (11 * 1048576) + b"\n")
    text_pdf(tmp_path / "in" / "long.pdf", reply, pages=6)
    (tmp_path / "in" / "junk.pdf").write_text("not a pdf")
    model.default = reply
    out = tmp_path / "invoices.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    assert model.calls == []
    review = {
        r["file"]: r["reasons"].split("; ") for r in read_xlsx(out)["Needs review"]
    }
    size_mb = big.stat().st_size / 1048576
    assert f"file too large: {size_mb:.1f} MB > 10 MB" in review["big.pdf"]
    assert "too many pages: 6 > 5" in review["long.pdf"]
    assert "not a readable PDF" in review["junk.pdf"]
