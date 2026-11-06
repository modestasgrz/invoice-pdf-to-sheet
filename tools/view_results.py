"""Turn one run's .xlsx into one local HTML page a busy owner can scan in 20 seconds: what is ready to book, what needs
them and why in plain words (with a link to the PDF page), and, with --key, the score against the answer key."""

import argparse
import html
import json
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pdfplumber
from loguru import logger
from openpyxl import load_workbook
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException

from invoice_pdf_to_sheet.evaluate import FIELDS, evaluate, same
from invoice_pdf_to_sheet.extract import MAX_BYTES, MAX_PAGES

PLAIN: list[tuple[str, str, str]] = [
    (
        r"duplicate of (.+)",
        "Already received as {0}: same supplier and invoice number.",
        "Don't book or pay it twice. Bin this copy.",
    ),
    (
        r"lines sum (\S+) != net (\S+)",
        "The invoice lines add up to {0}, but the invoice says the net total is {1}.",
        "Ask the supplier for a corrected invoice before paying.",
    ),
    (
        r"net \+ VAT (\S+) != gross (\S+)",
        "Net + VAT is {0}, but the invoice total says {1}.",
        "Ask the supplier which total is right.",
    ),
    (
        r"missing supplier VAT id",
        "The supplier's VAT number is not on the invoice.",
        "Ask the supplier for an invoice with their VAT number, or the VAT can't be reclaimed.",
    ),
    (
        r"invalid VAT id format: (.+)",
        "The supplier's VAT number '{0}' doesn't look like a real EU VAT number.",
        "Check it against the supplier's record.",
    ),
    (
        r"supplier VAT id is buyer's own: (.+)",
        "The VAT number on it ({0}) is your own, not the supplier's.",
        "Check who issued it; ask the supplier to fix it.",
    ),
    (
        r"invalid invoice date: (.+)",
        "The invoice date '{0}' isn't a real date.",
        "Check the date on the PDF.",
    ),
    (
        r"invoice date in the future: (.+)",
        "The invoice is dated {0}, which is in the future.",
        "Check the date on the PDF.",
    ),
    (
        r"invalid due date: (.+)",
        "The due date '{0}' isn't a real date.",
        "Check the due date on the PDF.",
    ),
    (
        r"due date before invoice date",
        "The due date is before the invoice date.",
        "Check both dates on the PDF.",
    ),
    (
        r"evidence not found: (.+)",
        "The reader couldn't point to where the {0} is printed.",
        "Check that value on the PDF before booking.",
    ),
    (
        r"evidence does not match: (.+)",
        "The {0} that was read doesn't match the text it quoted.",
        "Check that value on the PDF before booking.",
    ),
    (
        r"extraction failed: (.+)",
        "The invoice couldn't be read automatically ({0}).",
        "Type this one in by hand.",
    ),
    (
        r"file too large: (.+)",
        "The file is too large to read ({0}).",
        "Type this one in by hand.",
    ),
    (
        r"too many pages: (.+)",
        "The file has too many pages ({0}).",
        "Type this one in by hand.",
    ),
    (
        r"not a readable PDF",
        "The file is not a readable PDF.",
        "Ask the supplier to send it again.",
    ),
]
PAGE_FIELD = {
    "lines sum": "gross_total",
    "net + VAT": "gross_total",
    "missing supplier VAT id": "invoice_number",
    "invalid VAT id": "supplier_vat_id",
    "supplier VAT id is buyer's own": "supplier_vat_id",
    "duplicate of": "invoice_number",
    "invalid invoice date": "invoice_date",
    "invoice date in the future": "invoice_date",
    "invalid due date": "invoice_date",
    "due date before": "invoice_date",
}
# every way a PDF that changed since the run can fail to open; a bare `except Exception` is off by lint (BLE001)
UNREADABLE = (
    PdfminerException,
    MalformedPDFException,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
    RecursionError,
)
SUSPICIOUS = re.compile(
    r"ignore (?:all |any )?(?:previous|prior|above|earlier) instructions|disregard (?:the|all|any|previous)\b|system prompt|you are now\b|mark (?:this|the) invoice as (?:approved|paid)",
    re.IGNORECASE,
)

