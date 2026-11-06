from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from invoice_pdf_to_sheet import extract
from invoice_pdf_to_sheet.cli import main
from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow
from invoice_pdf_to_sheet.xlsx import read_xlsx, write_xlsx


def row(pdf_dir: Path, name: str, **fields: Any) -> ResultRow:
    return ResultRow(file=name, pdf_path=pdf_dir / name, **fields)


def as_data(item: ResultRow) -> dict[str, Any]:
    return item.model_dump(exclude={"pdf_path"})


def roundtrip(
    tmp_path: Path, ready: list[ResultRow], review: list[ResultRow]
) -> tuple[list[ResultRow], list[ResultRow]]:
    xlsx = tmp_path / "out" / "r.xlsx"
    write_xlsx(ready, review, xlsx)
    return read_xlsx(xlsx, tmp_path)


def test_every_column_survives_a_round_trip(tmp_path: Path) -> None:
    full = row(
        tmp_path,
        "a.pdf",
        supplier_name="Verdant Ridge BV",
        supplier_vat_id="NL123456789B01",
        invoice_number="VR-2026-0101",
        invoice_date="2026-10-01",
        due_date="2026-10-31",
        currency="EUR",
        net_total=250.5,
        vat_total=52.61,
        gross_total=303.11,
        line_count=3,
        method="text",
        confidence="verified",
        evidence="supplier_name p1: Verdant Ridge; gross_total p1: 303.11",
    )

    ready, review = roundtrip(tmp_path, [full], [])

    assert review == []
    assert ready == [full]
    assert set(as_data(ready[0])) == set(COLUMNS[:-1])


def test_tabs_stay_on_their_side_in_order(tmp_path: Path) -> None:
    ready_rows = [row(tmp_path, "z.pdf"), row(tmp_path, "a.pdf")]
    review_rows = [row(tmp_path, "m.pdf", reasons=["r"])]

    ready, review = roundtrip(tmp_path, ready_rows, review_rows)

    assert [r.file for r in ready] == ["z.pdf", "a.pdf"]
    assert [r.file for r in review] == ["m.pdf"]
    assert review[0].reasons == ["r"]


