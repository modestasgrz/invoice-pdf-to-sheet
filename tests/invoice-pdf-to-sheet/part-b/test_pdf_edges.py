import os
import re
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from reportlab.pdfgen import canvas

from invoice_pdf_to_sheet.cli import main
from invoice_pdf_to_sheet.schema import InvoiceFields

TEN_MB = 10 * 1048576
FIELDS: dict[str, Any] = {
    "supplier_name": "Verdant Ridge Green Coffee Imports BV",
    "supplier_vat_id": "NL999000001B01",
    "invoice_number": "VR-2026-0101",
    "invoice_date": "2026-10-01",
    "due_date": "2026-10-31",
    "currency": "EUR",
    "net_total": 250.0,
    "vat_total": 52.5,
    "gross_total": 302.5,
    "lines": [
        {
            "description": "Green coffee",
            "quantity": 2,
            "unit_price": 100.0,
            "line_total": 200.0,
        },
        {
            "description": "Sample roast fee",
            "quantity": 1,
            "unit_price": 50.0,
            "line_total": 50.0,
        },
    ],
}
QUOTES = (
    ("invoice_number", 1, "VR-2026-0101"),
    ("invoice_date", 1, "2026-10-01"),
    ("gross_total", 1, "302.50"),
)


class Model:
    def __init__(
        self,
        quotes: tuple[tuple[str, int, str], ...] = QUOTES,
        error: BaseException | None = None,
    ) -> None:
        self.quotes = quotes
        self.error = error
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, system: str, content: list[dict[str, Any]]) -> InvoiceFields:
        self.calls.append(content)
        if self.error:
            raise self.error
        evidence = [{"field": f, "page": p, "quote": q} for f, p, q in self.quotes]
        return InvoiceFields.model_validate({**FIELDS, "evidence": evidence})


def install(monkeypatch: pytest.MonkeyPatch, model: Model) -> Model:
    monkeypatch.setattr("invoice_pdf_to_sheet.extract.call_model", model)
    return model


def make_pdf(path: Path, pages: list[list[str]]) -> Path:
    pdf = canvas.Canvas(str(path), invariant=1)
    for lines in pages:
        for i, line in enumerate(lines):
            pdf.drawString(40, 800 - 16 * i, line)
        pdf.showPage()
    pdf.save()
    return path


def run_xlsx(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str] | None = None
) -> tuple[int, str, str, dict[str, list[dict[str, Any]]]]:
    xlsx = tmp_path / "out" / "o.xlsx"
    code = main(
        ["run", "--input", str(tmp_path / "in"), "--xlsx", str(xlsx), *(extra or [])]
    )
    captured = capsys.readouterr()
    tabs: dict[str, list[dict[str, Any]]] = {}
    if xlsx.exists():
        for sheet in load_workbook(xlsx).worksheets:
            rows = list(sheet.iter_rows(values_only=True))
            tabs[sheet.title] = [dict(zip(rows[0], r, strict=True)) for r in rows[1:]]
    return code, captured.out, captured.err, tabs


@pytest.fixture
def indir(tmp_path: Path) -> Path:
    (tmp_path / "in").mkdir()
    return tmp_path / "in"


