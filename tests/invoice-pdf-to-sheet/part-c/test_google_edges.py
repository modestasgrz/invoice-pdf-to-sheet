"""PART-C edge cases for google_sheet (AC-20..22, DEC-08, DEC-12): scopes, no sharing calls, folder membership, value shapes, error leaks."""

import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError
from loguru import logger
from reportlab.pdfgen import canvas

from invoice_pdf_to_sheet import google_sheet
from invoice_pdf_to_sheet.cli import main
from invoice_pdf_to_sheet.schema import COLUMNS, InvoiceFields, ResultRow

SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
TOKEN_SECRET = "TOKEN_SECRET_VALUE_9137"
BODY_SECRET = "SECRET_PROVIDER_BODY_77"


class Call:
    def __init__(self, result: dict[str, Any], error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def execute(self) -> dict[str, Any]:
        if self.error:
            raise self.error
        return self.result


class FakeDrive:
    """Only files().create/update exist; any other attribute (permissions, ...) fails the test."""

    def __init__(self, fail_upload_number: int | None = None) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.fail_upload_number = fail_upload_number
        self.uploads = 0

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected Drive call: {name}")

    def files(self) -> "FakeDrive":
        return self

    def create(self, **kwargs: Any) -> Call:
        self.events.append(("create", kwargs))
        body = kwargs["body"]
        if body["mimeType"] == FOLDER_MIME:
            return Call({"id": "FOLDER"})
        self.uploads += 1
        if self.uploads == self.fail_upload_number:
            error = HttpError(
                httplib2.Response({"status": "403"}), BODY_SECRET.encode()
            )
            return Call({}, error)
        return Call(
            {
                "id": f"F{self.uploads}",
                "webViewLink": f"https://drive.example/{body['name']}",
            }
        )

    def update(self, **kwargs: Any) -> Call:
        self.events.append(("update", kwargs))
        return Call({"id": kwargs["fileId"]})


class FakeValues:
    """spreadsheets().values(): its batchUpdate is recorded apart from spreadsheets().batchUpdate."""

    def __init__(self, sheets: "FakeSheets") -> None:
        self.sheets = sheets

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected Sheets values call: {name}")

    def batchUpdate(self, **kwargs: Any) -> Call:
        self.sheets.events.append(("values.batchUpdate", kwargs))
        return Call({})


class FakeSheets:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected Sheets call: {name}")

    def spreadsheets(self) -> "FakeSheets":
        return self

    def values(self) -> FakeValues:
        return FakeValues(self)

    def create(self, **kwargs: Any) -> Call:
        self.events.append(("create", kwargs))
        return Call(
            {"spreadsheetId": "S1", "spreadsheetUrl": "https://sheet.example/S1"}
        )

    def batchUpdate(self, **kwargs: Any) -> Call:
        self.events.append(("batchUpdate", kwargs))
        return Call({})


class Services:
    def __init__(self, drive: FakeDrive, sheets: FakeSheets, media: MagicMock) -> None:
        self.drive = drive
        self.sheets = sheets
        self.media = media


def install(
    monkeypatch: pytest.MonkeyPatch, fail_upload_number: int | None = None
) -> Services:
    drive, sheets, media = FakeDrive(fail_upload_number), FakeSheets(), MagicMock()
    monkeypatch.setattr(google_sheet, "load_credentials", lambda: object())
    monkeypatch.setattr(google_sheet, "MediaFileUpload", media)
    monkeypatch.setattr(
        google_sheet,
        "build",
        lambda name, version, credentials: drive if name == "drive" else sheets,
    )
    return Services(drive, sheets, media)


def make_row(tmp_path: Path, name: str, **fields: Any) -> ResultRow:
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.4\n")
    return ResultRow(file=name, pdf_path=pdf, **fields)


def sheet_data(services: Services) -> dict[str, list[list[Any]]]:
    batches = [
        kw for kind, kw in services.sheets.events if kind == "values.batchUpdate"
    ]
    assert len(batches) == 1
    assert batches[0]["body"]["valueInputOption"] == "USER_ENTERED"
    return {d["range"]: d["values"] for d in batches[0]["body"]["data"]}


def link_requests(services: Services) -> list[dict[str, Any]]:
    """The updateCells requests of the one spreadsheets().batchUpdate call (none when that call was never made)."""
    batches = [kw for kind, kw in services.sheets.events if kind == "batchUpdate"]
    if not batches:
        return []
    assert len(batches) == 1
    assert set(batches[0]) == {"spreadsheetId", "body"}
    assert batches[0]["spreadsheetId"] == "S1"
    assert set(batches[0]["body"]) == {"requests"}
    requests: list[dict[str, Any]] = batches[0]["body"]["requests"]
    assert all(set(r) == {"updateCells"} for r in requests)
    return [r["updateCells"] for r in requests]


def link_cells(services: Services, tab: int) -> list[dict[str, Any]]:
    """The pdf_link cells sent for one tab (0 Ready, 1 Needs review), with the range they are written to checked."""
    found = [r for r in link_requests(services) if r["range"]["sheetId"] == tab]
    if not found:
        return []
    assert len(found) == 1
    request = found[0]
    link = COLUMNS.index("pdf_link")
    rows = request["rows"]
    assert request["fields"] == "userEnteredValue,textFormatRuns"
    assert request["range"] == {
        "sheetId": tab,
        "startRowIndex": 1,
        "endRowIndex": len(rows) + 1,
        "startColumnIndex": link,
        "endColumnIndex": link + 1,
    }
    assert all(len(r["values"]) == 1 for r in rows)
    cells: list[dict[str, Any]] = [r["values"][0] for r in rows]
    return cells


def linked(name: str, uri: str) -> dict[str, Any]:
    return {
        "userEnteredValue": {"stringValue": name},
        "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": uri}}}],
    }