CSS = """:root{--ink:#16181d;--mute:#6b7280;--line:#e5e7eb;--bg:#f7f7f5;--ok:#15803d;--rev:#b45309;--err:#b91c1c}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Inter","Segoe UI",sans-serif;color:var(--ink);background:var(--bg)}
main{max-width:1100px;margin:0 auto;padding:28px 24px 48px}h1{margin:0;font-size:22px}h2{font-size:16px;margin:28px 0 10px}
.sub{color:var(--mute);margin-top:4px}.tiles{display:flex;flex-wrap:wrap;gap:12px;margin-top:18px}
.tile{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 16px;min-width:170px}
.tile b{display:block;font-size:26px}.tile span{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.tile.ok b{color:var(--ok)}.tile.rev{border-color:var(--rev)}.tile.rev b{color:var(--rev)}
.card{background:#fff;border:1px solid var(--line);border-left:4px solid var(--rev);border-radius:10px;padding:14px 16px;margin-bottom:10px}
.card .top{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.card .who{font-weight:600}.card .amt{font-weight:600;white-space:nowrap}
.card ul{margin:8px 0 6px;padding-left:18px}.card li{margin:3px 0}.todo{color:var(--rev);font-weight:600}
.meta{color:var(--mute);font-size:12px}a{color:#1d4ed8}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}
th{text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);padding:9px 12px;border-bottom:1px solid var(--line);background:#fafafa}
td{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}td.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.pill{display:inline-block;border-radius:999px;padding:1px 8px;font-size:12px;font-weight:600;border:1px solid var(--line);color:var(--mute)}
.pill.verified{color:var(--ok);border-color:#bbf7d0}.pill.vision{color:#6d28d9;border-color:#ddd6fe}
.card.glance{border-left-color:#6d28d9}.card.glance .todo{color:#6d28d9}.flag{color:#6d28d9;font-size:12px;font-weight:600}
.miss{color:var(--err);font-size:12px;font-weight:600}.score{margin-top:28px;color:var(--mute);font-size:13px}"""


def money(rows: list[dict[str, Any]]) -> str:
    totals: dict[str, float] = {}
    for row in rows:
        if isinstance(row["gross_total"], int | float):
            currency = str(row["currency"] or "?")
            totals[currency] = totals.get(currency, 0) + row["gross_total"]
    return " + ".join(f"{v:,.2f} {c}" for c, v in totals.items()) or "—"


def plain(reason: str) -> tuple[str, str]:
    for pattern, said, todo in PLAIN:
        if match := re.fullmatch(pattern, reason):
            return said.format(*match.groups()), todo
    return reason, "Check it on the PDF."


def page_of(reason: str, evidence: str) -> int:
    named = re.fullmatch(r"evidence (?:not found|does not match): (\w+)", reason)
    field = (
        named.group(1)
        if named
        else next((f for k, f in PAGE_FIELD.items() if reason.startswith(k)), "")
    )
    pages: dict[str, str] = {}
    for name, page in re.findall(r'(?:^|; )(\w+) p(\d+): "', evidence):
        pages.setdefault(name, page)
    return int(pages.get(field) or next(iter(pages.values()), 1))


