"""Shared fixtures for the invoice-pdf-to-sheet acceptance tests: test PDFs built with reportlab/Pillow and a fake model at the DEC-04 seam."""

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

CONSISTENT: dict[str, Any] = {
    "supplier_name": "Verdant Ridge Green Coffee Imports BV",
    "supplier_vat_id": "NL999000001B01",
    "invoice_number": "VR-2026-0101",
    "invoice_date": "2026-10-01",
    "due_date": "2026-10-31",
    "currency": "EUR",
    "net_total": 250.00,
    "vat_total": 52.50,
    "gross_total": 302.50,
    "lines": [
        {
            "description": "Green coffee Ethiopia Guji 1 kg",
            "quantity": 2,
            "unit_price": 100.00,
            "line_total": 200.00,
        },
        {
            "description": "Sample roast fee",
            "quantity": 1,
            "unit_price": 50.00,
            "line_total": 50.00,
        },
    ],
}

COLUMNS = [
    "file",
    "supplier_name",
    "supplier_vat_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "net_total",
    "vat_total",
    "gross_total",
    "line_count",
    "method",
    "confidence",
    "evidence",
    "reasons",
    "pdf_link",
]


def invoice_lines(fields: dict[str, Any]) -> list[str]:
    lines = [
        str(fields["supplier_name"]),
        f"VAT id: {fields['supplier_vat_id'] or ''}",
        f"Invoice no: {fields['invoice_number']}",
        f"Invoice date: {fields['invoice_date']}",
        f"Due date: {fields['due_date'] or ''}",
        f"Currency: {fields['currency']}",
    ]
    lines += [
        f"{li['description']}  {li['quantity']} x {li['unit_price']:.2f} = {li['line_total']:.2f}"
        for li in fields["lines"]
    ]
    lines += [
        f"Net: {fields['net_total']:.2f}",
        f"VAT: {fields['vat_total']:.2f}",
        f"Total gross: {fields['gross_total']:.2f}",
    ]
    return lines


def with_evidence(fields: dict[str, Any]) -> dict[str, Any]:
    reply = copy.deepcopy(fields)
    quoted = {
        "invoice_number": fields["invoice_number"],
        "invoice_date": fields["invoice_date"],
        "gross_total": f"{fields['gross_total']:.2f}",
    }
    if fields["supplier_vat_id"]:
        quoted["supplier_vat_id"] = fields["supplier_vat_id"]
    reply.setdefault(
        "evidence",
        [{"field": k, "page": 1, "quote": v} for k, v in quoted.items()],
    )
    return reply


@pytest.fixture
def consistent() -> Callable[..., dict[str, Any]]:
    """consistent(**overrides) -> a model reply dict for the spec's consistent invoice, evidence quotes included."""

    def build(**overrides: Any) -> dict[str, Any]:
        fields = copy.deepcopy(CONSISTENT)
        fields.update(overrides)
        return with_evidence(fields)

    return build


@pytest.fixture
def text_pdf() -> Callable[..., Path]:
    """text_pdf(path, reply, extra=[], pages=1) writes a text PDF printing the reply's fields on page 1."""

    def build(
        path: Path,
        reply: dict[str, Any],
        extra: list[str] | None = None,
        pages: int = 1,
    ) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf = canvas.Canvas(str(path), invariant=1)
        for page in range(pages):
            y = 800
            for line in (
                [*invoice_lines(reply), *(extra or [])]
                if page == 0
                else [f"Page {page + 1}"]
            ):
                pdf.drawString(40, y, line)
                y -= 16
            pdf.showPage()
        pdf.save()
        return path

    return build


@pytest.fixture
def scan_pdf() -> Callable[[Path, dict[str, Any]], Path]:
    """scan_pdf(path, reply) writes an image-only PDF (no text layer) showing the reply's fields."""

    def build(path: Path, reply: dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(image)
        for i, line in enumerate(invoice_lines(reply)):
            draw.text((80, 80 + i * 30), line, fill="black")
        image.save(path, "PDF", resolution=150)
        return path

    return build


class FakeModel:
    """Stands in for invoice_pdf_to_sheet.extract.call_model. Replies are chosen by a marker found in the text blocks."""

    def __init__(self) -> None:
        self.replies: dict[str, Any] = {}
        self.default: Any = None
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    def text_of(self, content: list[dict[str, Any]]) -> str:
        return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")

    def calls_with(self, marker: str) -> int:
        return sum(marker in self.text_of(content) for _, content in self.calls)

    def __call__(self, system: str, content: list[dict[str, Any]]) -> Any:
        from invoice_pdf_to_sheet.schema import InvoiceFields

        self.calls.append((system, content))
        text = self.text_of(content)
        reply = next(
            (r for marker, r in self.replies.items() if marker in text), self.default
        )
        if isinstance(reply, BaseException):
            raise reply
        return InvoiceFields.model_validate(reply)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr("invoice_pdf_to_sheet.extract.call_model", fake)
    return fake


@pytest.fixture
def run_cli(
    capsys: pytest.CaptureFixture[str],
) -> Callable[[list[str]], tuple[int, str, str]]:
    def run(argv: list[str]) -> tuple[int, str, str]:
        from invoice_pdf_to_sheet.cli import main

        code = main(argv)
        out = capsys.readouterr()
        return code, out.out, out.err

    return run


@pytest.fixture
def read_xlsx() -> Callable[[Path], dict[str, list[dict[str, Any]]]]:
    """read_xlsx(path) -> {"Ready": [row dicts], "Needs review": [row dicts]} keyed by the header row."""

    def read(path: Path) -> dict[str, list[dict[str, Any]]]:
        from openpyxl import load_workbook

        book = load_workbook(path)
        tabs: dict[str, list[dict[str, Any]]] = {}
        for sheet in book.worksheets:
            rows = list(sheet.iter_rows(values_only=True))
            header = [str(h) for h in rows[0]]
            tabs[sheet.title] = [dict(zip(header, r, strict=True)) for r in rows[1:]]
        return tabs

    return read
