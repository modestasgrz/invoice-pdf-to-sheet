import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from invoice_pdf_to_sheet import google_sheet
from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow
from invoice_pdf_to_sheet.xlsx import WIDTHS

LINK = "https://drive.example/x"


def run(
    monkeypatch: pytest.MonkeyPatch, ready: list[ResultRow], review: list[ResultRow]
) -> MagicMock:
    sheets = MagicMock()
    sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
        "spreadsheetId": "S1",
        "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/S1",
    }
    drive = MagicMock()
    drive.files.return_value.create.return_value.execute.return_value = {
        "id": "F",
        "webViewLink": LINK,
    }
    monkeypatch.setattr(google_sheet, "load_credentials", lambda: object())
    monkeypatch.setattr(
        google_sheet,
        "build",
        lambda name, version, credentials: drive if name == "drive" else sheets,
    )
    google_sheet.publish(ready, review)
    return sheets


def make_row(tmp_path: Path, name: str = "a.pdf", **fields: Any) -> ResultRow:
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.4")
    return ResultRow(file=name, pdf_path=pdf, **fields)


def tabs(sheets: MagicMock) -> list[dict[str, Any]]:
    body: dict[str, Any] = sheets.spreadsheets.return_value.create.call_args.kwargs[
        "body"
    ]
    result: list[dict[str, Any]] = body["sheets"]
    return result


def link_cell(tab: dict[str, Any], row: int) -> dict[str, Any]:
    cell: dict[str, Any] = tab["data"][0]["rowData"][row + 1]["values"][
        COLUMNS.index("pdf_link")
    ]
    return cell


def written(sheets: MagicMock) -> list[list[Any]]:
    data = sheets.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs[
        "body"
    ]["data"]
    result: list[list[Any]] = [d["values"] for d in data]
    return result


def test_no_rows_at_all_still_gives_header_only_tabs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sheets = run(monkeypatch, [], [])

    for tab in tabs(sheets):
        assert tab["basicFilter"]["range"]["endRowIndex"] == 1
        assert len(tab["data"][0]["rowData"]) == 1
    assert written(sheets) == [[COLUMNS], [COLUMNS]]


def test_zero_ready_rows_leaves_review_tab_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [], [make_row(tmp_path), make_row(tmp_path, "b.pdf")])

    ready, review = tabs(sheets)
    assert ready["basicFilter"]["range"]["endRowIndex"] == 1
    assert len(ready["data"][0]["rowData"]) == 1
    assert review["basicFilter"]["range"]["endRowIndex"] == 3
    assert len(review["data"][0]["rowData"]) == 3
    assert len(written(sheets)[0]) == 1
    assert len(written(sheets)[1]) == 3


def test_zero_review_rows_leaves_ready_tab_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path)], [])

    ready, review = tabs(sheets)
    assert ready["basicFilter"]["range"]["endRowIndex"] == 2
    assert len(ready["data"][0]["rowData"]) == 2
    assert review["basicFilter"]["range"]["endRowIndex"] == 1
    assert len(review["data"][0]["rowData"]) == 1
    assert len(written(sheets)[1]) == 1


def test_many_rows_filter_row_data_and_values_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [make_row(tmp_path, f"{i}.pdf", gross_total=float(i)) for i in range(750)]
    sheets = run(monkeypatch, rows, rows[:3])

    ready, review = tabs(sheets)
    assert ready["basicFilter"]["range"]["endRowIndex"] == 751
    assert len(ready["data"][0]["rowData"]) == 751
    assert review["basicFilter"]["range"]["endRowIndex"] == 4
    assert len(written(sheets)[0]) == 751
    assert all(len(r) == len(COLUMNS) - 1 for r in written(sheets)[0][1:])
    for i in range(750):
        assert link_cell(ready, i) == {
            "userEnteredValue": {"stringValue": f"{i}.pdf"},
            "userEnteredFormat": {"textFormat": {"link": {"uri": LINK}}},
        }
        assert (
            ready["data"][0]["rowData"][i + 1]["values"][:-1]
            == (ready["data"][0]["rowData"][1]["values"][:-1])
        )


def test_every_column_has_a_width_and_widths_are_positive_pixels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert set(COLUMNS) <= set(WIDTHS)
    sheets = run(monkeypatch, [make_row(tmp_path)], [])

    for tab in tabs(sheets):
        sizes = [c["pixelSize"] for c in tab["data"][0]["columnMetadata"]]
        assert sizes == [WIDTHS[c] * 7 + 10 for c in COLUMNS]
        assert all(isinstance(s, int) and s > 0 for s in sizes)


def test_every_row_has_one_format_per_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path)] * 2, [make_row(tmp_path)])

    for tab in tabs(sheets):
        for row in tab["data"][0]["rowData"]:
            assert len(row["values"]) == len(COLUMNS)


