"""AC-03: Given mock/make_invoices.py, when `uv run python mock/make_invoices.py <tmp_dir>` runs, then <tmp_dir> holds
30 PDFs with the same file names as data/invoices/, and <tmp_dir>/answer_key.json equals data/answer_key.json."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_ac_03_generator_reproducible(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, str(REPO / "mock" / "make_invoices.py"), str(tmp_path)],
        check=True,
        cwd=REPO,
        timeout=300,
    )
    generated = sorted(p.name for p in tmp_path.glob("*.pdf"))
    committed = sorted(p.name for p in (REPO / "data" / "invoices").glob("*.pdf"))
    assert len(generated) == 30
    assert generated == committed
    assert json.loads((tmp_path / "answer_key.json").read_text()) == json.loads(
        (REPO / "data" / "answer_key.json").read_text()
    )
