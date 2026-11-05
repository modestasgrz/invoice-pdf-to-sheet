"""AC-12: Given 3 PDFs where call_model raises anthropic.APITimeoutError on every call for the second file, when run
runs, then call_model was called exactly 2 times for that file; its row is in Needs review with
'extraction failed: APITimeoutError' and method none; the other 2 are in Ready; exit code 0."""

from pathlib import Path
from typing import Any

import anthropic
import httpx2


def test_ac_12_retry_then_review(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    first = consistent(invoice_number="VR-2026-0401")
    second = consistent(invoice_number="VR-2026-0402")
    third = consistent(invoice_number="VR-2026-0403")
    for name, reply in (("1.pdf", first), ("2.pdf", second), ("3.pdf", third)):
        text_pdf(tmp_path / "in" / name, reply)
    timeout = anthropic.APITimeoutError(
        request=httpx2.Request("POST", "https://api.example/v1/messages")
    )
    model.replies = {
        "VR-2026-0401": first,
        "VR-2026-0402": timeout,
        "VR-2026-0403": third,
    }
    out = tmp_path / "invoices.xlsx"

    code, _, _ = run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    assert code == 0
    assert model.calls_with("VR-2026-0402") == 2
    tabs = read_xlsx(out)
    assert sorted(r["file"] for r in tabs["Ready"]) == ["1.pdf", "3.pdf"]
    [failed] = tabs["Needs review"]
    assert failed["file"] == "2.pdf"
    assert failed["method"] == "none"
    assert "extraction failed: APITimeoutError" in failed["reasons"].split("; ")