def test_evidence_and_reasons_wrap_and_nothing_else_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path)], [])

    cells = tabs(sheets)[0]["data"][0]["rowData"][1]["values"]
    for column, cell in zip(COLUMNS, cells, strict=True):
        fmt = cell["userEnteredFormat"]
        if column in ("evidence", "reasons"):
            assert fmt == {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}
        else:
            assert "wrapStrategy" not in fmt
    header = tabs(sheets)[0]["data"][0]["rowData"][0]["values"]
    assert all("wrapStrategy" not in c["userEnteredFormat"] for c in header)


def test_money_and_date_columns_are_not_wrapped_and_other_columns_have_no_number_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path)], [])

    cells = tabs(sheets)[0]["data"][0]["rowData"][1]["values"]
    formatted = {"net_total", "vat_total", "gross_total", "invoice_date", "due_date"}
    for column, cell in zip(COLUMNS, cells, strict=True):
        assert ("numberFormat" in cell["userEnteredFormat"]) == (column in formatted)


def test_none_dates_are_empty_cells_not_text_or_apostrophes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path)], [])

    row = written(sheets)[0][1]
    assert row[COLUMNS.index("invoice_date")] == ""
    assert row[COLUMNS.index("due_date")] == ""


def test_one_date_none_other_iso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path, invoice_date="2026-10-01")], [])

    row = written(sheets)[0][1]
    assert row[COLUMNS.index("invoice_date")] == "2026-10-01"
    assert row[COLUMNS.index("due_date")] == ""


@pytest.mark.parametrize(
    "value",
    [
        "31.10.2026",
        "2026/10/01",
        "2026-1-5",
        "20261001",
        "2026-10-01 ",
        " 2026-10-01",
        "2026-10-01\n",
        "1/10/2026",
        "Oct 1, 2026",
        "2026-10-01T00:00:00",
        "2026-10-01; 2026-10-02",
        "2026-02-30",
        "2026-13-01",
        "٢٠٢٦-١٠-٠١",
        "-2026-10-01",
        "+2026-10-01",
    ],
)
def test_non_iso_dates_are_kept_as_typed_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    sheets = run(
        monkeypatch,
        [make_row(tmp_path, invoice_date=value, due_date=value)],
        [make_row(tmp_path, invoice_date=value)],
    )

    ready, review = written(sheets)
    cell = ready[1][COLUMNS.index("invoice_date")]
    assert cell != value
    assert cell == google_sheet.as_text(value)
    assert ready[1][COLUMNS.index("due_date")] == cell
    assert review[1][COLUMNS.index("invoice_date")] == cell


@pytest.mark.parametrize(
    "value",
    [
        "=1+1",
        "+1",
        "-1",
        "@SUM(A1)",
        "=2026-10-01",
        '=HYPERLINK("http://evil")',
        "  =1+1",
    ],
)
def test_formula_like_dates_and_fields_are_neutralised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    row = make_row(
        tmp_path,
        invoice_date=value,
        due_date=value,
        supplier_name=value,
        invoice_number=value,
        currency=value,
        evidence=value,
        reasons=[value],
    )
    sheets = run(monkeypatch, [row], [row])

    for tab in written(sheets):
        cells = tab[1]
        for column in (
            "invoice_date",
            "due_date",
            "supplier_name",
            "invoice_number",
            "currency",
            "evidence",
            "reasons",
        ):
            cell = cells[COLUMNS.index(column)]
            assert isinstance(cell, str)
            assert cell == google_sheet.as_text(value)
            assert not cell.startswith(("=", "+", "-", "@"))


def test_formula_like_file_name_is_neutralised_in_the_file_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path, "=1+1.pdf")], [])

    assert written(sheets)[0][1][0] == "'=1+1.pdf"


def test_numeric_looking_text_in_text_columns_stays_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(
        monkeypatch,
        [make_row(tmp_path, invoice_number="0001234", supplier_vat_id="TRUE")],
        [],
    )

    row = written(sheets)[0][1]
    assert row[COLUMNS.index("invoice_number")] == "'0001234"
    assert row[COLUMNS.index("supplier_vat_id")] == "'TRUE"


def test_pdf_link_cell_is_text_with_a_link_in_the_create_body_not_a_formula(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path)], [make_row(tmp_path, "b.pdf")])

    ready, review = written(sheets)
    assert COLUMNS[-1] == "pdf_link"
    assert len(ready[1]) == len(COLUMNS) - 1
    assert len(review[1]) == len(COLUMNS) - 1
    ready_tab, review_tab = tabs(sheets)
    assert len(ready_tab["data"][0]["rowData"][1]["values"]) == len(COLUMNS)
    assert link_cell(ready_tab, 0) == {
        "userEnteredValue": {"stringValue": "a.pdf"},
        "userEnteredFormat": {"textFormat": {"link": {"uri": LINK}}},
    }
    assert link_cell(review_tab, 0) == {
        "userEnteredValue": {"stringValue": "b.pdf"},
        "userEnteredFormat": {"textFormat": {"link": {"uri": LINK}}},
    }


