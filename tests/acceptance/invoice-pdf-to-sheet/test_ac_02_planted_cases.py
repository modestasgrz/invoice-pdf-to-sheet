"""AC-02: exactly 1 image-only PDF (key "scan": true), 1 duplicate pair (same VAT id + invoice number), 1 sum mismatch,
1 missing VAT id; expected_tab is Ready for 27 entries and Needs review for 3 (second duplicate, sum mismatch, missing VAT)."""

import json
from collections import Counter
from pathlib import Path

import pdfplumber

REPO = Path(__file__).resolve().parents[3]


def test_ac_02_planted_cases() -> None:
    key = json.loads((REPO / "data" / "answer_key.json").read_text())

    no_text = []
    for name in sorted(key):
        with pdfplumber.open(REPO / "data" / "invoices" / name) as pdf:
            if all(
                len("".join((page.extract_text() or "").split())) < 20
                for page in pdf.pages
            ):
                no_text.append(name)
    assert len(no_text) == 1
    assert key[no_text[0]]["scan"] is True
    assert [n for n, e in key.items() if e["scan"]] == no_text

    pairs = Counter((e["supplier_vat_id"], e["invoice_number"]) for e in key.values())
    assert sorted(pairs.values(), reverse=True)[:2] == [2, 1]
    dup_key = next(k for k, n in pairs.items() if n == 2)
    dup_files = sorted(
        n
        for n, e in key.items()
        if (e["supplier_vat_id"], e["invoice_number"]) == dup_key
    )

    mismatched = [
        n
        for n, e in key.items()
        if abs(sum(li["line_total"] for li in e["lines"]) - e["net_total"]) > 0.01
    ]
    assert len(mismatched) == 1
    missing_vat = [n for n, e in key.items() if e["supplier_vat_id"] is None]
    assert len(missing_vat) == 1

    review = sorted(n for n, e in key.items() if e["expected_tab"] == "Needs review")
    ready = [n for n, e in key.items() if e["expected_tab"] == "Ready"]
    assert len(ready) == 27
    assert review == sorted([dup_files[1], mismatched[0], missing_vat[0]])
