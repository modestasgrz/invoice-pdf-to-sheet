"""AC-06: Given a consistent invoice whose model output has lines summing to 260.00 and net 250.00, when run runs,
then the row is in Needs review and its reasons include exactly 'lines sum 260.00 != net 250.00'."""

from pathlib import Path
from typing import Any


def test_ac_06_line_sum_mismatch(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    reply = consistent(
        lines=[
            {
                "description": "Green coffee",
                "quantity": 2,
                "unit_price": 100.0,
                "line_total": 200.0,
            },
            {
                "description": "Sample roast fee",
                "quantity": 1,
                "unit_price": 60.0,
                "line_total": 60.0,
            },
        ]
    )
    text_pdf(tmp_path / "in" / "a.pdf", reply)
    model.default = reply
    out = tmp_path / "invoices.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    tabs = read_xlsx(out)
    assert tabs["Ready"] == []
    [row] = tabs["Needs review"]
    assert "lines sum 260.00 != net 250.00" in row["reasons"].split("; ")
