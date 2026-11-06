from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from invoice_pdf_to_sheet import google_sheet
from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow


def publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready: int,
    review: int,
    **fields: Any,
) -> MagicMock:
    sheets = MagicMock()
    sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
        "spreadsheetId": "S1",
        "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/S1",
    }
    drive = MagicMock()
    drive.files.return_value.create.return_value.execute.return_value = {
        "id": "F",
        "webViewLink": "https://drive.example/x",
    }
    monkeypatch.setattr(google_sheet, "load_credentials", lambda: object())
    monkeypatch.setattr(
        google_sheet,
        "build",
        lambda name, version, credentials: drive if name == "drive" else sheets,
    )
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    row = ResultRow(
        file="a.pdf",
        pdf_path=pdf,
        gross_total=1234.5,
        net_total=1000.0,
        vat_total=234.5,
        **fields,
    )
    google_sheet.publish(
        [row] * ready, [row.model_copy(update={"reasons": ["x"]})] * review
    )
    return sheets


def create_body(sheets: MagicMock) -> dict[str, Any]:
    body: dict[str, Any] = sheets.spreadsheets.return_value.create.call_args.kwargs[
        "body"
    ]
    return body


def test_tabs_get_header_style_frozen_rows_filter_and_widths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = publish(tmp_path, monkeypatch, ready=2, review=1)

    tabs = create_body(sheets)["sheets"]
    assert [t["properties"]["title"] for t in tabs] == ["Ready", "Needs review"]
    for tab, rows in zip(tabs, (2, 1), strict=True):
        props = tab["properties"]
        assert props["gridProperties"] == {"frozenRowCount": 1, "frozenColumnCount": 1}
        assert tab["basicFilter"]["range"] == {
            "sheetId": props["sheetId"],
            "startRowIndex": 0,
            "endRowIndex": rows + 1,
            "startColumnIndex": 0,
            "endColumnIndex": len(COLUMNS),
        }
        grid = tab["data"][0]
        widths = [c["pixelSize"] for c in grid["columnMetadata"]]
        assert len(widths) == len(COLUMNS)
        assert widths[COLUMNS.index("supplier_name")] >= 220
        header = grid["rowData"][0]["values"]
        assert all(c["userEnteredFormat"]["textFormat"]["bold"] for c in header)
        assert all("backgroundColorStyle" in c["userEnteredFormat"] for c in header)
        assert len(grid["rowData"]) == rows + 1
    assert tabs[0]["properties"]["sheetId"] != tabs[1]["properties"]["sheetId"]


def test_amount_and_date_columns_get_number_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = publish(tmp_path, monkeypatch, ready=1, review=0)

    cells = create_body(sheets)["sheets"][0]["data"][0]["rowData"][1]["values"]
    for column in ("net_total", "vat_total", "gross_total"):
        assert cells[COLUMNS.index(column)]["userEnteredFormat"]["numberFormat"] == {
            "type": "NUMBER",
            "pattern": "#,##0.00",
        }
    for column in ("invoice_date", "due_date"):
        assert cells[COLUMNS.index(column)]["userEnteredFormat"]["numberFormat"] == {
            "type": "DATE",
            "pattern": "yyyy-mm-dd",
        }


def written(sheets: MagicMock) -> list[Any]:
    data = sheets.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs[
        "body"
    ]["data"]
    rows: list[Any] = data[0]["values"]
    return rows


def test_iso_dates_go_as_real_dates_and_other_text_stays_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = publish(
        tmp_path,
        monkeypatch,
        ready=1,
        review=0,
        invoice_date="2026-10-01",
        due_date="31.10.2026",
        supplier_vat_id="2026-10-01",
    )

    row = written(sheets)[1]
    assert row[COLUMNS.index("invoice_date")] == "2026-10-01"
    assert row[COLUMNS.index("due_date")] == "'31.10.2026"
    assert row[COLUMNS.index("supplier_vat_id")] == "'2026-10-01"


def test_formula_in_a_date_field_is_still_neutralised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = publish(
        tmp_path,
        monkeypatch,
        ready=1,
        review=0,
        invoice_date='=HYPERLINK("http://evil")',
    )

    assert (
        written(sheets)[1][COLUMNS.index("invoice_date")]
        == '\'=HYPERLINK("http://evil")'
    )


@pytest.mark.parametrize(
    "raw",
    [
        "2026-13-45",
        "2026-02-30",
        "\uff12\uff10\uff12\uff16-10-01",
        "2026-10-01T00:00:00",
    ],
)
def test_date_shaped_but_not_real_dates_stay_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    sheets = publish(tmp_path, monkeypatch, ready=1, review=0, invoice_date=raw)

    assert written(sheets)[1][COLUMNS.index("invoice_date")] == "'" + raw
