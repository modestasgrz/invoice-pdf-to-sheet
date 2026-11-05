"""AC-05: Given one image-only PDF, when run runs and the mocked model returns a consistent invoice, then call_model
received at least 1 image block and no text block containing page text; the row is in Ready with method vision and confidence vision."""

from pathlib import Path
from typing import Any


def test_ac_05_scan_uses_vision(
    tmp_path: Path,
    consistent: Any,
    scan_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    reply = consistent()
    scan_pdf(tmp_path / "in" / "scan.pdf", reply)
    model.default = reply
    out = tmp_path / "invoices.xlsx"

    code, _, _ = run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    assert code == 0
    assert len(model.calls) >= 1
    _, content = model.calls[0]
    assert any(block["type"] == "image" for block in content)
    sent_text = model.text_of(content)
    assert "VR-2026-0101" not in sent_text
    assert "Verdant Ridge" not in sent_text
    [row] = read_xlsx(out)["Ready"]
    assert row["method"] == "vision"
    assert row["confidence"] == "vision"
