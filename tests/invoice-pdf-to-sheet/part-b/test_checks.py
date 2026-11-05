from pathlib import Path
from typing import Any

import pytest

from invoice_pdf_to_sheet.checks import (
    check_evidence,
    check_fields,
    mark_duplicates,
    normalize_vat,
)
from invoice_pdf_to_sheet.schema import InvoiceFields, ResultRow

BASE: dict[str, Any] = {
    "supplier_name": "Brightfold Verpackung GmbH",
    "supplier_vat_id": "DE999000002",
    "invoice_number": "BF-1",
    "invoice_date": "2026-10-01",
    "due_date": "2026-10-15",
    "currency": "EUR",
    "net_total": 100.0,
    "vat_total": 19.0,
    "gross_total": 119.0,
    "lines": [
        {"description": "Box", "quantity": 1, "unit_price": 100.0, "line_total": 100.0}
    ],
    "evidence": [
        {"field": "invoice_number", "page": 1, "quote": "BF-1"},
        {"field": "invoice_date", "page": 1, "quote": "01.10.2026"},
        {"field": "gross_total", "page": 1, "quote": "119,00"},
    ],
}


def fields(**overrides: Any) -> InvoiceFields:
    return InvoiceFields.model_validate({**BASE, **overrides})


def test_clean_invoice_has_no_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INVOICE_BUYER_VAT_ID", raising=False)
    assert check_fields(fields()) == []


@pytest.mark.parametrize(
    "vat_id",
    [
        "LT999000003",
        "LT999000003123",
        "LV99900000006",
        "EE999000001",
        "NL999000001B01",
        "IE9990004W",
        "IE9990004WA",
        "PL9990000001",
        "FI99900001",
        "de 999.000-002",
    ],
)
def test_vat_formats_accepted(vat_id: str) -> None:
    assert not any("VAT" in r for r in check_fields(fields(supplier_vat_id=vat_id)))


@pytest.mark.parametrize(
    "vat_id", ["DE12345678", "FR99900000001", "NL999000001X01", "IE999000"]
)
def test_vat_formats_rejected(vat_id: str) -> None:
    assert f"invalid VAT id format: {vat_id}" in check_fields(
        fields(supplier_vat_id=vat_id)
    )


def test_empty_vat_id_is_missing() -> None:
    assert "missing supplier VAT id" in check_fields(fields(supplier_vat_id="  "))


def test_invalid_due_date() -> None:
    assert "invalid due date: soon" in check_fields(fields(due_date="soon"))


def test_tolerance_allows_one_cent() -> None:
    assert check_fields(fields(gross_total=119.01)) == []
    assert check_fields(fields(gross_total=119.02)) == [
        "net + VAT 119.00 != gross 119.02"
    ]


def test_reason_order_follows_dec_06() -> None:
    reasons = check_fields(
        fields(
            net_total=90.0, gross_total=200.0, supplier_vat_id=None, invoice_date="bad"
        )
    )
    assert reasons == [
        "lines sum 100.00 != net 90.00",
        "net + VAT 109.00 != gross 200.00",
        "missing supplier VAT id",
        "invalid invoice date: bad",
    ]


def test_evidence_found_with_collapsed_whitespace() -> None:
    page = "Rechnungsnummer:   BF-1\nRechnungsdatum: 01.10.2026\nGesamt 119,00"
    assert check_evidence(fields(), [page]) == []


def test_evidence_wrong_page_and_missing_required() -> None:
    evidence = [{"field": "invoice_number", "page": 3, "quote": "BF-1"}]
    reasons = check_evidence(fields(evidence=evidence), ["BF-1"])
    assert reasons == [
        "evidence not found: invoice_number",
        "evidence not found: invoice_date",
        "evidence not found: gross_total",
    ]


def test_normalize_vat() -> None:
    assert normalize_vat(" nl 999.000-001 b01 ") == "NL999000001B01"


def row(file: str, **kwargs: Any) -> ResultRow:
    return ResultRow(file=file, pdf_path=Path(file), method="text", **kwargs)


def test_duplicates_by_name_when_vat_missing_and_by_spacing_in_number() -> None:
    rows = [
        row("a.pdf", supplier_name="Clovermere Dairy Co-op", invoice_number="CD 1"),
        row("b.pdf", supplier_name="clovermere dairy co-op", invoice_number="cd1"),
        row("c.pdf", supplier_name="Other", invoice_number="CD1"),
    ]
    mark_duplicates(rows)
    assert [r.reasons for r in rows] == [[], ["duplicate of a.pdf"], []]


def test_failed_rows_never_count_as_duplicates() -> None:
    rows = [
        ResultRow(file="a.pdf", pdf_path=Path("a.pdf")),
        row("b.pdf", supplier_vat_id="DE999000002", invoice_number="X"),
        row("c.pdf", supplier_vat_id="DE999000002", invoice_number="X"),
    ]
    mark_duplicates(rows)
    assert rows[1].reasons == []
    assert rows[2].reasons == ["duplicate of b.pdf"]