def test_pdf_path_points_into_the_given_pdf_dir(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "invoices"
    xlsx = tmp_path / "r.xlsx"
    write_xlsx([row(pdf_dir, "a.pdf")], [row(pdf_dir, "b.pdf", reasons=["r"])], xlsx)

    ready, review = read_xlsx(xlsx, pdf_dir)

    assert ready[0].pdf_path == pdf_dir / "a.pdf"
    assert review[0].pdf_path == pdf_dir / "b.pdf"


def test_none_cells_come_back_as_none_and_defaults(tmp_path: Path) -> None:
    ready, _ = roundtrip(tmp_path, [row(tmp_path, "bare.pdf")], [])

    assert as_data(ready[0]) == as_data(row(tmp_path, "bare.pdf"))
    assert ready[0].supplier_name is None
    assert ready[0].net_total is None
    assert ready[0].line_count is None
    assert ready[0].method == "none"
    assert ready[0].confidence == "none"


def test_zero_values_are_not_mistaken_for_empty_cells(tmp_path: Path) -> None:
    zeros = row(tmp_path, "z.pdf", net_total=0.0, vat_total=0.0, line_count=0)

    ready, _ = roundtrip(tmp_path, [zeros], [])

    assert ready[0].net_total == 0.0
    assert ready[0].vat_total == 0.0
    assert ready[0].line_count == 0
    assert ready[0].gross_total is None


@pytest.mark.parametrize("amount", [250.0, 0.0, -10.0, 1000000.0, 7.0])
def test_whole_number_amounts_come_back_as_floats(
    tmp_path: Path, amount: float
) -> None:
    ready, _ = roundtrip(tmp_path, [row(tmp_path, "w.pdf", gross_total=amount)], [])

    assert ready[0].gross_total == amount
    assert isinstance(ready[0].gross_total, float)


def test_line_count_stays_an_int(tmp_path: Path) -> None:
    ready, _ = roundtrip(tmp_path, [row(tmp_path, "l.pdf", line_count=4)], [])

    assert ready[0].line_count == 4
    assert isinstance(ready[0].line_count, int)


def test_reasons_split_back_into_the_same_list(tmp_path: Path) -> None:
    reasons = ["lines sum 1 != net 2", "missing supplier VAT id", "duplicate of a.pdf"]

    _, review = roundtrip(tmp_path, [], [row(tmp_path, "r.pdf", reasons=reasons)])

    assert review[0].reasons == reasons


def test_reasons_with_semicolons_and_commas_keep_their_entry_count(
    tmp_path: Path,
) -> None:
    reasons = ["a; b", "c,d", "e;f", "g; h; i"]

    _, review = roundtrip(tmp_path, [], [row(tmp_path, "r.pdf", reasons=reasons)])

    assert len(review[0].reasons) == 4
    assert review[0].reasons == [
        "a; b",
        "c,d",
        "e;f",
        "g; h; i",
    ]


def test_no_reasons_is_an_empty_list_not_a_blank_entry(tmp_path: Path) -> None:
    ready, _ = roundtrip(tmp_path, [row(tmp_path, "r.pdf")], [])

    assert ready[0].reasons == []


def test_empty_evidence_comes_back_as_empty_string(tmp_path: Path) -> None:
    ready, _ = roundtrip(tmp_path, [row(tmp_path, "e.pdf", evidence="")], [])

    assert ready[0].evidence == ""


def test_evidence_text_is_kept_as_is(tmp_path: Path) -> None:
    evidence = "invoice_number p1: VR-1; gross_total p2: 3.00"

    ready, _ = roundtrip(tmp_path, [row(tmp_path, "e.pdf", evidence=evidence)], [])

    assert ready[0].evidence == evidence


def test_both_tabs_empty_give_two_empty_lists(tmp_path: Path) -> None:
    assert roundtrip(tmp_path, [], []) == ([], [])


def test_one_empty_tab_does_not_affect_the_other(tmp_path: Path) -> None:
    only_review = roundtrip(tmp_path, [], [row(tmp_path, "r.pdf", reasons=["r"])])
    only_ready = roundtrip(tmp_path, [row(tmp_path, "a.pdf")], [])

    assert (len(only_review[0]), len(only_review[1])) == (0, 1)
    assert (len(only_ready[0]), len(only_ready[1])) == (1, 0)


def test_formula_and_number_like_text_stays_text(tmp_path: Path) -> None:
    tricky = row(
        tmp_path,
        "=1+1.pdf",
        supplier_name="2026",
        invoice_number="0001234",
        supplier_vat_id="=A1",
        currency="-5",
    )

    ready, _ = roundtrip(tmp_path, [tricky], [])

    assert ready[0].file == "=1+1.pdf"
    assert ready[0].supplier_name == "2026"
    assert ready[0].invoice_number == "0001234"
    assert ready[0].supplier_vat_id == "=A1"
    assert ready[0].currency == "-5"
    assert ready[0].pdf_path == tmp_path / "=1+1.pdf"


def test_many_rows_round_trip_in_order(tmp_path: Path) -> None:
    rows = [row(tmp_path, f"{i:03}.pdf", gross_total=float(i)) for i in range(300)]

    ready, _ = roundtrip(tmp_path, rows, [])

    assert [r.file for r in ready] == [r.file for r in rows]
    assert [r.gross_total for r in ready] == [float(i) for i in range(300)]


def test_pdf_link_column_is_ignored_when_reading(tmp_path: Path) -> None:
    xlsx = tmp_path / "r.xlsx"
    write_xlsx([row(tmp_path, "a.pdf")], [], xlsx)

    ready, _ = read_xlsx(xlsx, tmp_path)

    assert "pdf_link" not in as_data(ready[0])


def test_a_book_without_the_two_tabs_is_rejected(tmp_path: Path) -> None:
    xlsx = tmp_path / "other.xlsx"
    Workbook().save(xlsx)

    with pytest.raises(KeyError):
        read_xlsx(xlsx, tmp_path)


def test_blank_rows_below_the_data_are_skipped(tmp_path: Path) -> None:
    book = Workbook()
    ready_sheet = book.worksheets[0]
    ready_sheet.title = "Ready"
    ready_sheet.append(COLUMNS)
    ready_sheet.append(["a.pdf"])
    ready_sheet.append([])
    book.create_sheet("Needs review").append(COLUMNS)
    xlsx = tmp_path / "gaps.xlsx"
    book.save(xlsx)

    ready, review = read_xlsx(xlsx, tmp_path)

    assert [r.file for r in ready] == ["a.pdf"]
    assert review == []


def never_called(*_: Any, **__: Any) -> None:
    raise AssertionError("the model must not be called by publish")


@pytest.fixture
def no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "call_model", never_called)
    monkeypatch.setattr(extract, "extract_file", never_called)


