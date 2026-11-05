"""AC-19: Given anthropic.Anthropic patched with a mock, when the real call_model runs once, then the client was built with
timeout=60 and max_retries=3, and the call used client.beta.messages.parse with model claude-sonnet-5-5, max_tokens 16000,
output_format InvoiceFields, betas ["server-side-fallback-2026-07-01"] and fallbacks "default"."""

import importlib
from typing import Any
from unittest.mock import MagicMock

import anthropic
import pytest


def test_ac_19_model_call_config(
    monkeypatch: pytest.MonkeyPatch, consistent: Any
) -> None:
    from invoice_pdf_to_sheet.schema import InvoiceFields

    extract = importlib.import_module("invoice_pdf_to_sheet.extract")

    client_cls = MagicMock()
    parse = client_cls.return_value.beta.messages.parse
    parse.return_value.stop_reason = "end_turn"
    parse.return_value.parsed_output = InvoiceFields.model_validate(consistent())
    monkeypatch.setattr(anthropic, "Anthropic", client_cls)

    result = extract.call_model(
        "system prompt", [{"type": "text", "text": "<user_input>x</user_input>"}]
    )

    assert result == parse.return_value.parsed_output
    client_cls.assert_called_once()
    assert client_cls.call_args.kwargs["timeout"] == 60
    assert client_cls.call_args.kwargs["max_retries"] == 3
    parse.assert_called_once()
    kwargs = parse.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-5-5"
    assert kwargs["max_tokens"] == 16000
    assert kwargs["output_format"] is InvoiceFields
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["fallbacks"] == "default"
    assert kwargs["system"] == "system prompt"
