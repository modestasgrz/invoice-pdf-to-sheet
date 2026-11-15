import os
import re
from collections import Counter
from datetime import date

from invoice_pdf_to_sheet.schema import InvoiceFields, ResultRow

VAT_FORMATS = re.compile(
    r"LT(\d{9}|\d{12})|LV\d{11}|EE\d{9}|DE\d{9}|NL\d{9}B\d{2}|IE\d{7}[A-W][A-I]?|PL\d{10}|FI\d{8}"
)
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
EVIDENCE_REQUIRED = ("invoice_number", "invoice_date", "gross_total")
TOLERANCE = 0.01 + 1e-9


def normalize_vat(raw: str) -> str:
    return re.sub(r"[\s.\-]", "", raw).upper()


def normalize_iban(raw: str) -> str:
    return normalize_vat(raw).removeprefix("IBAN").lstrip(":")


def collapse(text: str) -> str:
    return " ".join(text.split())


def check_fields(fields: InvoiceFields) -> list[str]:
    """Arithmetic, VAT id and date checks on the model's output. Evidence and duplicates are checked elsewhere."""
    reasons: list[str] = []
    lines_sum = sum(line.line_total for line in fields.lines)
    if not abs(lines_sum - fields.net_total) <= TOLERANCE:
        reasons.append(f"lines sum {lines_sum:.2f} != net {fields.net_total:.2f}")
    net_vat = fields.net_total + fields.vat_total
    if not abs(net_vat - fields.gross_total) <= TOLERANCE:
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
        if not ISO_DATE.fullmatch(fields.invoice_date):
            raise ValueError
        issued = date.fromisoformat(fields.invoice_date)
    except ValueError:
        reasons.append(f"invalid invoice date: {fields.invoice_date}")
    if issued and issued > date.today():
        reasons.append(f"invoice date in the future: {issued.isoformat()}")
    if fields.due_date:
        try:
            if not ISO_DATE.fullmatch(fields.due_date):
                raise ValueError
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
        if not collapse(quote.quote)
        or not 1 <= quote.page <= len(pages)
        or collapse(quote.quote) not in collapse(pages[quote.page - 1])
    ]
    quoted = {quote.field for quote in fields.evidence}
    missing += [field for field in EVIDENCE_REQUIRED if field not in quoted]
    if fields.supplier_iban and normalize_iban(
        fields.supplier_iban
    ) not in normalize_vat("".join(pages)):
        missing.append("supplier_iban")
    mismatched = [
        quote.field
        for quote in fields.evidence
        if quote.field == "invoice_number"
        and re.sub(r"\s", "", fields.invoice_number).upper()
        not in re.sub(r"\s", "", quote.quote).upper()
        or quote.field == "gross_total"
        and not any(
            abs(amount - fields.gross_total) <= 0.005 for amount in amounts(quote.quote)
        )
    ]
    return [f"evidence not found: {field}" for field in dict.fromkeys(missing)] + [
        f"evidence does not match: {field}" for field in dict.fromkeys(mismatched)
    ]


def amounts(text: str) -> list[float]:
    """Numbers in printed form: '.' or ',' before exactly 2 final digits is the decimal mark, other '.', ',' and spaces group thousands."""
    found = []
    for token in re.findall(r"\d[\d., ]*\d|\d", text):
        whole, _, cents = (
            token.rpartition(token[-3])
            if len(token) > 3 and token[-3] in ".,"
            else (token, "", "")
        )
        found.append(
            float(re.sub(r"[., ]", "", whole) + ("." + cents if cents else ""))
        )
    return found


def supplier_key(row: ResultRow) -> str:
    return (
        normalize_vat(row.supplier_vat_id or "") or (row.supplier_name or "").casefold()
    )


def mark_duplicates(rows: list[ResultRow]) -> None:
    """Rows must be in sorted file order; a later row with the same supplier + invoice number is a duplicate."""
    first: dict[tuple[str, str], str] = {}
    for row in rows:
        if row.method == "none" or row.invoice_number is None:
            continue
        key = (supplier_key(row), re.sub(r"\s", "", row.invoice_number).upper())
        if key in first:
            row.reasons.append(f"duplicate of {first[key]}")
        else:
            first[key] = row.file


def mark_iban_changes(rows: list[ResultRow]) -> None:
    """Rows must be in sorted file order. Per supplier, the IBAN on strictly the most rows is the usual one and every row
    asking to be paid elsewhere is flagged; with no clear usual IBAN every row is. IBANs not printed in the PDF text are
    already in review and left out of the vote."""
    by_supplier: dict[str, list[tuple[str, ResultRow]]] = {}
    for row in rows:
        iban = normalize_iban(row.supplier_iban or "")
        if iban and "evidence not found: supplier_iban" not in row.reasons:
            by_supplier.setdefault(supplier_key(row), []).append((iban, row))
    for group in by_supplier.values():
        counts = Counter(iban for iban, _ in group).most_common()
        usual = counts[0][0] if len(counts) == 1 or counts[0][1] > counts[1][1] else ""
        for iban, row in group:
            if iban != usual:
                other = usual or next(i for i, _ in counts if i != iban)
                file = next(r.file for i, r in group if i == other)
                row.reasons.append(f"IBAN changed: {iban} != {other} on {file}")