# ---------- scopes and credential loading ----------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    client = tmp_path / "client.json"
    client.write_text("{}")
    token = tmp_path / "nested" / "dir" / "token.json"
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_FILE", str(client))
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(token))
    monkeypatch.setattr(google_sheet, "Request", MagicMock())
    return client, token


def test_scope_constant_is_exactly_drive_file() -> None:
    assert google_sheet.SCOPES == [SCOPE]


def test_new_flow_gets_exact_scope_client_path_and_ephemeral_port(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = MagicMock()
    creds = flow.from_client_secrets_file.return_value.run_local_server.return_value
    creds.to_json.return_value = '{"t": 1}'
    monkeypatch.setattr(google_sheet, "InstalledAppFlow", flow)

    assert google_sheet.load_credentials() is creds

    flow.from_client_secrets_file.assert_called_once_with(str(env[0]), [SCOPE])
    flow.from_client_secrets_file.return_value.run_local_server.assert_called_once_with(
        port=0
    )
    assert env[1].read_text() == '{"t": 1}'
    assert env[1].parent.is_dir()


def test_existing_token_load_uses_exact_scope(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    env[1].parent.mkdir(parents=True)
    env[1].write_text("{}")
    untyped = MagicMock()
    untyped.from_authorized_user_file.return_value = MagicMock(valid=True)
    flow = MagicMock()
    monkeypatch.setattr(google_sheet, "UntypedCredentials", untyped)
    monkeypatch.setattr(google_sheet, "InstalledAppFlow", flow)

    google_sheet.load_credentials()

    untyped.from_authorized_user_file.assert_called_once_with(str(env[1]), [SCOPE])
    flow.from_client_secrets_file.assert_not_called()


def test_refresh_keeps_scope_and_rewrites_token_owner_only(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    env[1].parent.mkdir(parents=True)
    env[1].write_text("{}")
    creds = MagicMock(valid=False, expired=True, refresh_token="r")
    creds.to_json.return_value = '{"fresh": true}'
    untyped = MagicMock()
    untyped.from_authorized_user_file.return_value = creds
    flow = MagicMock()
    monkeypatch.setattr(google_sheet, "UntypedCredentials", untyped)
    monkeypatch.setattr(google_sheet, "InstalledAppFlow", flow)

    assert google_sheet.load_credentials() is creds

    untyped.from_authorized_user_file.assert_called_once_with(str(env[1]), [SCOPE])
    creds.refresh.assert_called_once()
    assert env[1].read_text() == '{"fresh": true}'
    assert env[1].stat().st_mode & 0o777 == 0o600
    flow.from_client_secrets_file.assert_not_called()


def test_invalid_token_without_refresh_token_falls_back_to_flow_with_exact_scope(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    env[1].parent.mkdir(parents=True)
    env[1].write_text("{}")
    stale = MagicMock(valid=False, expired=True, refresh_token=None)
    untyped = MagicMock()
    untyped.from_authorized_user_file.return_value = stale
    flow = MagicMock()
    flow.from_client_secrets_file.return_value.run_local_server.return_value.to_json.return_value = "{}"
    monkeypatch.setattr(google_sheet, "UntypedCredentials", untyped)
    monkeypatch.setattr(google_sheet, "InstalledAppFlow", flow)

    google_sheet.load_credentials()

    stale.refresh.assert_not_called()
    flow.from_client_secrets_file.assert_called_once_with(str(env[0]), [SCOPE])


def test_missing_client_file_message_is_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "nope" / "client_secret.json"
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_FILE", str(missing))
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))
    flow = MagicMock()
    monkeypatch.setattr(google_sheet, "InstalledAppFlow", flow)

    with pytest.raises(google_sheet.GoogleSetupError) as caught:
        google_sheet.load_credentials()

    assert str(caught.value) == f"Google OAuth client file not found: {missing}"
    flow.from_client_secrets_file.assert_not_called()
    assert not (tmp_path / "token.json").exists()


def test_default_paths_used_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_TOKEN_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(google_sheet.GoogleSetupError) as caught:
        google_sheet.load_credentials()
    assert str(caught.value) == (
        f"Google OAuth client file not found: {tmp_path}/.config/portfolio-demos/client_secret.json"
    )

    config = tmp_path / ".config" / "portfolio-demos"
    config.mkdir(parents=True)
    (config / "client_secret.json").write_text("{}")
    flow = MagicMock()
    flow.from_client_secrets_file.return_value.run_local_server.return_value.to_json.return_value = "{}"
    monkeypatch.setattr(google_sheet, "InstalledAppFlow", flow)
    google_sheet.load_credentials()
    flow.from_client_secrets_file.assert_called_once_with(
        str(config / "client_secret.json"), [SCOPE]
    )
    assert (config / "token-invoice-pdf-to-sheet.json").exists()


def test_tilde_in_env_paths_is_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_FILE", "~/missing-client.json")
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", "~/tok.json")
    with pytest.raises(google_sheet.GoogleSetupError) as caught:
        google_sheet.load_credentials()
    assert (
        str(caught.value)
        == f"Google OAuth client file not found: {tmp_path}/missing-client.json"
    )


def test_token_contents_never_printed_or_logged(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="TRACE")
    try:
        env[1].parent.mkdir(parents=True)
        env[1].write_text(TOKEN_SECRET)
        creds = MagicMock(valid=False, expired=True, refresh_token="r")
        creds.to_json.return_value = TOKEN_SECRET
        untyped = MagicMock()
        untyped.from_authorized_user_file.return_value = creds
        monkeypatch.setattr(google_sheet, "UntypedCredentials", untyped)
        google_sheet.load_credentials()

        env[1].unlink()
        flow = MagicMock()
        flow.from_client_secrets_file.return_value.run_local_server.return_value.to_json.return_value = TOKEN_SECRET
        monkeypatch.setattr(google_sheet, "InstalledAppFlow", flow)
        google_sheet.load_credentials()
    finally:
        logger.remove(sink)

    captured = capsys.readouterr()
    assert TOKEN_SECRET not in captured.out + captured.err + "".join(messages)


# ---------- publish ----------


def test_title_is_timestamp_and_shared_by_folder_and_spreadsheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = install(monkeypatch)
    google_sheet.publish([make_row(tmp_path, "a.pdf")], [])

    folder = services.drive.events[0][1]["body"]
    assert folder["mimeType"] == FOLDER_MIME
    assert re.fullmatch(
        r"invoice-pdf-to-sheet \d{4}-\d{2}-\d{2} \d{2}:\d{2}", folder["name"]
    )
    assert services.drive.events[0][1]["fields"] == "id"
    created = services.sheets.events[0][1]
    assert created["body"]["properties"]["title"] == folder["name"]
    assert created["fields"] == "spreadsheetId,spreadsheetUrl"


def test_every_upload_goes_into_created_folder_as_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = install(monkeypatch)
    ready = [make_row(tmp_path, "a.pdf"), make_row(tmp_path, "b.pdf")]
    review = [
        make_row(tmp_path, "c.pdf", reasons=["x"]),
        make_row(tmp_path, "d.pdf", reasons=["y"]),
    ]

    google_sheet.publish(ready, review)

    creates = [kw for kind, kw in services.drive.events if kind == "create"]
    assert sum(c["body"]["mimeType"] == FOLDER_MIME for c in creates) == 1
    uploads = [c for c in creates if c["body"]["mimeType"] != FOLDER_MIME]
    assert [u["body"]["name"] for u in uploads] == ["a.pdf", "b.pdf", "c.pdf", "d.pdf"]
    for upload in uploads:
        assert upload["body"]["mimeType"] == "application/pdf"
        assert upload["body"]["parents"] == ["FOLDER"]
        assert upload["fields"] == "id,webViewLink"
        assert "media_body" in upload
    paths = [call.args[0] for call in services.media.call_args_list]
    assert paths == [str(r.pdf_path) for r in [*ready, *review]]
    assert all(
        call.kwargs == {"mimetype": "application/pdf"}
        for call in services.media.call_args_list
    )


def test_spreadsheet_is_moved_into_folder_after_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = install(monkeypatch)
    url = google_sheet.publish([make_row(tmp_path, "a.pdf")], [])

    assert url == "https://sheet.example/S1"
    updates = [kw for kind, kw in services.drive.events if kind == "update"]
    assert updates == [
        {
            "fileId": "S1",
            "addParents": "FOLDER",
            "removeParents": "root",
            "fields": "id",
        }
    ]
    titles = [
        s["properties"]["title"] for s in services.sheets.events[0][1]["body"]["sheets"]
    ]
    assert titles == ["Ready", "Needs review"]
    assert [k for k, _ in services.sheets.events] == [
        "create",
        "values.batchUpdate",
        "batchUpdate",
    ]


def test_no_sharing_or_permission_calls_ever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = install(monkeypatch)
    google_sheet.publish(
        [make_row(tmp_path, "a.pdf")], [make_row(tmp_path, "b.pdf", reasons=["r"])]
    )

    # the fakes raise on any attribute other than files()/spreadsheets()/values(); also no sharing arguments
    assert {k for k, _ in services.drive.events} == {"create", "update"}
    assert {k for k, _ in services.sheets.events} == {
        "create",
        "values.batchUpdate",
        "batchUpdate",
    }
    flat = repr(services.drive.events) + repr(services.sheets.events)
    for word in ("permission", "emailAddress", "anyone", "role", "transferOwnership"):
        assert word not in flat


def test_values_have_header_rows_in_order_and_numbers_stay_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = install(monkeypatch)
    ready = [
        make_row(
            tmp_path,
            "z.pdf",
            supplier_name="S1",
            net_total=250.0,
            vat_total=52.5,
            gross_total=302.5,
            line_count=2,
            method="text",
            confidence="verified",
        ),
        make_row(
            tmp_path,
            "a.pdf",
            supplier_name="S2",
            net_total=-10.0,
            vat_total=0.0,
            gross_total=-10.0,
            line_count=0,
            method="text",
            confidence="verified",
        ),
    ]
    review = [
        make_row(
            tmp_path,
            "m.pdf",
            reasons=["lines sum 1 != net 2", "missing supplier VAT id"],
        )
    ]

    google_sheet.publish(ready, review)

    data = sheet_data(services)
    ready_values, review_values = data["'Ready'!A1"], data["'Needs review'!A1"]
    assert ready_values[0] == COLUMNS == review_values[0]
    assert [r[0] for r in ready_values[1:]] == ["z.pdf", "a.pdf"]
    assert [r[0] for r in review_values[1:]] == ["m.pdf"]
    first = dict(zip(COLUMNS[:-1], ready_values[1], strict=True))
    assert first["net_total"] == 250.0 and isinstance(first["net_total"], float)
    assert first["gross_total"] == 302.5
    assert first["line_count"] == 2 and isinstance(first["line_count"], int)
    second = dict(zip(COLUMNS[:-1], ready_values[2], strict=True))
    assert second["net_total"] == -10.0 and isinstance(second["net_total"], float)
    assert second["line_count"] == 0
    assert len(ready_values[0]) == len(COLUMNS) == len(review_values[0])
    assert all(
        len(r) == len(COLUMNS) - 1 for r in [*ready_values[1:], *review_values[1:]]
    )
    only = dict(zip(COLUMNS[:-1], review_values[1], strict=True))
    assert only["reasons"] == "'lines sum 1 != net 2; missing supplier VAT id"
    assert (
        only["supplier_name"] == ""
        and only["net_total"] == ""
        and only["line_count"] == ""
    )
    assert link_cells(services, 0) == [
        linked(name, f"https://drive.example/{name}") for name in ("z.pdf", "a.pdf")
    ]
    assert link_cells(services, 1) == [linked("m.pdf", "https://drive.example/m.pdf")]
    create = next(kw for kind, kw in services.sheets.events if kind == "create")
    for tab in create["body"]["sheets"]:
        for row in tab["data"][0]["rowData"][1:]:
            assert len(row["values"]) == len(COLUMNS) - 1


def test_empty_ready_and_empty_review_each_get_only_a_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = install(monkeypatch)
    google_sheet.publish([], [make_row(tmp_path, "c.pdf", reasons=["r"])])
    data = sheet_data(services)
    assert data["'Ready'!A1"] == [COLUMNS]
    assert len(data["'Needs review'!A1"]) == 2
    assert [r["range"]["sheetId"] for r in link_requests(services)] == [1]

    services = install(monkeypatch)
    google_sheet.publish([make_row(tmp_path, "a.pdf")], [])
    data = sheet_data(services)
    assert data["'Needs review'!A1"] == [COLUMNS]
    assert len(data["'Ready'!A1"]) == 2
    assert [r["range"]["sheetId"] for r in link_requests(services)] == [0]


def test_both_lists_empty_creates_folder_and_sheet_but_uploads_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = install(monkeypatch)

    assert google_sheet.publish([], []) == "https://sheet.example/S1"

    creates = [kw for kind, kw in services.drive.events if kind == "create"]
    assert [c["body"]["mimeType"] for c in creates] == [FOLDER_MIME]
    services.media.assert_not_called()
    data = sheet_data(services)
    assert data == {"'Ready'!A1": [COLUMNS], "'Needs review'!A1": [COLUMNS]}
    assert "batchUpdate" not in [k for k, _ in services.sheets.events]


def test_formula_like_file_name_is_text_in_file_cell_but_raw_in_link_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = install(monkeypatch)
    row = make_row(tmp_path, "=1+1.pdf", reasons=["@boom"], supplier_name="-x")

    google_sheet.publish([], [row])

    values = sheet_data(services)["'Needs review'!A1"][1]
    cells = dict(zip(COLUMNS[:-1], values, strict=True))
    assert cells["file"] == "'=1+1.pdf"
    assert cells["reasons"] == "'@boom"
    assert cells["supplier_name"] == "'-x"
    assert link_cells(services, 1) == [
        linked("=1+1.pdf", "https://drive.example/=1+1.pdf")
    ]
    assert link_cells(services, 0) == []


def test_http_error_mid_upload_propagates_and_nothing_after_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = install(monkeypatch, fail_upload_number=2)
    rows = [make_row(tmp_path, f"{n}.pdf") for n in "abc"]

    with pytest.raises(HttpError):
        google_sheet.publish(rows[:2], rows[2:])

    assert services.drive.uploads == 2
    assert services.sheets.events == []
    assert not [e for e in services.drive.events if e[0] == "update"]


# ---------- CLI ----------


def invoice_reply() -> InvoiceFields:
    return InvoiceFields.model_validate(
        {
            "supplier_name": "Verdant Ridge Green Coffee Imports BV",
            "supplier_vat_id": "NL999000001B01",
            "invoice_number": "VR-2026-0101",
            "invoice_date": "2026-10-01",
            "due_date": "2026-10-31",
            "currency": "EUR",
            "net_total": 250.0,
            "vat_total": 52.5,
            "gross_total": 302.5,
            "lines": [
                {
                    "description": "Green coffee",
                    "quantity": 2,
                    "unit_price": 100.0,
                    "line_total": 200.0,
                },
                {
                    "description": "Fee",
                    "quantity": 1,
                    "unit_price": 50.0,
                    "line_total": 50.0,
                },
            ],
            "evidence": [],
        }
    )


def cli_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    source = tmp_path / "in"
    source.mkdir()
    pdf = canvas.Canvas(str(source / "a.pdf"), invariant=1)
    pdf.drawString(
        40,
        800,
        "Verdant Ridge Green Coffee Imports BV invoice VR-2026-0101 total 302.50",
    )
    pdf.save()
    monkeypatch.setattr(
        "invoice_pdf_to_sheet.extract.call_model",
        lambda system, content: invoice_reply(),
    )
    return [
        "run",
        "--input",
        str(source),
        "--xlsx",
        str(tmp_path / "out.xlsx"),
        "--google",
    ]


def test_cli_http_error_mid_upload_exits_1_with_type_name_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = cli_argv(tmp_path, monkeypatch)
    install(monkeypatch, fail_upload_number=1)
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="TRACE")
    try:
        code = main(argv)
    finally:
        logger.remove(sink)

    captured = capsys.readouterr()
    assert code == 1
    assert (tmp_path / "out.xlsx").exists()
    assert (
        captured.err.strip().splitlines()[-1] == "Google Sheet upload failed: HttpError"
    )
    everything = captured.out + captured.err + "".join(messages)
    assert BODY_SECRET not in everything
    assert "403" not in captured.err
    assert "Google Sheet:" not in captured.out


def test_cli_setup_error_prints_type_name_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = cli_argv(tmp_path, monkeypatch)
    missing = tmp_path / "private-dir-name" / "client.json"
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_FILE", str(missing))
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))

    code = main(argv)

    captured = capsys.readouterr()
    assert code == 1
    assert "Google Sheet upload failed: GoogleSetupError" in captured.err
    assert "private-dir-name" not in captured.out + captured.err


def test_cli_success_prints_url_and_publishes_split_lists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = cli_argv(tmp_path, monkeypatch)
    services = install(monkeypatch)

    code = main(argv)

    captured = capsys.readouterr()
    assert code == 0
    assert "Google Sheet: https://sheet.example/S1" in captured.out.splitlines()
    data = sheet_data(services)
    assert len(data["'Ready'!A1"]) + len(data["'Needs review'!A1"]) == 3
    assert len(link_cells(services, 0)) + len(link_cells(services, 1)) == 1
