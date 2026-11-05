"""AC-09: invoice date 2026-13-45 -> 'invalid invoice date: 2026-13-45'; invoice date 1 day after today ->
'invoice date in the future: <ISO>'; due 2026-09-01 before invoice date 2026-10-01 -> 'due date before invoice date'."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any


def test_ac_09_date_checks(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    bad = consistent(invoice_number="VR-2026-0301", invoice_date="2026-13-45")
    future = consistent(
        invoice_number="VR-2026-0302", invoice_date=tomorrow, due_date=None
    )
    early_due = consistent(invoice_number="VR-2026-0303", due_date="2026-09-01")
    text_pdf(tmp_path / "in" / "bad.pdf", bad)
    text_pdf(tmp_path / "in" / "future.pdf", future)
    text_pdf(tmp_path / "in" / "early_due.pdf", early_due)
    model.replies = {
        "VR-2026-0301": bad,
        "VR-2026-0302": future,
        "VR-2026-0303": early_due,
    }
    out = tmp_path / "invoices.xlsx"

    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    review = {
        r["file"]: r["reasons"].split("; ") for r in read_xlsx(out)["Needs review"]
    }
    assert "invalid invoice date: 2026-13-45" in review["bad.pdf"]
    assert f"invoice date in the future: {tomorrow}" in review["future.pdf"]
    assert "due date before invoice date" in review["early_due.pdf"]
