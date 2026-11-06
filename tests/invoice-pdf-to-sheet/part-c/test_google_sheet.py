from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError

from invoice_pdf_to_sheet import google_sheet
from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow


def test_as_text_neutralizes_formula_starts() -> None:
    assert (
        google_sheet.as_text('=HYPERLINK("http://evil")')
        == '\'=HYPERLINK("http://evil")'
    )
    assert google_sheet.as_text("+1") == "'+1"
    assert google_sheet.as_text("@x") == "'@x"
    assert google_sheet.as_text(None) == ""
    assert google_sheet.as_text(-5.0) == -5.0
    assert google_sheet.as_text("VR-1") == "'VR-1"
    assert google_sheet.as_text("Verdant Ridge BV") == "Verdant Ridge BV"
    assert google_sheet.as_text("#N/A") == "'#N/A"
    assert google_sheet.as_text("Oct 1, 2026") == "'Oct 1, 2026"
    assert google_sheet.as_text(" false ") == "' false "
    assert google_sheet.as_text("'t Hooft BV") == "''t Hooft BV"
    assert google_sheet.as_text("-5") == "'-5"


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


def test_link_label_keeps_quotes_raw_and_cells_are_neutralized(
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
    assert len(review[1]) == len(COLUMNS) - 1
    assert "HYPERLINK" not in str(body)
    assert body["data"][0]["values"] == [review[0]]
    create_body = sheets.spreadsheets.return_value.create.call_args.kwargs["body"]
    assert len(create_body["sheets"][1]["data"][0]["rowData"][1]["values"]) == 15
    requests = sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"][
        "requests"
    ]
    assert len(requests) == 1
    update = requests[0]["updateCells"]
    assert update["range"]["sheetId"] == 1
    assert update["rows"] == [
        {
            "values": [
                {
                    "userEnteredValue": {"stringValue": 'a"b.pdf'},
                    "textFormatRuns": [
                        {
                            "startIndex": 0,
                            "format": {"link": {"uri": "https://drive.example/x"}},
                        }
                    ],
                }
            ]
        }
    ]


def test_coercible_text_is_kept_as_typed() -> None:
    assert google_sheet.as_text("000123") == "'000123"
    assert google_sheet.as_text("2026-10-01") == "'2026-10-01"
    assert google_sheet.as_text("TRUE") == "'TRUE"


def test_revoked_token_falls_back_to_sign_in(
    paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    paths[1].parent.mkdir()
    paths[1].write_text("{}")
    creds = MagicMock(valid=False, expired=True, refresh_token="r")
    creds.refresh.side_effect = RefreshError
    untyped = MagicMock()
    untyped.from_authorized_user_file.return_value = creds
    flow = MagicMock()
    flow.from_client_secrets_file.return_value.run_local_server.return_value.to_json.return_value = '{"new": 2}'
    monkeypatch.setattr(google_sheet, "UntypedCredentials", untyped)
    monkeypatch.setattr(google_sheet, "InstalledAppFlow", flow)
    google_sheet.load_credentials()
    assert paths[1].read_text() == '{"new": 2}'


def test_symlinked_pdf_is_not_uploaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    link = tmp_path / "x.pdf"
    link.symlink_to(secret)
    drive, sheets = MagicMock(), MagicMock()
    sheets.spreadsheets.return_value.create.return_value.execute.return_value = {
        "spreadsheetId": "S",
        "spreadsheetUrl": "u",
    }
    monkeypatch.setattr(google_sheet, "load_credentials", lambda: object())
    monkeypatch.setattr(
        google_sheet,
        "build",
        lambda name, version, credentials: drive if name == "drive" else sheets,
    )
    row = ResultRow(file="x.pdf", pdf_path=link, reasons=["not a readable PDF"])
    google_sheet.publish([], [row])
    uploads = [
        c
        for c in drive.files.return_value.create.call_args_list
        if "media_body" in c.kwargs
    ]
    assert uploads == []
    body = sheets.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs[
        "body"
    ]
    assert len(body["data"][1]["values"][1]) == len(COLUMNS) - 1
    requests = sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"][
        "requests"
    ]
    assert [r["updateCells"]["range"]["sheetId"] for r in requests] == [1]
    assert requests[0]["updateCells"]["rows"] == [
        {"values": [{"userEnteredValue": {"stringValue": "(not uploaded)"}}]}
    ]
