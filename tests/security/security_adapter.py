"""ADAPT: wire the kit's security tests (test_security.py) to this app. Owner-only, locked like tests/acceptance/.

Fill every function and constant marked ADAPT. A check that genuinely doesn't apply goes in NOT_APPLICABLE with
the reason; it then shows as skipped with that reason. Never use NOT_APPLICABLE to silence a failing check; a real hole the owner accepts
for now goes in KNOWN_OPEN (shows as xfail with the reason).
Check names: pii_logs, prompt_injection, model_output, step_skip, xff_spoof, cors, provider_error, body_cap, image_lock.
`make security` fails when a test listed in tests/security/EXPECTED_TESTS isn't collected by the full test run.
After adapting: uv run pytest tests/security --collect-only -q | grep '::' > tests/security/EXPECTED_TESTS
"""

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from starlette.testclient import TestClient

if TYPE_CHECKING:
    from kit_security import FakeLLM

REPO = Path(__file__).resolve().parents[2]

NOT_APPLICABLE: dict[str, str] = {
    "step_skip": "batch CLI, no multi-step HTTP workflow",
    "xff_spoof": "batch CLI, no HTTP endpoint to rate-limit",
    "cors": "batch CLI, no HTTP server",
    "body_cap": "batch CLI, no HTTP request bodies; input PDFs are size-capped in code (spec AC)",
    "image_lock": "no deploy image; runs locally from the uv lockfile",
}
# A real hole the owner accepts for now (shows as xfail with the reason, listed in the PR). Fix it, then remove the entry.
PRE_BUILD = "pipeline not built yet (PART-B); the shim below is wired to DEC-02/DEC-04, remove these entries in the [no-spec] PR right after PART-B merges"
KNOWN_OPEN: dict[str, str] = {
    "pii_logs": PRE_BUILD,
    "prompt_injection": PRE_BUILD,
    "model_output": PRE_BUILD,
    "provider_error": PRE_BUILD,
}

RATE_LIMIT = 5  # ADAPT: requests one session may make to the rate-limited endpoint before it gets 429
CORS_PATH = "/ADAPT"  # ADAPT: a route browsers call cross-origin
ALLOWED_ORIGIN = "https://ADAPT.example"  # ADAPT: the one origin allowed to call it
BODY_CAP_PATH = "/ADAPT"  # ADAPT: a POST route that takes a JSON body
REQUEST_ID_HEADER = "x-request-id"
USER_INPUT_TAG = "user_input"  # ADAPT: tag the app fences visitor text in, e.g. <user_input> or <visitor_input name="...">
# ADAPT: files that define deploy images, e.g. [REPO / "api" / "app.py"]
IMAGE_FILES: list[Path] = []


def app() -> Any:
    """Test-only HTTP shim around the batch CLI: POST /run {"text", "email"} prints the text into one PDF, runs
    `invoice-pdf-to-sheet run` on it and returns the row's status. Extraction failure -> 500 + request id."""
    import tempfile
    import uuid

    from openpyxl import load_workbook
    from reportlab.pdfgen import canvas
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def run(request: Request) -> JSONResponse:
        from invoice_pdf_to_sheet.cli import main

        body = await request.json()
        request_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "in"
            folder.mkdir()
            pdf = canvas.Canvas(str(folder / "visitor.pdf"), invariant=1)
            y = 800
            for line in [
                *str(body["text"]).splitlines(),
                str(body["email"]),
                "Invoice no: SHIM-1",
            ]:
                pdf.drawString(40, y, line)
                y -= 16
            pdf.save()
            out = Path(tmp) / "out.xlsx"
            main(["run", "--input", str(folder), "--xlsx", str(out)])
            rows = [
                dict(
                    zip(
                        [c.value for c in sheet[1]], [c.value for c in row], strict=True
                    )
                )
                for sheet in load_workbook(out).worksheets
                for row in sheet.iter_rows(min_row=2)
            ]
        headers = {REQUEST_ID_HEADER: request_id}
        if any("extraction failed" in str(r["reasons"] or "") for r in rows):
            return JSONResponse(
                {"error": "extraction failed", "request_id": request_id}, 500, headers
            )
        status = [{k: r[k] for k in ("file", "method", "confidence")} for r in rows]
        return JSONResponse({"rows": status}, 200, headers)

    return Starlette(routes=[Route("/run", run, methods=["POST"])])


def install_fakes(monkeypatch: pytest.MonkeyPatch, llm: "FakeLLM") -> None:
    """Every model call goes through the DEC-04 seam extract.call_model(system, content) -> InvoiceFields.
    Before PART-B exists there is nothing to patch (the LLM checks are KNOWN_OPEN until then)."""
    import importlib.util

    if importlib.util.find_spec("invoice_pdf_to_sheet.extract") is None:
        return
    from invoice_pdf_to_sheet.schema import InvoiceFields

    def fake_call_model(system: str, content: list[dict[str, Any]]) -> Any:
        user = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
        return InvoiceFields.model_validate(llm.respond(system, user))

    monkeypatch.setattr("invoice_pdf_to_sheet.extract.call_model", fake_call_model)


def good_reply() -> Any:
    return {
        "supplier_name": "Verdant Ridge Green Coffee Imports BV",
        "supplier_vat_id": "NL999000001B01",
        "invoice_number": "SHIM-1",
        "invoice_date": "2026-10-01",
        "due_date": "2026-10-31",
        "currency": "EUR",
        "net_total": 100.0,
        "vat_total": 21.0,
        "gross_total": 121.0,
        "lines": [
            {
                "description": "Item",
                "quantity": 1,
                "unit_price": 100.0,
                "line_total": 100.0,
            }
        ],
        "evidence": [{"field": "invoice_number", "page": 1, "quote": "SHIM-1"}],
    }


def invalid_reply(canary: str) -> Any:
    return {"supplier_name": canary}


def provider_error(canary: str) -> Exception:
    """ADAPT: the error the provider SDK raises on a 5xx, with canary in its message/body."""
    return RuntimeError(f"provider 500: {canary}")


def send_visitor_text(http: TestClient, text: str, email: str) -> Any:
    """ADAPT: run the full visitor flow that reaches the model, with text in EVERY free-text field and email where
    the app takes one. Return the response of the request that calls the model."""
    return http.post("/run", json={"text": text, "email": email})


def skip_step(http: TestClient) -> Any:
    """ADAPT: call a later workflow step directly, without the server-side state its earlier step creates."""
    raise NotImplementedError("ADAPT tests/security/security_adapter.py: skip_step()")


def writes() -> list[Any]:
    """ADAPT: what the in-memory fakes from install_fakes stored (sheet rows, emails...)."""
    raise NotImplementedError("ADAPT tests/security/security_adapter.py: writes()")


def call_rate_limited(http: TestClient, headers: dict[str, str]) -> Any:
    """ADAPT: one call to the rate-limited endpoint in the same visitor session, with these extra headers."""
    raise NotImplementedError(
        "ADAPT tests/security/security_adapter.py: call_rate_limited()"
    )


BREAK_DEPENDENCY: dict[str, Callable[[pytest.MonkeyPatch], None]] = {}
"""ADAPT: one entry per outside service the flow writes to, making it raise, e.g.
{"sheet": lambda mp: mp.setattr(app_module, "append_row", raise_error)}. PII must stay out of the logs when it fails."""
