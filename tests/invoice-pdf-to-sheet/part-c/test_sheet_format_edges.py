import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

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


def link_requests(sheets: MagicMock) -> dict[int, dict[str, Any]]:
    """The updateCells request per sheetId of the one spreadsheets().batchUpdate call (empty when it was never made)."""
    batch = sheets.spreadsheets.return_value.batchUpdate
    if not batch.called:
        return {}
    assert batch.call_count == 1
    assert batch.call_args.kwargs["spreadsheetId"] == "S1"
    requests: list[dict[str, Any]] = batch.call_args.kwargs["body"]["requests"]
    assert all(set(r) == {"updateCells"} for r in requests)
    result = {r["updateCells"]["range"]["sheetId"]: r["updateCells"] for r in requests}
    assert len(result) == len(requests)
    return result


def link_cells(sheets: MagicMock, sheet_id: int) -> list[dict[str, Any]]:
    request = link_requests(sheets)[sheet_id]
    link = COLUMNS.index("pdf_link")
    assert request["fields"] == "userEnteredValue,textFormatRuns"
    assert request["range"] == {
        "sheetId": sheet_id,
        "startRowIndex": 1,
        "endRowIndex": len(request["rows"]) + 1,
        "startColumnIndex": link,
        "endColumnIndex": link + 1,
    }
    assert all(len(r["values"]) == 1 for r in request["rows"])
    cells: list[dict[str, Any]] = [r["values"][0] for r in request["rows"]]
    return cells


def linked(name: str, uri: str = LINK) -> dict[str, Any]:
    return {
        "userEnteredValue": {"stringValue": name},
        "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": uri}}}],
    }


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
    assert link_cells(sheets, 0) == [linked(f"{i}.pdf") for i in range(750)]
    assert link_cells(sheets, 1) == [linked(f"{i}.pdf") for i in range(3)]
    rows = ready["data"][0]["rowData"]
    assert all(len(r["values"]) == len(COLUMNS) - 1 for r in rows[1:])
    assert all(r == rows[1] for r in rows[1:])


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
        header, *data = tab["data"][0]["rowData"]
        assert len(header["values"]) == len(COLUMNS)
        assert data
        assert all(len(row["values"]) == len(COLUMNS) - 1 for row in data)


def test_evidence_and_reasons_wrap_and_nothing_else_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path)], [])

    cells = tabs(sheets)[0]["data"][0]["rowData"][1]["values"]
    for column, cell in zip(COLUMNS[:-1], cells, strict=True):
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
    for column, cell in zip(COLUMNS[:-1], cells, strict=True):
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


def test_pdf_link_cells_are_text_with_a_link_run_in_a_separate_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path)], [make_row(tmp_path, "b.pdf")])

    ready, review = written(sheets)
    assert COLUMNS[-1] == "pdf_link"
    assert len(ready[0]) == len(COLUMNS)
    assert len(ready[1]) == len(COLUMNS) - 1
    assert len(review[1]) == len(COLUMNS) - 1
    for tab in tabs(sheets):
        assert len(tab["data"][0]["rowData"][1]["values"]) == len(COLUMNS) - 1
    assert link_cells(sheets, 0) == [linked("a.pdf")]
    assert link_cells(sheets, 1) == [linked("b.pdf")]
    assert "HYPERLINK" not in repr(sheets.mock_calls)


def test_values_are_written_before_the_link_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path)], [])

    calls = [
        c[0]
        for c in sheets.mock_calls
        if c[0].endswith("batchUpdate") or c[0].endswith("create")
    ]
    assert calls == [
        "spreadsheets().create",
        "spreadsheets().values().batchUpdate",
        "spreadsheets().batchUpdate",
    ]


def test_one_link_request_per_non_empty_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    only_ready = run(monkeypatch, [make_row(tmp_path)], [])
    only_review = run(monkeypatch, [], [make_row(tmp_path)])
    both = run(monkeypatch, [make_row(tmp_path)], [make_row(tmp_path)])

    assert list(link_requests(only_ready)) == [0]
    assert list(link_requests(only_review)) == [1]
    assert list(link_requests(both)) == [0, 1]


def test_no_link_update_at_all_when_both_tabs_are_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sheets = run(monkeypatch, [], [])

    sheets.spreadsheets.return_value.batchUpdate.assert_not_called()
    assert link_requests(sheets) == {}


def test_pdf_link_label_with_quotes_stays_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path, 'we"ird.pdf')], [])

    assert link_cells(sheets, 0) == [linked('we"ird.pdf')]
    assert "HYPERLINK" not in repr(sheets.mock_calls)


def test_missing_and_symlinked_pdfs_say_not_uploaded_without_link_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "real.pdf"
    target.write_bytes(b"%PDF-1.4")
    link = tmp_path / "link.pdf"
    link.symlink_to(target)
    missing = ResultRow(file="gone.pdf", pdf_path=tmp_path / "gone.pdf")
    symlinked = ResultRow(file="link.pdf", pdf_path=link)
    sheets = run(monkeypatch, [missing], [symlinked])

    not_uploaded = {"userEnteredValue": {"stringValue": "(not uploaded)"}}
    assert link_cells(sheets, 0) == [not_uploaded]
    assert link_cells(sheets, 1) == [not_uploaded]


def test_link_cells_follow_row_order_when_some_rows_are_not_uploaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = ResultRow(file="gone.pdf", pdf_path=tmp_path / "gone.pdf")
    rows = [make_row(tmp_path, "a.pdf"), gone, make_row(tmp_path, "c.pdf")]
    sheets = run(monkeypatch, rows, [])

    assert link_cells(sheets, 0) == [
        linked("a.pdf"),
        {"userEnteredValue": {"stringValue": "(not uploaded)"}},
        linked("c.pdf"),
    ]


def test_link_cell_text_is_the_raw_file_name_even_when_formula_like(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path, "=1+1.pdf")], [])

    assert link_cells(sheets, 0) == [linked("=1+1.pdf")]
    assert written(sheets)[0][1][0] == "'=1+1.pdf"


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
    for sheet_id in (0, 1):
        for cell in link_cells(sheets, sheet_id):
            assert set(cell["userEnteredValue"]) == {"stringValue"}


def test_header_row_ends_with_pdf_link_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheets = run(monkeypatch, [make_row(tmp_path)], [])

    assert written(sheets)[0][0][-1] == "pdf_link"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_amounts_never_reach_a_row(tmp_path: Path, bad: float) -> None:
    with pytest.raises(ValidationError):
        ResultRow(file="a.pdf", pdf_path=tmp_path / "a.pdf", gross_total=bad)


def test_as_text_still_neutralises_non_finite_numbers() -> None:
    assert google_sheet.as_text(float("-inf")) == "'-inf"
    assert google_sheet.as_text(float("nan")) == "nan"


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
