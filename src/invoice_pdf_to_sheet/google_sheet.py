import os
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER = "application/vnd.google-apps.folder"
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
        creds: Any = UntypedCredentials.from_authorized_user_file(str(token), SCOPES)
        if creds.valid:
            return cast(Credentials, creds)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token.write_text(creds.to_json())
            token.chmod(0o600)
            return cast(Credentials, creds)
    if not client.exists():
        raise GoogleSetupError(f"Google OAuth client file not found: {client}")
    creds = InstalledAppFlow.from_client_secrets_file(
        str(client), SCOPES
    ).run_local_server(port=0)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json())
    token.chmod(0o600)
    return cast(Credentials, creds)


def as_text(value: str | float | None) -> str | float | int:
    """USER_ENTERED would run a leading = + - @ as a formula; a leading apostrophe keeps it plain text."""
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
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
        fileId=spreadsheet["spreadsheetId"], addParents=folder_id, fields="id"
    ).execute()

    def values(rows: list[ResultRow]) -> list[list[str | float | int]]:
        return [
            list(COLUMNS),
            *(
                [
                    *map(as_text, row.cells()),
                    f'=HYPERLINK("{links[row.file]}", "{row.file.replace('"', '""')}")',
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
