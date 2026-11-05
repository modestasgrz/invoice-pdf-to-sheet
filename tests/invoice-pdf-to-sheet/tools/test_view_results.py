import html
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest
from openpyxl import load_workbook

from invoice_pdf_to_sheet.evaluate import evaluate
from invoice_pdf_to_sheet.schema import ResultRow
from invoice_pdf_to_sheet.xlsx import write_xlsx

REPO = Path(__file__).resolve().parents[3]
VIEWER = REPO / "tools" / "view_results.py"

READY_FIELDS: dict[str, Any] = {
    "supplier_name": "Verdant Ridge Green Coffee Imports BV",
    "supplier_vat_id": "NL999000001B01",
    "invoice_number": "VR-1",
    "invoice_date": "2026-10-01",
    "due_date": "2026-10-31",
    "currency": "EUR",
    "net_total": 250.0,
    "vat_total": 52.5,
    "gross_total": 302.5,
    "line_count": 2,
    "method": "text",
    "confidence": "verified",
}

# (raw reason from DEC-06, text expected in the plain sentence, text expected in the to-do)
REASONS = [
    (
        "duplicate of 03_quillon.pdf",
        "Already received as 03_quillon.pdf",
        "Don't book or pay it twice",
    ),
    (
        "lines sum 100.00 != net 90.00",
        "add up to €100.00, but the invoice says the net total is €90.00",
        "corrected invoice",
    ),
    (
        "net + VAT 121.00 != gross 125.00",
        "Net + VAT is €121.00, but the invoice total says €125.00",
        "which total is right",
    ),
    (
        "missing supplier VAT id",
        "VAT number is not on the invoice",
        "VAT can't be reclaimed",
    ),
    (
        "invalid VAT id format: XX123",
        "VAT number 'XX123' doesn't look like a real EU VAT number",
        "supplier's record",
    ),
    (
        "supplier VAT id is buyer's own: LT999000099",
        "The VAT number on it (LT999000099) is your own",
        "ask the supplier to fix it",
    ),
    (
        "invalid invoice date: 32/13/2026",
        "invoice date '32/13/2026' isn't a real date",
        "Check the date on the PDF",
    ),
    (
        "invoice date in the future: 2030-01-01",
        "dated 2030-01-01, which is in the future",
        "Check the date on the PDF",
    ),
    (
        "invalid due date: soon",
        "due date 'soon' isn't a real date",
        "Check the due date on the PDF",
    ),
    (
        "due date before invoice date",
        "due date is before the invoice date",
        "Check both dates",
    ),
    (
        "evidence not found: gross_total",
        "couldn't point to where the gross_total is printed",
        "Check that value on the PDF",
    ),
    (
        "evidence does not match: invoice_number",
        "invoice_number that was read doesn't match the text it quoted",
        "Check that value on the PDF",
    ),
    (
        "extraction failed: APITimeoutError",
        "couldn't be read automatically (APITimeoutError)",
        "Type this one in by hand",
    ),
    (
        "file too large: 12.3 MB > 10 MB",
        "too large to read (12.3 MB > 10 MB)",
        "Type this one in by hand",
    ),
    (
        "too many pages: 6 > 5",
        "too many pages (6 > 5)",
        "Type this one in by hand",
    ),
    (
        "not a readable PDF",
        "file is not a readable PDF",
        "send it again",
    ),
]


def review_row(
    pdf: Path, reasons: list[str], evidence: str = "", **fields: Any
) -> ResultRow:
    base: dict[str, Any] = {
        **READY_FIELDS,
        "invoice_number": "VR-9",
        "confidence": "unverified",
    }
    return ResultRow(
        file=pdf.name,
        pdf_path=pdf,
        reasons=reasons,
        evidence=evidence,
        **{**base, **fields},
    )


def ready_row(pdf: Path, **fields: Any) -> ResultRow:
    return ResultRow(file=pdf.name, pdf_path=pdf, **{**READY_FIELDS, **fields})