def test_multipage_text_numbers_pages_in_content_and_evidence(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_pdf(
        indir / "a.pdf",
        [
            ["Verdant Ridge Green Coffee Imports BV", "Invoice no: VR-2026-0101"],
            ["Invoice date: 2026-10-01", "continued"],
            ["Total gross: 302.50"],
        ],
    )
    model = install(
        monkeypatch,
        Model(
            (
                ("invoice_number", 1, "VR-2026-0101"),
                ("invoice_date", 2, "2026-10-01"),
                ("gross_total", 3, "302.50"),
            )
        ),
    )
    code, _, _, tabs = run_xlsx(tmp_path, capsys)
    assert code == 0
    (content,) = model.calls
    assert len(content) == 1
    text = content[0]["text"]
    tag = re.fullmatch(
        r"<(invoice-[0-9a-f]{16})>\n--- page 1 ---\n.*\n</\1>", text, re.DOTALL
    )
    assert tag
    assert text.count(f"<{tag.group(1)}>") == 1
    assert [m.group(1) for m in re.finditer(r"--- page (\d+) ---", text)] == [
        "1",
        "2",
        "3",
    ]
    assert (
        text.index("VR-2026-0101")
        < text.index("--- page 2 ---")
        < text.index("2026-10-01")
    )
    assert text.index("--- page 3 ---") < text.index("302.50")
    (row,) = tabs["Ready"]
    assert row["confidence"] == "verified"
    assert (
        row["evidence"]
        == 'invoice_number p1: "VR-2026-0101"; invoice_date p2: "2026-10-01"; gross_total p3: "302.50"'
    )


@pytest.mark.parametrize("claimed_page", [1, 3, 0, 4])
def test_quote_cited_on_the_wrong_or_missing_page_is_not_found(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    claimed_page: int,
) -> None:
    make_pdf(
        indir / "a.pdf",
        [
            ["Invoice no: VR-2026-0101", "Total gross: 302.50"],
            ["Invoice date: 2026-10-01"],
            ["nothing here at all"],
        ],
    )
    install(
        monkeypatch,
        Model(
            (
                ("invoice_number", 1, "VR-2026-0101"),
                ("invoice_date", claimed_page, "2026-10-01"),
                ("gross_total", 1, "302.50"),
            )
        ),
    )
    _, _, _, tabs = run_xlsx(tmp_path, capsys)
    (row,) = tabs["Needs review"]
    assert row["reasons"] == "evidence not found: invoice_date"
    assert row["confidence"] == "unverified"
    assert row["method"] == "text"


def test_blank_first_page_with_text_later_is_still_a_text_invoice(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_pdf(
        indir / "a.pdf",
        [
            [],
            [
                "Invoice no: VR-2026-0101",
                "Invoice date: 2026-10-01",
                "Total gross: 302.50",
            ],
        ],
    )
    model = install(
        monkeypatch,
        Model(
            (
                ("invoice_number", 2, "VR-2026-0101"),
                ("invoice_date", 2, "2026-10-01"),
                ("gross_total", 2, "302.50"),
            )
        ),
    )
    _, _, _, tabs = run_xlsx(tmp_path, capsys)
    assert [b["type"] for b in model.calls[0]] == ["text"]
    assert "--- page 1 ---\n\n--- page 2 ---\n" in model.calls[0][0]["text"]
    (row,) = tabs["Ready"]
    assert (row["method"], row["confidence"]) == ("text", "verified")


@pytest.mark.parametrize(("characters", "method"), [(19, "vision"), (20, "text")])
def test_text_layer_threshold_is_twenty_non_space_characters(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    characters: int,
    method: str,
) -> None:
    make_pdf(indir / "a.pdf", [["x " * (characters - 1) + "x"]])
    model = install(monkeypatch, Model(()))
    _, _, _, tabs = run_xlsx(tmp_path, capsys)
    kinds = {b["type"] for b in model.calls[0]}
    assert kinds == ({"image", "text"} if method == "vision" else {"text"})
    row = (tabs.get("Ready") or []) + (tabs.get("Needs review") or [])
    assert row[0]["method"] == method


@pytest.mark.parametrize(("pages", "called"), [(5, True), (6, False)])
def test_page_limit_boundary(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pages: int,
    called: bool,
) -> None:
    make_pdf(
        indir / "a.pdf",
        [
            [
                "Invoice no: VR-2026-0101",
                "Invoice date: 2026-10-01",
                "Total gross: 302.50",
            ]
        ]
        + [[f"filler page {n} with plenty of text"] for n in range(2, pages + 1)],
    )
    model = install(monkeypatch, Model())
    code, _, _, tabs = run_xlsx(tmp_path, capsys)
    assert code == 0
    assert bool(model.calls) is called
    if called:
        assert len(tabs["Ready"]) == 1
        assert [
            m.group(1)
            for m in re.finditer(r"--- page (\d+) ---", model.calls[0][0]["text"])
        ] == [str(n) for n in range(1, 6)]
    else:
        assert tabs["Needs review"][0]["reasons"] == "too many pages: 6 > 5"


def padded_pdf(path: Path, size: int) -> Path:
    make_pdf(
        path,
        [
            [
                "Invoice no: VR-2026-0101",
                "Invoice date: 2026-10-01",
                "Total gross: 302.50",
            ]
        ],
    )
    with path.open("ab") as handle:
        handle.write(b"\n" * (size - path.stat().st_size))
    assert path.stat().st_size == size
    return path


def test_file_of_exactly_ten_mb_is_accepted(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    padded_pdf(indir / "a.pdf", TEN_MB)
    model = install(monkeypatch, Model())
    code, _, _, tabs = run_xlsx(tmp_path, capsys)
    assert code == 0
    assert len(model.calls) == 1
    assert [r["file"] for r in tabs["Ready"]] == ["a.pdf"]


def test_file_one_byte_over_ten_mb_is_refused_before_any_model_call(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    padded_pdf(indir / "a.pdf", TEN_MB + 1)
    model = install(monkeypatch, Model())
    _, _, _, tabs = run_xlsx(tmp_path, capsys)
    assert model.calls == []
    assert tabs["Needs review"][0]["reasons"] == "file too large: 10.0 MB > 10 MB"
    assert tabs["Needs review"][0]["method"] == "none"


def test_uppercase_pdf_extension_is_processed(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pages = [
        ["Invoice no: VR-2026-0101", "Invoice date: 2026-10-01", "Total gross: 302.50"]
    ]
    make_pdf(indir / "a.pdf", pages)
    make_pdf(indir / "B.PDF", pages)
    model = install(monkeypatch, Model())
    code, out, _, tabs = run_xlsx(tmp_path, capsys)
    assert code == 0
    assert len(model.calls) == 2
    assert out.startswith("2 invoices:")
    files = [r["file"] for tab in tabs.values() for r in tab]
    assert sorted(files) == ["B.PDF", "a.pdf"]


@pytest.mark.parametrize(
    "payload", [b"", b"%PDF-1.4\n", b"%PDF-1.4\n1 0 obj\n<<", b"\x00" * 64]
)
def test_corrupt_files_become_review_rows_without_stopping_the_batch(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: bytes,
) -> None:
    (indir / "a_bad.pdf").write_bytes(payload)
    make_pdf(
        indir / "b_good.pdf",
        [
            [
                "Invoice no: VR-2026-0101",
                "Invoice date: 2026-10-01",
                "Total gross: 302.50",
            ]
        ],
    )
    model = install(monkeypatch, Model())
    code, out, _, tabs = run_xlsx(tmp_path, capsys)
    assert code == 0
    assert len(model.calls) == 1
    assert tabs["Needs review"][0]["file"] == "a_bad.pdf"
    assert tabs["Needs review"][0]["reasons"] == "not a readable PDF"
    assert [r["file"] for r in tabs["Ready"]] == ["b_good.pdf"]
    assert "2 invoices: 1 ready, 1 needs review" in out


@pytest.mark.parametrize("keep", [0.3, 0.6, 0.9])
def test_truncated_real_pdf_never_crashes_the_batch(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    keep: float,
) -> None:
    good = make_pdf(
        tmp_path / "src.pdf",
        [
            [
                "Invoice no: VR-2026-0101",
                "Invoice date: 2026-10-01",
                "Total gross: 302.50",
            ]
        ],
    )
    data = good.read_bytes()
    (indir / "a_cut.pdf").write_bytes(data[: int(len(data) * keep)])
    install(monkeypatch, Model())
    code, out, _, tabs = run_xlsx(tmp_path, capsys)
    assert code == 0
    assert "1 invoices:" in out
    assert len(tabs["Ready"]) + len(tabs["Needs review"]) == 1


def test_directory_named_like_a_pdf_is_a_review_row(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (indir / "folder.pdf").mkdir()
    make_pdf(
        indir / "z.pdf",
        [
            [
                "Invoice no: VR-2026-0101",
                "Invoice date: 2026-10-01",
                "Total gross: 302.50",
            ]
        ],
    )
    install(monkeypatch, Model())
    code, _, _, tabs = run_xlsx(tmp_path, capsys)
    assert code == 0
    assert tabs["Needs review"][0]["file"] == "folder.pdf"
    assert [r["file"] for r in tabs["Ready"]] == ["z.pdf"]


def test_broken_symlink_named_pdf_is_isolated_not_fatal(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    os.symlink(tmp_path / "does-not-exist.pdf", indir / "a_dangling.pdf")
    make_pdf(
        indir / "b_good.pdf",
        [
            [
                "Invoice no: VR-2026-0101",
                "Invoice date: 2026-10-01",
                "Total gross: 302.50",
            ]
        ],
    )
    install(monkeypatch, Model())
    code, _, _, tabs = run_xlsx(tmp_path, capsys)
    assert code == 0
    assert [r["file"] for r in tabs["Ready"]] == ["b_good.pdf"]
    assert [r["file"] for r in tabs["Needs review"]] == ["a_dangling.pdf"]


@pytest.mark.parametrize("error", [RuntimeError("boom"), KeyError("k"), TypeError("t")])
def test_any_exception_from_the_model_is_retried_once_then_reported(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: BaseException,
) -> None:
    """DEC-05: any exception from call_model is retried once, then becomes 'extraction failed: <type name>'."""
    make_pdf(
        indir / "a.pdf",
        [
            [
                "Invoice no: VR-2026-0101",
                "Invoice date: 2026-10-01",
                "Total gross: 302.50",
            ]
        ],
    )
    model = install(monkeypatch, Model(error=error))
    code, _, _, tabs = run_xlsx(tmp_path, capsys)
    assert code == 0
    assert len(model.calls) == 2
    assert (
        tabs["Needs review"][0]["reasons"]
        == f"extraction failed: {type(error).__name__}"
    )


def test_duplicate_detection_ignores_a_first_file_that_failed_extraction(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (indir / "a.pdf").write_bytes(b"not a pdf")
    make_pdf(
        indir / "b.pdf",
        [
            [
                "Invoice no: VR-2026-0101",
                "Invoice date: 2026-10-01",
                "Total gross: 302.50",
            ]
        ],
    )
    install(monkeypatch, Model())
    _, _, _, tabs = run_xlsx(tmp_path, capsys)
    assert [r["file"] for r in tabs["Ready"]] == ["b.pdf"]


def test_duplicate_found_when_vat_id_is_formatted_differently(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_pdf(
        indir / "a.pdf",
        [
            [
                "Invoice no: VR-2026-0101",
                "Invoice date: 2026-10-01",
                "Total gross: 302.50",
            ]
        ],
    )
    make_pdf(
        indir / "b.pdf",
        [
            [
                "Invoice no: VR-2026-0101",
                "Invoice date: 2026-10-01",
                "Total gross: 302.50",
                "second copy",
            ]
        ],
    )
    replies = iter(["NL999000001B01", "nl 999.000.001-b01"])

    def varying(system: str, content: list[dict[str, Any]]) -> InvoiceFields:
        return InvoiceFields.model_validate(
            {
                **FIELDS,
                "supplier_vat_id": next(replies),
                "invoice_number": "vr-2026- 0101",
                "evidence": [{"field": f, "page": p, "quote": q} for f, p, q in QUOTES],
            }
        )

    monkeypatch.setattr("invoice_pdf_to_sheet.extract.call_model", varying)
    _, _, _, tabs = run_xlsx(tmp_path, capsys, ["--workers", "1"])
    assert [r["file"] for r in tabs["Ready"]] == ["a.pdf"]
    assert tabs["Needs review"][0]["reasons"] == "duplicate of a.pdf"


def test_zero_workers_is_rejected_cleanly(
    tmp_path: Path,
    indir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_pdf(indir / "a.pdf", [["Invoice no: VR-2026-0101"]])
    install(monkeypatch, Model())
    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run",
                "--input",
                str(indir),
                "--xlsx",
                str(tmp_path / "o.xlsx"),
                "--workers",
                "0",
            ]
        )
    assert exit_info.value.code == 2
