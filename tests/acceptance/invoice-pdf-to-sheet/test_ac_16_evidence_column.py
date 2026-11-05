"""AC-16: Given a consistent text invoice, when run runs, then its evidence cell lists each quoted field as
'<field> p<page>: "<quote>"' joined with '; ', and includes 'invoice_number p1: "VR-2026-0101"'."""

from pathlib import Path
from typing import Any


def test_ac_16_evidence_column(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    reply = consistent()
    text_pdf(tmp_path / "in" / "a.pdf", reply)
    model.default = reply
    out = tmp_path / "invoices.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    [row] = read_xlsx(out)["Ready"]
    parts = row["evidence"].split("; ")
    assert 'invoice_number p1: "VR-2026-0101"' in parts
    expected = [f'{e["field"]} p{e["page"]}: "{e["quote"]}"' for e in reply["evidence"]]
    assert sorted(parts) == sorted(expected)