def make_pdf(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / name
    pdf.write_bytes(b"%PDF-1.4 test")
    return pdf


def render(
    tmp_path: Path,
    ready: list[ResultRow],
    review: list[ResultRow],
    key: dict[str, dict[str, object]] | None = None,
    out: Path | None = None,
    xlsx: Path | None = None,
) -> tuple[str, Path, Path]:
    xlsx = xlsx or tmp_path / "out" / "invoices.xlsx"
    out = out or tmp_path / "out" / "results.html"
    write_xlsx(ready, review, xlsx)
    cmd = [sys.executable, str(VIEWER), str(xlsx), "--out", str(out)]
    if key is not None:
        key_path = tmp_path / "answer_key.json"
        key_path.write_text(json.dumps(key), encoding="utf-8")
        cmd += ["--key", str(key_path)]
    done = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert done.stdout.strip() == f"results page: {out}"
    return out.read_text(encoding="utf-8"), xlsx, out


def list_items(page: str) -> list[str]:
    return re.findall(r"<li>(.*?)</li>", page, flags=re.DOTALL)


def pdf_hrefs(page: str) -> list[str]:
    return [html.unescape(h) for h in re.findall(r"<a href='([^']*)'", page)]


def key_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        **{k: v for k, v in READY_FIELDS.items() if k not in ("method", "confidence")},
        "expected_tab": "Ready",
    }
    return {**entry, **overrides}


def load_viewer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("view_results_under_test", VIEWER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("reason", "said", "todo"), REASONS)
def test_every_documented_reason_maps_to_plain_words(
    tmp_path: Path, reason: str, said: str, todo: str
) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")

    page, _, _ = render(tmp_path, [], [review_row(pdf, [reason])])

    (item,) = list_items(page)
    assert said in html.unescape(item)
    assert todo in html.unescape(item)
    assert "technical reason" not in item
    assert "Check it on the PDF." not in item


def test_every_plain_pattern_is_covered_by_a_documented_reason() -> None:
    viewer = load_viewer()
    covered = {
        pattern
        for pattern, _, _ in viewer.PLAIN
        for reason, _, _ in REASONS
        if re.fullmatch(pattern, reason)
    }
    assert covered == {pattern for pattern, _, _ in viewer.PLAIN}


def test_unknown_reason_falls_back_to_raw_text_and_generic_todo(
    tmp_path: Path,
) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")

    page, _, _ = render(tmp_path, [], [review_row(pdf, ["something brand new: 42"])])

    (item,) = list_items(page)
    assert "something brand new: 42" in item
    assert "Check it on the PDF." in item


def test_reason_with_trailing_text_is_not_swallowed_by_a_partial_match(
    tmp_path: Path,
) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")

    page, _, _ = render(tmp_path, [], [review_row(pdf, ["not a readable PDF at all"])])

    (item,) = list_items(page)
    assert "not a readable PDF at all" in item
    assert "Ask the supplier to send it again." not in item


def test_multiple_reasons_in_one_row_make_one_card_with_one_item_each(
    tmp_path: Path,
) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")
    reasons = [
        "lines sum 100.00 != net 90.00",
        "missing supplier VAT id",
        "evidence not found: gross_total",
    ]

    page, _, _ = render(tmp_path, [], [review_row(pdf, reasons)])

    assert page.count("<div class='card'>") == 1
    assert len(list_items(page)) == 3
    assert "Needs you (1)" in page
    assert "technical reason: " + html.escape("; ".join(reasons)) in page


