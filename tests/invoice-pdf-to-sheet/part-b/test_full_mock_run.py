import contextlib
import io
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

from invoice_pdf_to_sheet.cli import main
from invoice_pdf_to_sheet.schema import COLUMNS, InvoiceFields

ROOT = Path(__file__).resolve().parents[3]
INVOICES = ROOT / "data" / "invoices"
KEY: dict[str, dict[str, Any]] = json.loads(
    (ROOT / "data" / "answer_key.json").read_text()
)
SCAN_FILE = next(name for name, entry in KEY.items() if entry["scan"])
REVIEW_FILES = {
    "22_clovermere_CD-8119.pdf",
    "29_brightfold_BF-2178.pdf",
    "30_quillon_QE5546_resend.pdf",
}


def collapse(text: str) -> str:
    return " ".join(text.split())


def first_printed(candidates: list[str], text: str) -> str:
    for candidate in candidates:
        if candidate in collapse(text):
            return candidate
    raise AssertionError(f"none of {candidates} printed on the page")


def key_reply(entry: dict[str, Any], page_text: str) -> InvoiceFields:
    """The answer key as the model's reply; evidence quotes are copied from text actually on the page."""
    reply: dict[str, Any] = {
        field: entry[field]
        for field in (
            "supplier_name",
            "supplier_vat_id",
            "invoice_number",
            "invoice_date",
            "due_date",
            "currency",
            "net_total",
            "vat_total",
            "gross_total",
            "lines",
        )
    }
    reply["evidence"] = []
    if page_text:
        year, month, day = entry["invoice_date"].split("-")
        gross = f"{entry['gross_total']:.2f}"
        quotes = {
            "invoice_number": [entry["invoice_number"]],
            "invoice_date": [entry["invoice_date"], f"{day}.{month}.{year}"],
            "gross_total": [
                gross,
                gross.replace(".", ","),
                f"{entry['gross_total']:,.2f}",
                f"{entry['gross_total']:,.2f}".replace(",", "_")
                .replace(".", ",")
                .replace("_", "."),
            ],
        }
        if entry["supplier_vat_id"]:
            quotes["supplier_vat_id"] = [entry["supplier_vat_id"]]
        reply["evidence"] = [
            {"field": field, "page": 1, "quote": first_printed(candidates, page_text)}
            for field, candidates in quotes.items()
        ]
    return InvoiceFields.model_validate(reply)


class KeyModel:
    def __init__(self) -> None:
        self.calls = 0
        self.image_calls = 0

    def __call__(self, system: str, content: list[dict[str, Any]]) -> InvoiceFields:
        self.calls += 1
        if any(block["type"] == "image" for block in content):
            self.image_calls += 1
            return key_reply(KEY[SCAN_FILE], "")
        text = "\n".join(block["text"] for block in content if block["type"] == "text")
        matches = [
            entry
            for entry in KEY.values()
            if not entry["scan"]
            and entry["invoice_number"] in text
            and entry["supplier_name"] in text
        ]
        assert matches
        return key_reply(matches[0], text)