def test_publish_with_a_missing_xlsx_exits_2_and_never_calls_the_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_model: None,
) -> None:
    monkeypatch.setattr("invoice_pdf_to_sheet.google_sheet.publish", never_called)
    missing = tmp_path / "nope.xlsx"

    code = main(["publish", "--xlsx", str(missing), "--input", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == f"no xlsx at {missing}\n"


def test_publish_with_a_directory_as_xlsx_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_model: None
) -> None:
    code = main(["publish", "--xlsx", str(tmp_path), "--input", str(tmp_path)])

    assert code == 2
    assert "no xlsx at" in capsys.readouterr().err


def test_publish_sends_the_xlsx_rows_to_google_without_the_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_model: None,
) -> None:
    pdfs = tmp_path / "invoices"
    pdfs.mkdir()
    xlsx = tmp_path / "r.xlsx"
    write_xlsx(
        [row(pdfs, "a.pdf", gross_total=5.0)],
        [row(pdfs, "b.pdf", reasons=["x; y"])],
        xlsx,
    )
    seen: list[tuple[list[ResultRow], list[ResultRow]]] = []

    def fake_publish(ready: list[ResultRow], review: list[ResultRow]) -> str:
        seen.append((ready, review))
        return "https://sheet.example/S1"

    monkeypatch.setattr("invoice_pdf_to_sheet.google_sheet.publish", fake_publish)

    code = main(["publish", "--xlsx", str(xlsx), "--input", str(pdfs)])

    assert code == 0
    assert capsys.readouterr().out.splitlines() == [
        "Google Sheet: https://sheet.example/S1"
    ]
    [(ready, review)] = seen
    assert [(r.file, r.pdf_path, r.gross_total) for r in ready] == [
        ("a.pdf", pdfs / "a.pdf", 5.0)
    ]
    assert [(r.file, r.pdf_path, r.reasons) for r in review] == [
        ("b.pdf", pdfs / "b.pdf", ["x; y"])
    ]


def test_publish_with_empty_tabs_still_publishes_two_empty_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_model: None
) -> None:
    xlsx = tmp_path / "r.xlsx"
    write_xlsx([], [], xlsx)
    seen: list[tuple[list[ResultRow], list[ResultRow]]] = []

    def fake_publish(ready: list[ResultRow], review: list[ResultRow]) -> str:
        seen.append((ready, review))
        return "u"

    monkeypatch.setattr("invoice_pdf_to_sheet.google_sheet.publish", fake_publish)

    assert main(["publish", "--xlsx", str(xlsx), "--input", str(tmp_path)]) == 0
    assert seen == [([], [])]


def test_publish_upload_failure_exits_1_with_the_type_name_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_model: None,
) -> None:
    xlsx = tmp_path / "r.xlsx"
    write_xlsx([row(tmp_path, "a.pdf")], [], xlsx)

    def boom(ready: list[ResultRow], review: list[ResultRow]) -> str:
        raise PermissionError("SECRET_PROVIDER_BODY_77")

    monkeypatch.setattr("invoice_pdf_to_sheet.google_sheet.publish", boom)

    code = main(["publish", "--xlsx", str(xlsx), "--input", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == "Google Sheet upload failed: PermissionError\n"
    assert "SECRET" not in captured.out + captured.err


@pytest.mark.parametrize("missing", ["--xlsx", "--input"])
def test_publish_requires_both_options(
    tmp_path: Path, missing: str, no_model: None
) -> None:
    argv = ["publish", "--xlsx", str(tmp_path / "r.xlsx"), "--input", str(tmp_path)]
    del argv[argv.index(missing) : argv.index(missing) + 2]

    with pytest.raises(SystemExit) as exit_info:
        main(argv)

    assert exit_info.value.code == 2