def test_one_card_per_review_row_with_counts_and_totals(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    review = [
        review_row(
            make_pdf(in_dir, "r1.pdf"), ["missing supplier VAT id"], gross_total=1234.5
        ),
        review_row(
            make_pdf(in_dir, "r2.pdf"), ["not a readable PDF"], gross_total=10.0
        ),
    ]
    ready = [
        ready_row(make_pdf(in_dir, "g1.pdf"), gross_total=1000.0),
        ready_row(make_pdf(in_dir, "g2.pdf"), gross_total=2500.25),
        ready_row(make_pdf(in_dir, "g3.pdf"), gross_total=0.5),
    ]

    page, _, _ = render(tmp_path, ready, review)

    assert page.count("<div class='card'>") == 2
    assert "<b>3</b><span>ready to book · €3,500.75</span>" in page
    assert "<b>2</b><span>need you · €1,244.50</span>" in page
    assert "Ready to book (3)" in page
    assert "5 PDFs read" in page


def test_extraction_failed_row_with_no_fields_does_not_crash(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in", "broken.pdf")
    row = ResultRow(
        file="broken.pdf", pdf_path=pdf, reasons=["extraction failed: APIError"]
    )

    page, _, _ = render(tmp_path, [], [row])

    assert "Unknown supplier" in page
    assert "no number read" in page
    assert "<div class='amt'>—</div>" in page
    assert "<b>1</b><span>need you · €0.00</span>" in page


def test_empty_review_tab_shows_nothing_to_check(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in", "g.pdf")

    page, _, _ = render(tmp_path, [ready_row(pdf)], [])

    assert "Needs you (0)" in page
    assert "Nothing to check. Every invoice passed." in page
    assert "<div class='card'>" not in page
    assert "<b>0</b><span>need you · €0.00</span>" in page


def test_empty_ready_tab_and_empty_review_tab_still_render(tmp_path: Path) -> None:
    page, _, _ = render(tmp_path, [], [])

    assert "Ready to book (0)" in page
    assert "0 PDFs read" in page
    assert "<tbody>\n</tbody>" in page


def test_ready_table_is_sorted_by_due_date_with_missing_due_last(
    tmp_path: Path,
) -> None:
    in_dir = tmp_path / "in"
    ready = [
        ready_row(
            make_pdf(in_dir, "c.pdf"), supplier_name="SUP-C", due_date="2026-12-01"
        ),
        ready_row(make_pdf(in_dir, "n.pdf"), supplier_name="SUP-NONE", due_date=None),
        ready_row(
            make_pdf(in_dir, "a.pdf"), supplier_name="SUP-A", due_date="2026-09-15"
        ),
        ready_row(
            make_pdf(in_dir, "b.pdf"), supplier_name="SUP-B", due_date="2026-10-20"
        ),
    ]

    page, _, _ = render(tmp_path, ready, [])

    order = re.findall(r"SUP-[A-Z]+", page.split("Ready to book")[1])
    assert order == ["SUP-A", "SUP-B", "SUP-C", "SUP-NONE"]


def test_ready_confidence_pills_use_plain_words(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    ready = [
        ready_row(make_pdf(in_dir, "t.pdf"), confidence="verified"),
        ready_row(make_pdf(in_dir, "v.pdf"), confidence="vision", method="vision"),
    ]

    page, _, _ = render(tmp_path, ready, [])

    assert "<span class='pill verified'>quotes checked</span>" in page
    assert "<span class='pill vision'>scan, read from image</span>" in page


EVIDENCE = (
    'invoice_number p1: "VR-9"; gross_total p3: "302,50"; supplier_vat_id p2: "NL1"'
)


@pytest.mark.parametrize(
    ("reason", "evidence", "page_number"),
    [
        ("lines sum 100.00 != net 90.00", EVIDENCE, 3),
        ("net + VAT 121.00 != gross 125.00", EVIDENCE, 3),
        ("missing supplier VAT id", EVIDENCE, 1),
        ("invalid VAT id format: XX1", EVIDENCE, 2),
        ("supplier VAT id is buyer's own: LT1", EVIDENCE, 2),
        ("duplicate of x.pdf", EVIDENCE, 1),
        ("not a readable PDF", "", 1),
        ("lines sum 1.00 != net 2.00", 'gross_total p12: "1"', 12),
        ("due date before invoice date", EVIDENCE, 1),
    ],
)
def test_page_link_points_at_the_page_of_the_relevant_evidence(
    tmp_path: Path, reason: str, evidence: str, page_number: int
) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")

    page, _, _ = render(tmp_path, [], [review_row(pdf, [reason], evidence)])

    (href,) = pdf_hrefs(page)
    assert href.endswith(f"#page={page_number}")
    assert f"Open PDF, page {page_number}" in page


@pytest.mark.parametrize(
    ("reason", "page_number"),
    [
        ("evidence does not match: gross_total", 3),
        ("invalid invoice date: 32/13/2026", 4),
    ],
)
def test_reason_that_names_a_field_links_to_that_fields_page(
    tmp_path: Path, reason: str, page_number: int
) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")
    evidence = 'invoice_number p1: "VR-9"; supplier_vat_id p2: "NL1"; gross_total p3: "1"; invoice_date p4: "32/13/2026"'

    page, _, _ = render(tmp_path, [], [review_row(pdf, [reason], evidence)])

    (href,) = pdf_hrefs(page)
    assert href.endswith(f"#page={page_number}")


def test_quote_text_that_looks_like_evidence_does_not_redirect_the_page(
    tmp_path: Path,
) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")
    evidence = 'gross_total p2: "302,50"; invoice_number p1: "x gross_total p9: y"'

    page, _, _ = render(
        tmp_path, [], [review_row(pdf, ["lines sum 1.00 != net 2.00"], evidence)]
    )

    (href,) = pdf_hrefs(page)
    assert href.endswith("#page=2")


def test_each_reason_in_a_row_gets_its_own_page(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")

    page, _, _ = render(
        tmp_path,
        [],
        [
            review_row(
                pdf, ["lines sum 1.00 != net 2.00", "missing supplier VAT id"], EVIDENCE
            )
        ],
    )

    assert [h.rsplit("#", 1)[1] for h in pdf_hrefs(page)] == ["page=3", "page=1"]


def test_page_link_resolves_to_the_real_pdf_next_to_the_xlsx(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")
    ready_pdf = make_pdf(tmp_path / "in", "ok.pdf")

    page, _, out = render(
        tmp_path,
        [ready_row(ready_pdf)],
        [review_row(pdf, ["missing supplier VAT id"], EVIDENCE)],
    )

    hrefs = pdf_hrefs(page)
    assert len(hrefs) == 2
    targets = {(out.parent / h.split("#")[0]).resolve() for h in hrefs}
    assert targets == {pdf.resolve(), ready_pdf.resolve()}
    assert all(not h.startswith("/") for h in hrefs)


def test_page_link_resolves_when_out_is_in_a_different_folder_than_the_xlsx(
    tmp_path: Path,
) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")
    ready_pdf = make_pdf(tmp_path / "in", "ok.pdf")
    out = tmp_path / "elsewhere" / "deep" / "site" / "page.html"

    page, xlsx, out = render(
        tmp_path,
        [ready_row(ready_pdf)],
        [review_row(pdf, ["missing supplier VAT id"], EVIDENCE)],
        out=out,
        xlsx=tmp_path / "x" / "y" / "invoices.xlsx",
    )

    assert out.parent != xlsx.parent
    hrefs = pdf_hrefs(page)
    assert len(hrefs) == 2
    for href in hrefs:
        assert (out.parent / href.split("#")[0]).resolve() in {
            pdf.resolve(),
            ready_pdf.resolve(),
        }
    assert hrefs[0].endswith("#page=1")


def test_out_folder_is_created_when_missing(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")
    out = tmp_path / "does" / "not" / "exist" / "page.html"

    _, _, written = render(tmp_path, [ready_row(pdf)], [], out=out)

    assert written.is_file()


def test_pdf_name_with_hash_and_space_still_links_to_the_real_file(
    tmp_path: Path,
) -> None:
    pdf = make_pdf(tmp_path / "in", "inv #1 final.pdf")

    page, _, out = render(tmp_path, [], [review_row(pdf, ["not a readable PDF"])])

    (href,) = pdf_hrefs(page)
    assert (out.parent / unquote(urlsplit(href).path)).resolve() == pdf.resolve()
    assert urlsplit(href).fragment == "page=1"


def test_hostile_supplier_and_reason_are_html_escaped(tmp_path: Path) -> None:
    in_dir = tmp_path / "in"
    hostile = "<script>alert(1)</script>\"'><img src=x onerror=alert(2)>"
    bad_pdf = make_pdf(in_dir, "x'onmouseover='alert(3)\".pdf")
    ok_pdf = make_pdf(in_dir, "ok'><b>.pdf")
    review = [
        review_row(
            bad_pdf,
            [f"weird reason {hostile}", f"invalid VAT id format: {hostile}"],
            f'invoice_number p1: "{hostile}"',
            supplier_name=hostile,
            invoice_number=hostile,
            invoice_date=hostile,
            due_date=hostile,
        )
    ]
    ready = [
        ready_row(
            ok_pdf,
            supplier_name=hostile,
            invoice_number=hostile,
            invoice_date=hostile,
            due_date=hostile,
            confidence="unverified",
        )
    ]

    page, xlsx, _ = render(tmp_path, ready, review)

    assert "<script" not in page
    assert "<img" not in page
    assert "<b>.pdf" not in page
    assert "onerror=alert(2)>" not in page.replace("&gt;", "")
    assert html.escape(hostile) in page
    assert str(xlsx) in html.unescape(page)
    for href in re.findall(r"<a href='([^']*)'", page):
        assert "'" not in href
        assert "<" not in href
    assert "<a href='" in page
    assert page.count("<a ") == len(pdf_hrefs(page))


def test_hostile_confidence_value_cannot_break_out_of_the_pill_class(
    tmp_path: Path,
) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")
    row = ready_row(pdf)
    write_xlsx([row], [], tmp_path / "out" / "invoices.xlsx")
    # confidence is a closed Literal in ResultRow, so tamper with the xlsx directly
    book = load_workbook(tmp_path / "out" / "invoices.xlsx")
    sheet = book["Ready"]
    column = [c.value for c in sheet[1]].index("confidence") + 1
    sheet.cell(row=2, column=column).value = "x' onmouseover='alert(1)"
    book.save(tmp_path / "out" / "invoices.xlsx")
    out = tmp_path / "out" / "results.html"

    subprocess.run(
        [
            sys.executable,
            str(VIEWER),
            str(tmp_path / "out" / "invoices.xlsx"),
            "--out",
            str(out),
        ],
        check=True,
        capture_output=True,
    )

    page = out.read_text(encoding="utf-8")
    assert "onmouseover='alert(1)" not in page
    assert "&#x27;" in page


def test_no_javascript_and_no_external_resources(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")

    page, _, _ = render(
        tmp_path, [ready_row(pdf)], [review_row(pdf, ["missing supplier VAT id"])]
    )

    assert "<script" not in page
    assert "http://" not in page and "https://" not in page


def test_without_key_there_is_no_score_line_and_no_miss_marker(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")

    page, _, _ = render(tmp_path, [ready_row(pdf)], [])

    assert "answer key" not in page
    assert "differs from" not in page


def test_key_adds_score_line_from_evaluate_and_marks_only_the_wrong_row(
    tmp_path: Path,
) -> None:
    in_dir = tmp_path / "in"
    good = ready_row(make_pdf(in_dir, "good.pdf"), supplier_name="GOODCO")
    wrong = ready_row(
        make_pdf(in_dir, "wrong.pdf"), supplier_name="WRONGCO", gross_total=999.0
    )
    flagged = review_row(
        make_pdf(in_dir, "flagged.pdf"),
        ["missing supplier VAT id"],
        supplier_vat_id=None,
        supplier_name="FLAGCO",
    )
    key = {
        "good.pdf": key_entry(supplier_name="GOODCO"),
        "wrong.pdf": key_entry(supplier_name="WRONGCO", gross_total=302.5),
        "flagged.pdf": key_entry(
            supplier_name="FLAGCO",
            supplier_vat_id=None,
            invoice_number="VR-9",
            expected_tab="Needs review",
        ),
    }

    page, xlsx, _ = render(tmp_path, [good, wrong], [flagged], key=key)

    lines = evaluate(xlsx, tmp_path / "answer_key.json")
    assert (
        f"Checked against the hand-made answer key: {html.escape(lines[0])} · {html.escape(lines[1])}"
        in page
    )
    assert "field accuracy: 29/30 = 96.7%" in page
    assert "routing: 3/3 files in the expected tab" in page
    assert page.count("differs from answer key") == 1
    assert "differs from answer key: gross_total</div>" in page
    row_html = page.split("<tr>")
    wrong_cell = next(r for r in row_html if "WRONGCO" in r)
    good_cell = next(r for r in row_html if "GOODCO" in r)
    assert "differs from answer key: gross_total" in wrong_cell
    assert "differs from answer key" not in good_cell


def test_miss_marker_lists_every_wrong_field_on_a_review_card(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in", "r.pdf")
    key = {
        "r.pdf": key_entry(
            invoice_number="OTHER-1", currency="USD", expected_tab="Needs review"
        )
    }

    page, _, _ = render(
        tmp_path, [], [review_row(pdf, ["missing supplier VAT id"])], key=key
    )

    assert "differs from answer key: invoice_number, currency</div>" in page


def test_score_line_counts_a_key_entry_with_no_row_and_ignores_extra_rows(
    tmp_path: Path,
) -> None:
    pdf = make_pdf(tmp_path / "in", "present.pdf")
    key = {
        "present.pdf": key_entry(),
        "absent.pdf": key_entry(),
    }

    page, _, _ = render(tmp_path, [ready_row(pdf)], [], key=key)

    assert "field accuracy: 10/20 = 50.0%" in page
    assert "routing: 1/2 files in the expected tab" in page
    assert "differs from answer key" not in page


def test_row_not_in_key_gets_no_marker(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "in", "extra.pdf")
    other = make_pdf(tmp_path / "in", "other.pdf")

    page, _, _ = render(tmp_path, [ready_row(pdf)], [], key={"other.pdf": key_entry()})

    assert "differs from answer key" not in page
    assert other.exists()


def test_hostile_text_in_the_key_path_or_fields_is_escaped_in_the_score_line(
    tmp_path: Path,
) -> None:
    pdf = make_pdf(tmp_path / "in", "a.pdf")
    row = ready_row(pdf, supplier_name="<b>Name</b>")
    key = {"a.pdf": key_entry(supplier_name="expected")}

    page, _, _ = render(tmp_path, [row], [], key=key)

    assert "<b>Name</b>" not in page
    assert "&lt;b&gt;Name&lt;/b&gt;" in page
    assert "differs from answer key: supplier_name" in page
