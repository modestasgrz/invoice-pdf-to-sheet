import json
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from invoice_pdf_to_sheet.checks import normalize_vat

FIELDS = [
    "supplier_name",
    "supplier_vat_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "net_total",
    "vat_total",
    "gross_total",
    "line_count",
]
NUMBERS = {"net_total", "vat_total", "gross_total", "line_count"}


def same(field: str, got: Any, expected: Any) -> bool:
    if got in (None, "") or expected is None:
        return got in (None, "") and expected is None
    if field in NUMBERS:
        try:
            return abs(float(got) - float(expected)) <= 0.005
        except ValueError:
            return False
    if field == "supplier_vat_id":
        return normalize_vat(str(got)) == normalize_vat(str(expected))
    return (
        " ".join(str(got).split()).casefold()
        == " ".join(str(expected).split()).casefold()
    )


def evaluate(xlsx: Path, key_path: Path) -> list[str]:
    """Field accuracy and routing against the answer key (DEC-09). Returns the report lines."""
    rows: dict[str, tuple[str, dict[str, Any]]] = {}
    for sheet in load_workbook(xlsx).worksheets:
        values = list(sheet.iter_rows(values_only=True))
        header = [str(h) for h in values[0]]
        for row in values[1:]:
            record = dict(zip(header, row, strict=True))
            rows[str(record["file"])] = (sheet.title, record)

    key: dict[str, dict[str, Any]] = json.loads(key_path.read_text())
    per_field = dict.fromkeys(FIELDS, 0)
    routed = 0
    for file, expected in key.items():
        if file not in rows:
            continue
        tab, record = rows[file]
        routed += tab == expected["expected_tab"]
        for field in FIELDS:
            per_field[field] += same(field, record.get(field), expected.get(field))
    correct, total = sum(per_field.values()), len(FIELDS) * len(key)
    return [
        f"field accuracy: {correct}/{total} = {100 * correct / total:.1f}%",
        f"routing: {routed}/{len(key)} files in the expected tab",
        *(f"  {field}: {n}/{len(key)}" for field, n in per_field.items()),
    ]