def render(xlsx: Path, key_path: Path | None, out: Path) -> None:
    tabs: dict[str, list[dict[str, Any]]] = {}
    for sheet in load_workbook(xlsx).worksheets:
        header = [str(c.value) for c in sheet[1]]
        tabs[sheet.title] = []
        for cells in sheet.iter_rows(min_row=2):
            row = dict(zip(header, [c.value for c in cells], strict=True))
            link = cells[-1].hyperlink
            row["href"] = link.target if link and link.target else ""
            tabs[sheet.title].append(row)
    ready, review = tabs["Ready"], tabs["Needs review"]
    key: dict[str, dict[str, Any]] = (
        json.loads(key_path.read_text()) if key_path else {}
    )
    base = out.parent.resolve()
    folder = xlsx.parent.resolve()

    def pdf(row: dict[str, Any]) -> Path | None:
        return (folder / str(row["href"])).resolve() if row["href"] else None

    def href(row: dict[str, Any], page: int) -> str:
        path = pdf(row)
        if not path:
            return ""
        return html.escape(
            f"{quote(str(path.relative_to(base, walk_up=True)))}#page={page}"
        )

    def suspicious(row: dict[str, Any]) -> str:
        """Re-read only PDFs the run read as text, under the run's own limits (the file may have changed since)."""
        path = pdf(row)
        if row["method"] != "text" or not path:
            return ""
        try:
            if path.stat().st_size > MAX_BYTES:
                return ""
            with pdfplumber.open(path) as document:
                if len(document.pages) > MAX_PAGES:
                    return ""
                text = " ".join(p.extract_text() or "" for p in document.pages)
        except UNREADABLE as error:
            logger.warning(
                "{}: text scan skipped ({})", row["file"], type(error).__name__
            )
            return ""
        match = SUSPICIOUS.search(" ".join(text.split()))
        return match.group(0) if match else ""

    flagged = {str(r["file"]): suspicious(r) for r in ready + review}

    def misses(row: dict[str, Any]) -> str:
        expected = key.get(str(row["file"]))
        if not expected:
            return ""
        wrong = [f for f in FIELDS if not same(f, row.get(f), expected.get(f))]
        return (
            f'<div class="miss">differs from answer key: {html.escape(", ".join(wrong))}</div>'
            if wrong
            else ""
        )

    e = html.escape
    total = len(ready) + len(review)
    parts = [
        f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><title>Invoices: {len(ready)} ready, {len(review)} need you</title>",
        f"<meta name='viewport' content='width=device-width, initial-scale=1'><style>{CSS}</style></head><body><main>",
        "<h1>Supplier invoices</h1>",
        f"<div class='sub'>{total} PDFs read from <code>{e(str(xlsx))}</code> · {date.today().isoformat()}</div>",
        "<div class='tiles'>",
        f"<div class='tile ok'><b>{len(ready)}</b><span>ready to book · {e(money(ready))}</span></div>",
        f"<div class='tile rev'><b>{len(review)}</b><span>need you · {e(money(review))}</span></div>",
        "</div>",
        f"<h2>Needs you ({len(review)})</h2>",
    ]
    if not review:
        parts.append("<p class='meta'>Nothing to check. Every invoice passed.</p>")
    for row in review:
        reasons = str(row["reasons"] or "").split("; ")
        items = []
        for reason in reasons:
            said, todo = plain(reason)
            page = page_of(reason, str(row["evidence"] or ""))
            link = href(row, page)
            opener = f" <a href='{link}'>Open PDF, page {page}</a>" if link else ""
            items.append(
                f"<li>{e(said)} <span class='todo'>{e(todo)}</span>{opener}</li>"
            )
        if phrase := flagged[str(row["file"])]:
            items.append(
                f"<li>This PDF also contains text that tries to give orders to software: \u201c{e(phrase)}\u201d. <span class='todo'>Make sure it's a real invoice from this supplier.</span></li>"
            )
        parts.append(
            "<div class='card'><div class='top'>"
            f"<div><span class='who'>{e(str(row['supplier_name'] or 'Unknown supplier'))}</span> · {e(str(row['invoice_number'] or 'no number read'))}</div>"
            f"<div class='amt'>{e(money([row]))}</div></div>"
            f"<ul>{''.join(items)}</ul>"
            f"<div class='meta'>{e(str(row['file']))} · invoice date {e(str(row['invoice_date'] or '—'))} · due {e(str(row['due_date'] or '—'))} · technical reason: {e(str(row['reasons']))}</div>"
            f"{misses(row)}</div>"
        )
    glance = [
        r for r in ready if flagged[str(r["file"])] or r["confidence"] == "vision"
    ]
    if glance:
        parts.append(f"<h2>Worth a glance ({len(glance)})</h2>")
    for row in glance:
        notes = []
        if phrase := flagged[str(row["file"])]:
            notes.append(
                f"This PDF contains text that tries to give orders to software: \u201c{e(phrase)}\u201d. The code checks still passed, but text like this can try to change what gets read. <span class='todo'>Compare the total with the PDF and make sure it's a real invoice from this supplier.</span>"
            )
        if row["confidence"] == "vision":
            notes.append(
                "This is a scan, read from the image, so the printed text couldn't be double-checked. <span class='todo'>Compare the total with the PDF before paying.</span>"
            )
        link = href(row, 1)
        opener = f" <a href='{link}'>Open PDF</a>" if link else ""
        parts.append(
            "<div class='card glance'><div class='top'>"
            f"<div><span class='who'>{e(str(row['supplier_name']))}</span> · {e(str(row['invoice_number']))}</div>"
            f"<div class='amt'>{e(money([row]))}</div></div>"
            f"<ul>{''.join(f'<li>{n}{opener}</li>' for n in notes)}</ul>"
            f"<div class='meta'>{e(str(row['file']))} · still counted as ready to book</div></div>"
        )
    parts += [
        f"<h2>Ready to book ({len(ready)})</h2>",
        "<table><thead><tr><th>Supplier</th><th>Invoice</th><th>Date</th><th>Due</th><th>Total</th><th>Read by</th><th>PDF</th></tr></thead><tbody>",
    ]
    for row in sorted(ready, key=lambda r: str(r["due_date"] or "9999")):
        link = href(row, 1)
        parts.append(
            f"<tr><td>{e(str(row['supplier_name']))}{'<div class=flag>see Worth a glance</div>' if row in glance else ''}{misses(row)}</td><td>{e(str(row['invoice_number']))}</td>"
            f"<td>{e(str(row['invoice_date']))}</td><td>{e(str(row['due_date'] or 'none printed'))}</td>"
            f"<td class='num'>{e(money([row]))}</td>"
            f"<td><span class='pill {e(str(row['confidence']))}'>{'quotes checked' if row['confidence'] == 'verified' else 'scan, read from image' if row['confidence'] == 'vision' else e(str(row['confidence']))}</span></td>"
            f"<td>{f'<a href={chr(39)}{link}{chr(39)}>open</a>' if link else ''}</td></tr>"
        )
    parts.append("</tbody></table>")
    if key_path:
        lines = evaluate(xlsx, key_path)
        parts.append(
            f"<div class='score'>Checked against the hand-made answer key: {e(lines[0])} · {e(lines[1])}</div>"
        )
    parts.append("</main></body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    render(args.xlsx, args.key, args.out)
    print(f"results page: {args.out}")
