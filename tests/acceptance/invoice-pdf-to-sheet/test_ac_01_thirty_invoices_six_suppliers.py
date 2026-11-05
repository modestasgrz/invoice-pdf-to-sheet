"""AC-01: Given the committed data/invoices/, when the files are listed, then there are exactly 30 .pdf files, and
data/answer_key.json has exactly 30 entries keyed by file name, 5 per supplier for each of the 6 supplier names in DEC-11."""

import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SUPPLIERS = {
    "Verdant Ridge Green Coffee Imports BV",
    "Brightfold Verpackung GmbH",
    "Clovermere Dairy Co-op",
    "Tamberlane Espresso Service Ltd",
    "Quillon Energy UAB",
    "Swiftpaw Couriers SIA",
}


def test_ac_01_thirty_invoices_six_suppliers() -> None:
    pdfs = sorted(p.name for p in (REPO / "data" / "invoices").glob("*.pdf"))
    assert len(pdfs) == 30
    key = json.loads((REPO / "data" / "answer_key.json").read_text())
    assert sorted(key) == pdfs
    counts = Counter(entry["supplier_name"] for entry in key.values())
    assert set(counts) == SUPPLIERS
    assert all(n == 5 for n in counts.values())
