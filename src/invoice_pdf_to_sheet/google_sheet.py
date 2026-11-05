import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER = "application/vnd.google-apps.folder"
COERCIBLE = re.compile(r"[\d\s.,/:%$€()-]+|true|false", re.IGNORECASE)
# google-auth ships no type hints; going through Any keeps mypy strict elsewhere
UntypedCredentials: Any = Credentials


class GoogleSetupError(Exception):
    pass


def load_credentials() -> Credentials:
    client = Path(
        os.environ.get(
            "GOOGLE_OAUTH_CLIENT_FILE", "~/.config/portfolio-demos/client_secret.json"
        )
    ).expanduser()
    token = Path(
        os.environ.get(
            "GOOGLE_TOKEN_FILE",
            "~/.config/portfolio-demos/token-invoice-pdf-to-sheet.json",
        )
    ).expanduser()
    if token.exists():
        try:
            creds: Any = UntypedCredentials.from_authorized_user_file(
                str(token), SCOPES
            )
            if creds.valid:
                return cast(Credentials, creds)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token.chmod(0o600)
                token.write_text(creds.to_json())
                return cast(Credentials, creds)
        except (ValueError, RefreshError):
            pass  # corrupt or revoked token (Testing-mode refresh tokens die after 7 days): sign in again
    if not client.exists():
        raise GoogleSetupError(f"Google OAuth client file not found: {client}")
    creds = InstalledAppFlow.from_client_secrets_file(
        str(client), SCOPES
    ).run_local_server(port=0)
    token.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    token.touch(mode=0o600)
    token.chmod(0o600)
    token.write_text(creds.to_json())
    return cast(Credentials, creds)


def as_text(value: str | float | None) -> str | float | int:
    """Under USER_ENTERED, Sheets would turn text into formulas, numbers or dates; a leading apostrophe keeps it as typed.
    Non-finite numbers aren't valid JSON, so they go as text too."""
    if value is None:
        return ""
    if isinstance(value, str):
        coercible = value.startswith(("=", "+", "-", "@")) or COERCIBLE.fullmatch(
            value.strip()
        )
        return "'" + value if coercible else value
    if not math.isfinite(value):
        return str(value)
    return value


def publish(ready: list[ResultRow], review: list[ResultRow]) -> str:
    creds = load_credentials()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    title = f"invoice-pdf-to-sheet {datetime.now():%Y-%m-%d %H:%M}"

    folder_id = (
        drive.files()
        .create(body={"name": title, "mimeType": FOLDER}, fields="id")
        .execute()["id"]
    )
    links: dict[str, str] = {}
    for row in [*ready, *review]:
        if row.pdf_path.is_symlink() or not row.pdf_path.is_file():
            continue
        uploaded = (
            drive.files()
            .create(
                body={
                    "name": row.file,
                    "mimeType": "application/pdf",
                    "parents": [folder_id],
                },
                media_body=MediaFileUpload(
                    str(row.pdf_path), mimetype="application/pdf"
                ),
                fields="id,webViewLink",
            )
            .execute()
        )
        links[row.file] = uploaded["webViewLink"]

    spreadsheet = (
        sheets.spreadsheets()
        .create(
            body={
                "properties": {"title": title},
                "sheets": [
                    {"properties": {"title": "Ready"}},
                    {"properties": {"title": "Needs review"}},
                ],
            },
            fields="spreadsheetId,spreadsheetUrl",
        )
        .execute()
    )
    drive.files().update(
        fileId=spreadsheet["spreadsheetId"],
        addParents=folder_id,
        removeParents="root",
        fields="id",
    ).execute()

    def values(rows: list[ResultRow]) -> list[list[str | float | int]]:
        return [
            list(COLUMNS),
            *(
                [
                    *map(as_text, row.cells()),
                    f'=HYPERLINK("{links[row.file]}", "{row.file.replace('"', '""')}")'
                    if row.file in links
                    else "(not uploaded)",
                ]
                for row in rows
            ),
        ]

    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet["spreadsheetId"],
        body={
            "valueInputOption": "USER_ENTERED",
            "data": [
                {"range": "'Ready'!A1", "values": values(ready)},
                {"range": "'Needs review'!A1", "values": values(review)},
            ],
        },
    ).execute()
    url: str = spreadsheet["spreadsheetUrl"]
    return url
