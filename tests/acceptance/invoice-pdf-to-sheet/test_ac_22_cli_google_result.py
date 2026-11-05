"""AC-22: Given run --google with google_sheet.publish mocked: publish returns a URL -> stdout 'Google Sheet: <url>', exit 0.
publish raises -> xlsx still written, stderr 'Google Sheet upload failed: <ExceptionTypeName>' without the message, exit 1."""

from pathlib import Path
from typing import Any

import pytest


def test_ac_22_cli_google_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
) -> None:
    reply = consistent()
    text_pdf(tmp_path / "in" / "a.pdf", reply)
    model.default = reply
    out = tmp_path / "invoices.xlsx"
    argv = ["run", "--input", str(tmp_path / "in"), "--xlsx", str(out), "--google"]
    monkeypatch.setattr(
        "invoice_pdf_to_sheet.google_sheet.publish",
        lambda ready, review: "https://docs.google.com/spreadsheets/d/S1",
    )

    code, stdout, _ = run_cli(argv)

    assert code == 0
    assert (
        "Google Sheet: https://docs.google.com/spreadsheets/d/S1" in stdout.splitlines()
    )

    out.unlink()

    def broken(ready: Any, review: Any) -> str:
        raise ConnectionError("SECRET_GOOGLE_DETAIL_42")

    monkeypatch.setattr("invoice_pdf_to_sheet.google_sheet.publish", broken)

    code, stdout, stderr = run_cli(argv)

    assert code == 1
    assert out.exists()
    assert "Google Sheet upload failed: ConnectionError" in stderr
    assert "SECRET_GOOGLE_DETAIL_42" not in stderr + stdout
