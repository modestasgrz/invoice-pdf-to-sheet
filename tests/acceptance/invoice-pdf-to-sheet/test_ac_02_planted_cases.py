"""AC-02: exactly 1 image-only PDF (key "scan": true), 1 duplicate pair (same VAT id + invoice number), 1 sum mismatch,
1 missing VAT id, 1 IBAN that differs from the supplier's first entry (and is printed in its PDF); expected_tab is Ready for
26 entries and Needs review for 4 (second duplicate, sum mismatch, missing VAT, changed IBAN)."""

import json
import re
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

    def norm(raw: str) -> str:
        return re.sub(r"[\s.\-]", "", raw).upper()

    first_iban: dict[str, str] = {}
    changed = []
    for name in sorted(key):
        entry = key[name]
        iban = norm(entry["supplier_iban"])
        if first_iban.setdefault(entry["supplier_name"], iban) != iban:
            changed.append(name)
    assert len(changed) == 1
    with pdfplumber.open(REPO / "data" / "invoices" / changed[0]) as pdf:
        printed = norm("".join(page.extract_text() or "" for page in pdf.pages))
    assert norm(key[changed[0]]["supplier_iban"]) in printed

    review = sorted(n for n, e in key.items() if e["expected_tab"] == "Needs review")
    ready = [n for n, e in key.items() if e["expected_tab"] == "Ready"]
    assert len(ready) == 26
    assert review == sorted([dup_files[1], mismatched[0], missing_vat[0], changed[0]])
