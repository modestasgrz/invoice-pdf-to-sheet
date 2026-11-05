"""AC-18: eval --xlsx <out> --key <key> prints 'field accuracy: <correct>/<total> = <pct>%' (total = 10 x entries) and
'routing: <correct>/<n> files in the expected tab'. 2 entries with 1 wrong gross_total -> 'field accuracy: 19/20 = 95.0%'."""

import json
from pathlib import Path
from typing import Any

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


def test_ac_18_eval_accuracy(
    tmp_path: Path,
    consistent: Any,
    text_pdf: Any,
    model: Any,
    run_cli: Any,
    read_xlsx: Any,
) -> None:
    a = consistent(invoice_number="VR-2026-0701")
    b = consistent(invoice_number="VR-2026-0702")
    text_pdf(tmp_path / "in" / "a.pdf", a)
    text_pdf(tmp_path / "in" / "b.pdf", b)
    model.replies = {"VR-2026-0701": a, "VR-2026-0702": b}
    out = tmp_path / "invoices.xlsx"
    run_cli(["run", "--input", str(tmp_path / "in"), "--xlsx", str(out)])

    key = {}
    for name, reply in (("a.pdf", a), ("b.pdf", b)):
        entry = {f: reply.get(f) for f in FIELDS if f != "line_count"}
        entry["line_count"] = len(reply["lines"])
        entry.update(
            lines=reply["lines"], scan=False, expected_tab="Ready", planted=None
        )
        key[name] = entry
    key["b.pdf"]["gross_total"] = 999.99
    key_path = tmp_path / "answer_key.json"
    key_path.write_text(json.dumps(key))

    code, stdout, _ = run_cli(["eval", "--xlsx", str(out), "--key", str(key_path)])

    assert code == 0
    lines = stdout.splitlines()
    assert "field accuracy: 19/20 = 95.0%" in lines
    assert "routing: 2/2 files in the expected tab" in lines
