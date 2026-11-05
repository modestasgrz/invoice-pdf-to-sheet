import importlib.util
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[3]


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_invoices", REPO / "mock" / "make_invoices.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_money_formats_english_and_german() -> None:
    gen = load()
    assert gen.money(1234.5, False) == "1,234.50"
    assert gen.money(1234.5, True) == "1.234,50"


def test_build_invoices_plants_each_case_once() -> None:
    invoices = load().build_invoices()
    planted = sorted(inv.planted for inv in invoices if inv.planted)
    assert planted == [
        "duplicate",
        "injection_note",
        "missing_vat",
        "scan",
        "sum_mismatch",
    ]
    assert len({inv.file for inv in invoices}) == 30


def test_totals_add_up_except_planted_mismatch() -> None:
    for inv in load().build_invoices():
        assert round(inv.net + inv.vat, 2) == inv.gross
        lines_match = abs(sum(li.line_total for li in inv.lines) - inv.net) <= 0.01
        assert lines_match is (inv.planted != "sum_mismatch")


def test_duplicate_is_last_and_matches_original() -> None:
    invoices = load().build_invoices()
    resend = invoices[-1]
    assert resend.file.endswith("_resend.pdf")
    originals = [i for i in invoices[:-1] if i.number == resend.number]
    assert len(originals) == 1
    assert originals[0].file < resend.file
