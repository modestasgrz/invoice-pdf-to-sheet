"""AC-21: Given GOOGLE_OAUTH_CLIENT_FILE (existing) and GOOGLE_TOKEN_FILE (no token yet), loading credentials asks the OAuth
flow for exactly ["https://www.googleapis.com/auth/drive.file"] and saves the token to GOOGLE_TOKEN_FILE. Unset env -> defaults
~/.config/portfolio-demos/client_secret.json and ~/.config/portfolio-demos/token-invoice-pdf-to-sheet.json. Missing client
file -> GoogleSetupError 'Google OAuth client file not found: <path>'."""

import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_ac_21_google_auth_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    google_sheet = importlib.import_module("invoice_pdf_to_sheet.google_sheet")

    client_file = tmp_path / "client_secret.json"
    client_file.write_text("{}")
    token_file = tmp_path / "tokens" / "token.json"
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_FILE", str(client_file))
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(token_file))
    flow_cls = MagicMock()
    flow_cls.from_client_secrets_file.return_value.run_local_server.return_value.to_json.return_value = '{"token": "t"}'
    monkeypatch.setattr(google_sheet, "InstalledAppFlow", flow_cls)

    google_sheet.load_credentials()

    args, kwargs = flow_cls.from_client_secrets_file.call_args
    scopes = kwargs.get("scopes", args[1] if len(args) > 1 else None)
    assert str(args[0]) == str(client_file)
    assert list(scopes) == ["https://www.googleapis.com/auth/drive.file"]
    assert token_file.read_text() == '{"token": "t"}'

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_FILE")
    monkeypatch.delenv("GOOGLE_TOKEN_FILE")
    default_client = home / ".config" / "portfolio-demos" / "client_secret.json"
    with pytest.raises(google_sheet.GoogleSetupError) as missing:
        google_sheet.load_credentials()
    assert str(missing.value) == f"Google OAuth client file not found: {default_client}"

    default_client.parent.mkdir(parents=True)
    default_client.write_text("{}")
    google_sheet.load_credentials()
    assert str(flow_cls.from_client_secrets_file.call_args.args[0]) == str(
        default_client
    )
    assert (
        home / ".config" / "portfolio-demos" / "token-invoice-pdf-to-sheet.json"
    ).read_text() == '{"token": "t"}'