def test_pdf_link_label_with_quotes_stays_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path, 'we"ird.pdf')], [])

    assert link_cell(tabs(sheets)[0], 0)["userEnteredValue"] == {
        "stringValue": 'we"ird.pdf'
    }
    assert "HYPERLINK" not in json.dumps(sheets.spreadsheets.mock_calls, default=str)


def test_missing_and_symlinked_pdfs_say_not_uploaded_without_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "real.pdf"
    target.write_bytes(b"%PDF-1.4")
    link = tmp_path / "link.pdf"
    link.symlink_to(target)
    missing = ResultRow(file="gone.pdf", pdf_path=tmp_path / "gone.pdf")
    symlinked = ResultRow(file="link.pdf", pdf_path=link)
    sheets = run(monkeypatch, [missing], [symlinked])

    ready_tab, review_tab = tabs(sheets)
    assert link_cell(ready_tab, 0) == {
        "userEnteredValue": {"stringValue": "(not uploaded)"}
    }
    assert link_cell(review_tab, 0) == {
        "userEnteredValue": {"stringValue": "(not uploaded)"}
    }


def test_link_cells_follow_row_order_when_some_rows_are_not_uploaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = ResultRow(file="gone.pdf", pdf_path=tmp_path / "gone.pdf")
    rows = [make_row(tmp_path, "a.pdf"), gone, make_row(tmp_path, "c.pdf")]
    sheets = run(monkeypatch, rows, [])

    tab = tabs(sheets)[0]
    assert [link_cell(tab, i)["userEnteredValue"]["stringValue"] for i in range(3)] == [
        "a.pdf",
        "(not uploaded)",
        "c.pdf",
    ]
    assert [("userEnteredFormat" in link_cell(tab, i)) for i in range(3)] == [
        True,
        False,
        True,
    ]


def test_no_written_value_starts_with_an_equals_sign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evil = '=HYPERLINK("http://evil")'
    rows = [
        make_row(
            tmp_path,
            "=1+1.pdf",
            supplier_name=evil,
            supplier_vat_id="=A1",
            invoice_number="=2*3",
            invoice_date="=NOW()",
            due_date=" =1",
            currency="=x",
            evidence=evil,
            reasons=[evil, "=y"],
        ),
        make_row(
            tmp_path,
            "=HYPERLINK(x).pdf",
            supplier_name="+cmd",
            invoice_date="2026-10-01",
        ),
    ]
    sheets = run(monkeypatch, rows, rows)

    batch = sheets.spreadsheets.return_value.values.return_value.batchUpdate
    assert batch.call_count == 1
    for data in batch.call_args.kwargs["body"]["data"]:
        for row in data["values"]:
            for value in row:
                assert not (isinstance(value, str) and value.startswith("="))
    for tab in tabs(sheets):
        for row in tab["data"][0]["rowData"]:
            for cell in row["values"]:
                assert "formulaValue" not in cell.get("userEnteredValue", {})


def test_header_row_ends_with_pdf_link_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path)], [])

    assert written(sheets)[0][0][-1] == "pdf_link"


@pytest.mark.parametrize(
    ("amount", "text"),
    [(float("nan"), "nan"), (float("inf"), "inf"), (float("-inf"), "-inf")],
)
def test_non_finite_amounts_go_as_text_and_payload_stays_valid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, amount: float, text: str
) -> None:
    row = make_row(
        tmp_path, gross_total=amount, net_total=amount, vat_total=1.5, line_count=2
    )
    sheets = run(monkeypatch, [row], [row])

    for tab in written(sheets):
        cells = tab[1]
        assert cells[COLUMNS.index("gross_total")] == text
        assert cells[COLUMNS.index("net_total")] == text
        assert cells[COLUMNS.index("vat_total")] == 1.5
        assert cells[COLUMNS.index("line_count")] == 2
    body = sheets.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs[
        "body"
    ]
    json.dumps(body, allow_nan=False)
    json.dumps(tabs(sheets), allow_nan=False)


def test_non_finite_amounts_keep_their_money_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path, gross_total=float("nan"))], [])

    cells = tabs(sheets)[0]["data"][0]["rowData"][1]["values"]
    assert cells[COLUMNS.index("gross_total")]["userEnteredFormat"]["numberFormat"] == {
        "type": "NUMBER",
        "pattern": "#,##0.00",
    }


def test_none_amounts_are_empty_cells_and_zero_stays_a_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path, vat_total=0.0)], [])

    row = written(sheets)[0][1]
    assert row[COLUMNS.index("net_total")] == ""
    assert row[COLUMNS.index("vat_total")] == 0.0
    assert isinstance(row[COLUMNS.index("vat_total")], float)


def test_ready_and_review_tabs_get_different_header_fills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [], [])

    fills = [
        tab["data"][0]["rowData"][0]["values"][0]["userEnteredFormat"][
            "backgroundColorStyle"
        ]
        for tab in tabs(sheets)
    ]
    assert fills[0] != fills[1]
    for tab in tabs(sheets):
        header = tab["data"][0]["rowData"][0]["values"]
        assert len({json.dumps(c, sort_keys=True) for c in header}) == 1
