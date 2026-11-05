from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from invoice_pdf_to_sheet import google_sheet
from invoice_pdf_to_sheet.schema import ResultRow


def test_as_text_neutralizes_formula_starts() -> None:
    assert (
        google_sheet.as_text('=HYPERLINK("http://evil")')
        == '\'=HYPERLINK("http://evil")'
    )
    assert google_sheet.as_text("+1") == "'+1"
    assert google_sheet.as_text("@x") == "'@x"
    assert google_sheet.as_text(None) == ""
    assert google_sheet.as_text(-5.0) == -5.0
    assert google_sheet.as_text("VR-1") == "VR-1"


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    client = tmp_path / "client.json"
    client.write_text("{}")
    token = tmp_path / "t" / "token.json"
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_FILE", str(client))
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(token))
    return client, token


def test_new_token_is_owner_only(
    paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = MagicMock()
    flow.from_client_secrets_file.return_value.run_local_server.return_value.to_json.return_value = "{}"
    monkeypatch.setattr(google_sheet, "InstalledAppFlow", flow)
    google_sheet.load_credentials()
    assert paths[1].stat().st_mode & 0o777 == 0o600


def test_valid_token_skips_the_browser_flow(
    paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    paths[1].parent.mkdir()
    paths[1].write_text("{}")
    creds = MagicMock(valid=True)
    untyped = MagicMock()
    untyped.from_authorized_user_file.return_value = creds
    flow = MagicMock()
    monkeypatch.setattr(google_sheet, "UntypedCredentials", untyped)
    monkeypatch.setattr(google_sheet, "InstalledAppFlow", flow)
    assert google_sheet.load_credentials() is creds
    flow.from_client_secrets_file.assert_not_called()


def test_expired_token_is_refreshed_and_saved(
    paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    paths[1].parent.mkdir()
    paths[1].write_text("{}")
    creds = MagicMock(valid=False, expired=True, refresh_token="r")
    creds.to_json.return_value = '{"new": 1}'
    untyped = MagicMock()
    untyped.from_authorized_user_file.return_value = creds
    monkeypatch.setattr(google_sheet, "UntypedCredentials", untyped)
    assert google_sheet.load_credentials() is creds
    creds.refresh.assert_called_once()
    assert paths[1].read_text() == '{"new": 1}'


def test_hyperlink_escapes_quotes_and_cells_are_neutralized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / 'a"b.pdf'
    pdf.write_bytes(b"%PDF-1.4\n")
    drive, sheets = MagicMock(), MagicMock()
    drive.files.return_value.create.return_value.execute.return_value = {
        "id": "F",
        "webViewLink": "https://drive.example/x",
    }
    sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
        "spreadsheetId": "S",
        "spreadsheetUrl": "https://sheet",
    }
    monkeypatch.setattr(google_sheet, "load_credentials", lambda: object())
    monkeypatch.setattr(google_sheet, "MediaFileUpload", MagicMock())
    monkeypatch.setattr(
        google_sheet,
        "build",
        lambda name, version, credentials: drive if name == "drive" else sheets,
    )
    row = ResultRow(
        file=pdf.name,
        pdf_path=pdf,
        supplier_name="=cmd()",
        reasons=["not a readable PDF"],
    )

    assert google_sheet.publish([], [row]) == "https://sheet"

    body: dict[str, Any] = (
        sheets.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs[
            "body"
        ]
    )
    review = body["data"][1]["values"]
    assert review[1][1] == "'=cmd()"
    assert review[1][-1] == '=HYPERLINK("https://drive.example/x", "a""b.pdf")'
    assert body["data"][0]["values"] == [review[0]]