def run_cli(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(argv)
    return code, out.getvalue()


def read_book(path: Path) -> dict[str, list[list[Any]]]:
    """Every cell value plus the pdf_link target, per sheet."""
    book = load_workbook(path)
    sheets: dict[str, list[list[Any]]] = {}
    for sheet in book.worksheets:
        rows: list[list[Any]] = []
        for row in sheet.iter_rows():
            cells = [cell.value for cell in row]
            link = row[-1].hyperlink
            cells.append(link.target if link else None)
            rows.append(cells)
        sheets[sheet.title] = rows
    return sheets


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    out_dir = tmp_path_factory.mktemp("out")
    model = KeyModel()
    results: dict[str, Any] = {"dir": out_dir, "model": model}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("invoice_pdf_to_sheet.extract.call_model", model)
        for workers in (1, 4):
            xlsx = out_dir / f"w{workers}.xlsx"
            code, stdout = run_cli(
                [
                    "run",
                    "--input",
                    str(INVOICES),
                    "--xlsx",
                    str(xlsx),
                    "--workers",
                    str(workers),
                ]
            )
            results[f"w{workers}"] = {"code": code, "stdout": stdout, "xlsx": xlsx}
    yield results


def test_full_run_routes_exactly_the_planted_files(runs: dict[str, Any]) -> None:
    run = runs["w4"]
    assert run["code"] == 0
    assert "30 invoices: 27 ready, 3 needs review" in run["stdout"]
    book = read_book(run["xlsx"])
    assert list(book) == ["Ready", "Needs review"]
    ready = {row[0] for row in book["Ready"][1:]}
    review = {row[0] for row in book["Needs review"][1:]}
    assert review == REVIEW_FILES
    assert ready == set(KEY) - REVIEW_FILES
    assert runs["model"].calls == 60
    assert runs["model"].image_calls == 2


def test_full_run_review_reasons_are_exact(runs: dict[str, Any]) -> None:
    book = read_book(runs["w4"]["xlsx"])
    reasons = {
        row[0]: row[COLUMNS.index("reasons")] for row in book["Needs review"][1:]
    }
    lines = KEY["29_brightfold_BF-2178.pdf"]["lines"]
    total = sum(line["line_total"] for line in lines)
    net = KEY["29_brightfold_BF-2178.pdf"]["net_total"]
    assert reasons == {
        "22_clovermere_CD-8119.pdf": "missing supplier VAT id",
        "29_brightfold_BF-2178.pdf": f"lines sum {total:.2f} != net {net:.2f}",
        "30_quillon_QE5546_resend.pdf": "duplicate of 25_quillon_QE5546.pdf",
    }


def test_full_run_ready_rows_method_confidence_and_special_files(
    runs: dict[str, Any],
) -> None:
    book = read_book(runs["w4"]["xlsx"])
    by_file = {
        row[0]: dict(zip(COLUMNS, row, strict=False)) for row in book["Ready"][1:]
    }
    for name, row in by_file.items():
        if name == SCAN_FILE:
            assert (row["method"], row["confidence"]) == ("vision", "vision")
        else:
            assert (row["method"], row["confidence"]) == ("text", "verified")
        assert row["reasons"] in (None, "")
    injection = KEY["12_tamberlane_TES-4389.pdf"]
    assert (
        by_file["12_tamberlane_TES-4389.pdf"]["gross_total"] == injection["gross_total"]
    )
    assert (
        by_file["12_tamberlane_TES-4389.pdf"]["line_count"] == injection["line_count"]
    )


def test_full_run_hyperlinks_resolve_to_the_source_pdfs(runs: dict[str, Any]) -> None:
    out_dir: Path = runs["dir"]
    book = read_book(runs["w4"]["xlsx"])
    for rows in book.values():
        assert rows[0][: len(COLUMNS)] == COLUMNS
        for row in rows[1:]:
            target = (out_dir / row[-1]).resolve()
            assert target == (INVOICES / row[0]).resolve()
            assert target.exists()


def test_full_run_leaves_input_folder_untouched() -> None:
    assert sorted(p.suffix for p in INVOICES.iterdir()) == [".pdf"] * 30


def test_workers_1_and_4_give_identical_workbooks(runs: dict[str, Any]) -> None:
    assert runs["w1"]["code"] == runs["w4"]["code"] == 0
    assert read_book(runs["w1"]["xlsx"]) == read_book(runs["w4"]["xlsx"])
    strip = re.compile(r"^xlsx: .*$", re.MULTILINE)
    assert strip.sub("", runs["w1"]["stdout"]) == strip.sub("", runs["w4"]["stdout"])


def test_rows_are_in_sorted_file_order_within_each_tab(runs: dict[str, Any]) -> None:
    book = read_book(runs["w4"]["xlsx"])
    for rows in book.values():
        names = [row[0] for row in rows[1:]]
        assert names == sorted(names)


def test_eval_scores_the_full_run_perfectly(runs: dict[str, Any]) -> None:
    code, stdout = run_cli(
        [
            "eval",
            "--xlsx",
            str(runs["w4"]["xlsx"]),
            "--key",
            str(ROOT / "data" / "answer_key.json"),
        ]
    )
    assert code == 0
    assert "field accuracy: 300/300 = 100.0%" in stdout
    assert "routing: 30/30 files in the expected tab" in stdout
    assert stdout.count(": 30/30") == 11
