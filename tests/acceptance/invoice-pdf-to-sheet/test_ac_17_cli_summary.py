"""AC-17: Given 3 PDFs (2 Ready, 1 review), run prints '3 invoices: 2 ready, 1 needs review' and the xlsx path, exit 0.
Given a missing or empty input folder, exit code 2 and stderr contains 'no PDF files in <dir>'."""

from pathlib import Path
from typing import Any


def test_ac_17_cli_summary(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    replies = {
        "VR-2026-0601": consistent(invoice_number="VR-2026-0601"),
        "VR-2026-0602": consistent(invoice_number="VR-2026-0602"),
        "VR-2026-0603": consistent(invoice_number="VR-2026-0603", supplier_vat_id=None),
    }
    for number, reply in replies.items():
        text_pdf(tmp_path / "in" / f"{number}.pdf", reply)
    model.replies = replies
    out = tmp_path / "invoices.xlsx"

    code, stdout, _ = run_cli(
        ["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)]
    )

    assert code == 0
    assert "3 invoices: 2 ready, 1 needs review" in stdout.splitlines()
    assert str(out) in stdout

    empty = tmp_path / "empty"
    empty.mkdir()
    for folder in (empty, tmp_path / "missing"):
        code, _, stderr = run_cli(["run", "--input", str(folder), "--xlsx", str(out)])
        assert code == 2
        assert f"no PDF files in {folder}" in stderr
