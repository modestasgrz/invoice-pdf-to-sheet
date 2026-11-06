"""AC-20: Given mocked Drive and Sheets services, when google_sheet.publish(ready, review) runs with 2 Ready rows and 1 review
row, then exactly 1 folder 'invoice-pdf-to-sheet <run timestamp>' is created, each of the 3 PDFs is uploaded into it as
application/pdf, exactly 1 spreadsheet is created with sheets Ready and Needs review holding the DEC-07 header plus 2 and 1
rows, each pdf_link cell is the plain file name linked via textFormat.link.uri to its webViewLink (no formula: no written
value starts with '='), and the spreadsheet URL is returned."""

import importlib
import re
from pathlib import Path
from typing import Any

import pytest

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


class Call:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def execute(self) -> dict[str, Any]:
        return self.result


class FakeDrive:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []

    def files(self) -> "FakeDrive":
        return self

    def create(self, **kwargs: Any) -> Call:
        self.created.append(kwargs)
        body = kwargs["body"]
        file_id = f"id-{len(self.created)}"
        return Call(
            {"id": file_id, "webViewLink": f"https://drive.example/{body['name']}"}
        )

    def update(self, **kwargs: Any) -> Call:
        self.updated.append(kwargs)
        return Call({"id": kwargs.get("fileId", "")})


class FakeSheets:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.batches: list[dict[str, Any]] = []

    def spreadsheets(self) -> "FakeSheets":
        return self

    def values(self) -> "FakeSheets":
        return self

    def create(self, **kwargs: Any) -> Call:
        self.created.append(kwargs)
        return Call(
            {
                "spreadsheetId": "S1",
                "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/S1",
            }
        )

    def batchUpdate(self, **kwargs: Any) -> Call:
        self.batches.append(kwargs)
        return Call({})


def test_ac_20_google_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consistent: Any, text_pdf: Any
) -> None:
    from invoice_pdf_to_sheet.schema import ResultRow

    google_sheet = importlib.import_module("invoice_pdf_to_sheet.google_sheet")

    drive, sheets = FakeDrive(), FakeSheets()
    monkeypatch.setattr(google_sheet, "load_credentials", lambda: object())
    monkeypatch.setattr(
        google_sheet,
        "build",
        lambda name, version, credentials: drive if name == "drive" else sheets,
    )

    def row(name: str, reasons: list[str]) -> Any:
        pdf = text_pdf(tmp_path / name, consistent())
        return ResultRow(
            file=name,
            supplier_name="Verdant Ridge Green Coffee Imports BV",
            supplier_vat_id="NL999000001B01",
            invoice_number=name,
            invoice_date="2026-10-01",
            due_date="2026-10-31",
            currency="EUR",
            net_total=250.0,
            vat_total=52.5,
            gross_total=302.5,
            line_count=2,
            method="text",
            confidence="verified",
            evidence="",
            reasons=reasons,
            pdf_path=pdf,
        )

    ready = [row("a.pdf", []), row("b.pdf", [])]
    review = [row("c.pdf", ["missing supplier VAT id"])]

    url = google_sheet.publish(ready, review)

    assert url == "https://docs.google.com/spreadsheets/d/S1"
    folders = [
        c
        for c in drive.created
        if c["body"].get("mimeType") == "application/vnd.google-apps.folder"
    ]
    assert len(folders) == 1
    assert re.fullmatch(
        r"invoice-pdf-to-sheet \d{4}-\d{2}-\d{2} \d{2}:\d{2}",
        folders[0]["body"]["name"],
    )
    folder_id = f"id-{drive.created.index(folders[0]) + 1}"
    uploads = [
        c for c in drive.created if c["body"].get("mimeType") == "application/pdf"
    ]
    assert sorted(c["body"]["name"] for c in uploads) == ["a.pdf", "b.pdf", "c.pdf"]
    assert all(c["body"]["parents"] == [folder_id] for c in uploads)

    assert len(sheets.created) == 1
    titles = [s["properties"]["title"] for s in sheets.created[0]["body"]["sheets"]]
    assert titles == ["Ready", "Needs review"]
    data = {d["range"]: d["values"] for b in sheets.batches for d in b["body"]["data"]}
    ready_values, review_values = data["'Ready'!A1"], data["'Needs review'!A1"]
    assert ready_values[0] == COLUMNS
    assert review_values[0] == COLUMNS
    assert len(ready_values) == 3
    assert len(review_values) == 2
    for b in sheets.batches:
        for d in b["body"]["data"]:
            for r in d["values"]:
                assert not any(str(v).startswith("=") for v in r)
    link = COLUMNS.index("pdf_link")
    tabs = sheets.created[0]["body"]["sheets"]
    for tab, values in zip(tabs, (ready_values[1:], review_values[1:]), strict=True):
        cells = [row["values"][link] for row in tab["data"][0]["rowData"][1:]]
        assert len(cells) == len(values)
        for cell, r in zip(cells, values, strict=True):
            assert cell["userEnteredValue"] == {"stringValue": r[0]}
            assert (
                cell["userEnteredFormat"]["textFormat"]["link"]["uri"]
                == f"https://drive.example/{r[0]}"
            )
