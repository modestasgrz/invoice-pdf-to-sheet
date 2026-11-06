from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from invoice_pdf_to_sheet import cli, google_sheet
from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow
from invoice_pdf_to_sheet.xlsx import read_xlsx, write_xlsx


@pytest.mark.parametrize(
    "name", ["../secret.pdf", "/etc/passwd", "sub/a.pdf", "..", ".", "a\\..\\b.pdf"]
)
def test_file_name_that_escapes_the_folder_is_never_uploaded(
    tmp_path: Path, name: str
) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-1.4 secret")
    out = tmp_path / "out.xlsx"
    write_xlsx([ResultRow(file=name, pdf_path=folder / "x.pdf")], [], out)

    [row], _ = read_xlsx(out, folder)

    assert row.pdf_path == folder
    assert not row.pdf_path.is_file()
    assert row.file == name


def test_symlink_inside_the_folder_pointing_out_is_not_uploaded(tmp_path: Path) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-1.4 secret")
    (folder / "a.pdf").symlink_to(tmp_path / "secret.pdf")
    out = tmp_path / "out.xlsx"
    write_xlsx([ResultRow(file="a.pdf", pdf_path=folder / "a.pdf")], [], out)

    [row], _ = read_xlsx(out, folder)

    assert row.pdf_path == folder


def test_plain_name_inside_the_folder_keeps_its_pdf(tmp_path: Path) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "a b.pdf").write_bytes(b"%PDF-1.4")
    out = tmp_path / "out.xlsx"
    write_xlsx([ResultRow(file="a b.pdf", pdf_path=folder / "a b.pdf")], [], out)

    [row], _ = read_xlsx(out, folder)

    assert row.pdf_path == folder / "a b.pdf"


def unreadable_inputs(tmp_path: Path) -> list[Path]:
    not_xlsx = tmp_path / "not.xlsx"
    not_xlsx.write_text("hello")
    no_tabs = tmp_path / "tabs.xlsx"
    book = Workbook()
    book.active.title = "Other"
    book.save(no_tabs)
    empty_file_cell = tmp_path / "empty.xlsx"
    book = Workbook()
    book.active.title = "Ready"
    book.active.append(COLUMNS)
    book.active.append([None, "Verdant Ridge BV", *[None] * (len(COLUMNS) - 2)])
    book.create_sheet("Needs review").append(COLUMNS)
    book.save(empty_file_cell)
    return [not_xlsx, no_tabs, empty_file_cell]


def test_unreadable_xlsx_is_a_clean_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[Any] = []

    def fake_publish(*args: Any) -> str:
        calls.append(args)
        return "u"

    monkeypatch.setattr(google_sheet, "publish", fake_publish)

    for path in unreadable_inputs(tmp_path):
        code = cli.main(["publish", "--xlsx", str(path), "--input", str(tmp_path)])
        err = capsys.readouterr().err
        assert code == 2
        assert err.startswith(f"cannot read {path}: ")
        assert "Traceback" not in err
    assert calls == []
