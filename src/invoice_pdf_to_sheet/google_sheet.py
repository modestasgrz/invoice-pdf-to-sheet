import math
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from invoice_pdf_to_sheet.schema import COLUMNS, ResultRow
from invoice_pdf_to_sheet.xlsx import MONEY, WIDTHS

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER = "application/vnd.google-apps.folder"
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
DATES = {"invoice_date", "due_date"}
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
    """Under USER_ENTERED, Sheets turns text into formulas, error values, booleans, numbers, dates or times (locale
    dependent, e.g. "Oct 1, 2026"); a leading apostrophe (not shown in the cell) keeps such text exactly as typed. Text
    with no digit, no boolean word and no formula/error/apostrophe start can't be coerced and goes as is. Non-finite numbers aren't
    valid JSON, so they go as text too."""
    if value is None:
        return ""
    if isinstance(value, str):
        coercible = (
            value.lstrip().startswith(("=", "+", "-", "@", "#", "'"))
            or any(c.isdigit() for c in value)
            or value.strip().casefold() in ("true", "false")
        )
        return "'" + value if coercible else value
    if not math.isfinite(value):
        return as_text(str(value))
    return value


def real_date(value: str | float | None) -> bool:
    """A real calendar date in plain ISO form goes to Sheets unprefixed so it becomes a date cell."""
    if not isinstance(value, str) or not ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


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

    def tab(
        sheet_id: int, name: str, fill: dict[str, float], rows: list[ResultRow]
    ) -> dict[str, Any]:
        """A tab formatted like the xlsx: coloured bold header, frozen header and file column, filter, widths, formats."""
        cell_formats = [
            {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}
            if column in MONEY
            else {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}
            if column in DATES
            else {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}
            if column in ("evidence", "reasons")
            else {}
            for column in COLUMNS[:-1]
        ]
        header = {
            "textFormat": {
                "bold": True,
                "foregroundColorStyle": {"rgbColor": {"red": 1, "green": 1, "blue": 1}},
            },
            "backgroundColorStyle": {"rgbColor": fill},
        }
        return {
            "properties": {
                "sheetId": sheet_id,
                "title": name,
                "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1},
            },
            "basicFilter": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": len(rows) + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(COLUMNS),
                }
            },
            "data": [
                {
                    "startRow": 0,
                    "startColumn": 0,
                    "columnMetadata": [
                        {"pixelSize": WIDTHS[c] * 7 + 10} for c in COLUMNS
                    ],
                    "rowData": [
                        {"values": [{"userEnteredFormat": header} for _ in COLUMNS]},
                        *(
                            {"values": [{"userEnteredFormat": f} for f in cell_formats]}
                            for _ in rows
                        ),
                    ],
                }
            ],
        }

    spreadsheet = (
        sheets.spreadsheets()
        .create(
            body={
                "properties": {"title": title},
                "sheets": [
                    tab(
                        0,
                        "Ready",
                        {"red": 0.12, "green": 0.16, "blue": 0.22},
                        ready,
                    ),
                    tab(
                        1,
                        "Needs review",
                        {"red": 0.71, "green": 0.33, "blue": 0.04},
                        review,
                    ),
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
                    str(value)
                    if column in DATES and real_date(value)
                    else as_text(value)
                    for column, value in zip(COLUMNS[:-1], row.cells(), strict=True)
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
    # The link column goes last, as plain text with a link run: =HYPERLINK's argument separator depends on the Sheet's
    # locale (#ERROR! where it is ';'), and a cell-level textFormat.link in the create body is dropped by the live API.
    link = COLUMNS.index("pdf_link")
    if not ready and not review:
        return str(spreadsheet["spreadsheetUrl"])
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet["spreadsheetId"],
        body={
            "requests": [
                {
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": len(rows) + 1,
                            "startColumnIndex": link,
                            "endColumnIndex": link + 1,
                        },
                        "rows": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {"stringValue": row.file},
                                        "textFormatRuns": [
                                            {
                                                "startIndex": 0,
                                                "format": {
                                                    "link": {"uri": links[row.file]}
                                                },
                                            }
                                        ],
                                    }
                                    if row.file in links
                                    else {
                                        "userEnteredValue": {
                                            "stringValue": "(not uploaded)"
                                        }
                                    }
                                ]
                            }
                            for row in rows
                        ],
                        "fields": "userEnteredValue,textFormatRuns",
                    }
                }
                for sheet_id, rows in ((0, ready), (1, review))
                if rows
            ]
        },
    ).execute()
    return str(spreadsheet["spreadsheetUrl"])
