import os
import re
from datetime import date

from invoice_pdf_to_sheet.schema import InvoiceFields, ResultRow

VAT_FORMATS = re.compile(
    r"LT(\d{9}|\d{12})|LV\d{11}|EE\d{9}|DE\d{9}|NL\d{9}B\d{2}|IE\d{7}[A-W][A-I]?|PL\d{10}|FI\d{8}"
)
EVIDENCE_REQUIRED = ("invoice_number", "invoice_date", "gross_total")
TOLERANCE = 0.01 + 1e-9


def normalize_vat(raw: str) -> str:
    return re.sub(r"[\s.\-]", "", raw).upper()


def collapse(text: str) -> str:
    return " ".join(text.split())


def check_fields(fields: InvoiceFields) -> list[str]:
    """Arithmetic, VAT id and date checks on the model's output. Evidence and duplicates are checked elsewhere."""
    reasons: list[str] = []
    lines_sum = sum(line.line_total for line in fields.lines)
    if abs(lines_sum - fields.net_total) > TOLERANCE:
        reasons.append(f"lines sum {lines_sum:.2f} != net {fields.net_total:.2f}")
    net_vat = fields.net_total + fields.vat_total
    if abs(net_vat - fields.gross_total) > TOLERANCE:
        reasons.append(f"net + VAT {net_vat:.2f} != gross {fields.gross_total:.2f}")

    vat_id = normalize_vat(fields.supplier_vat_id or "")
    if not vat_id:
        reasons.append("missing supplier VAT id")
    else:
        if not VAT_FORMATS.fullmatch(vat_id):
            reasons.append(f"invalid VAT id format: {fields.supplier_vat_id}")
        if vat_id == normalize_vat(
            os.environ.get("INVOICE_BUYER_VAT_ID", "LT999000099")
        ):
            reasons.append(f"supplier VAT id is buyer's own: {vat_id}")

    issued: date | None = None
    try:
        issued = date.fromisoformat(fields.invoice_date)
    except ValueError:
        reasons.append(f"invalid invoice date: {fields.invoice_date}")
    if issued and issued > date.today():
        reasons.append(f"invoice date in the future: {issued.isoformat()}")
    if fields.due_date:
        try:
            due = date.fromisoformat(fields.due_date)
        except ValueError:
            reasons.append(f"invalid due date: {fields.due_date}")
        else:
            if issued and due < issued:
                reasons.append("due date before invoice date")
    return reasons


def check_evidence(fields: InvoiceFields, pages: list[str]) -> list[str]:
    """Every quote must be in the page text the model saw; the required fields must each have a quote."""
    missing = [
        quote.field
        for quote in fields.evidence
        if not 1 <= quote.page <= len(pages)
        or collapse(quote.quote) not in collapse(pages[quote.page - 1])
    ]
    quoted = {quote.field for quote in fields.evidence}
    missing += [field for field in EVIDENCE_REQUIRED if field not in quoted]
    return [f"evidence not found: {field}" for field in dict.fromkeys(missing)]


def mark_duplicates(rows: list[ResultRow]) -> None:
    """Rows must be in sorted file order; a later row with the same supplier + invoice number is a duplicate."""
    first: dict[tuple[str, str], str] = {}
    for row in rows:
        if row.method == "none" or row.invoice_number is None:
            continue
        supplier = (
            normalize_vat(row.supplier_vat_id or "")
            or (row.supplier_name or "").casefold()
        )
        key = (supplier, re.sub(r"\s", "", row.invoice_number).upper())
        if key in first:
            row.reasons.append(f"duplicate of {first[key]}")
        else:
            first[key] = row.file
