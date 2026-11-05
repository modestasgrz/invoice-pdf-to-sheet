"""Owner condition for the BLE001 per-file ignore: each blind handler outputs type(e).__name__ only, never the message."""

import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pdfplumber
import pytest
from loguru import logger
from reportlab.pdfgen import canvas

from invoice_pdf_to_sheet import cli, extract
from invoice_pdf_to_sheet.schema import InvoiceFields

SECRET = "SECRET_MESSAGE_9d1"


class Boom(Exception):
    pass


@pytest.fixture
def logs() -> Iterator[list[str]]:
    lines: list[str] = []
    sink = logger.add(
        lambda m: lines.append(str(m)), level=0, format="{message} | {exception}"
    )
    yield lines
    logger.remove(sink)


def make_pdf(path: Path) -> Path:
    pdf = canvas.Canvas(str(path), invariant=1)
    pdf.drawString(40, 800, "Invoice no: VR-2026-0101 plus enough page text")
    pdf.save()
    return path


def test_extract_model_handler_outputs_type_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logs: list[str]
) -> None:
    def fail(system: str, content: list[dict[str, Any]]) -> InvoiceFields:
        raise Boom(SECRET)

    monkeypatch.setattr(extract, "call_model", fail)
    row = extract.extract_file(make_pdf(tmp_path / "a.pdf"))
    assert row.reasons == ["extraction failed: Boom"]
    assert any("Boom" in line for line in logs)
    assert SECRET not in "\n".join(logs) + row.model_dump_json()


def test_extract_read_handler_outputs_type_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logs: list[str]
) -> None:
    def fail(path: Path) -> None:
        raise Boom(SECRET)

    monkeypatch.setattr(pdfplumber, "open", fail)
    row = extract.extract_file(make_pdf(tmp_path / "a.pdf"))
    assert row.reasons == ["not a readable PDF"]
    assert any("Boom" in line for line in logs)
    assert SECRET not in "\n".join(logs) + row.model_dump_json()


def test_cli_google_handler_outputs_type_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    logs: list[str],
) -> None:
    (tmp_path / "in").mkdir()
    make_pdf(tmp_path / "in" / "a.pdf")

    def no_model(system: str, content: list[dict[str, Any]]) -> InvoiceFields:
        raise Boom("model off")

    class FakeGoogle:
        @staticmethod
        def publish(ready: Any, review: Any) -> str:
            raise Boom(SECRET)

    monkeypatch.setattr(extract, "call_model", no_model)
    monkeypatch.setattr(importlib, "import_module", lambda name: FakeGoogle)
    code = cli.main(
        [
            "run",
            "--input",
            str(tmp_path / "in"),
            "--xlsx",
            str(tmp_path / "o.xlsx"),
            "--google",
        ]
    )
    out = capsys.readouterr()
    assert code == 1
    assert "Google Sheet upload failed: Boom" in out.err
    assert SECRET not in out.out + out.err + "\n".join(logs)
