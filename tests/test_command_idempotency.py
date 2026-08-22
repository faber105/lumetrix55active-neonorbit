from __future__ import annotations

from backend.routers.auto import _command_key


def test_explicit_command_key_is_preserved_for_http_retries():
    payload = {"mode": "count", "strategy": "ema_trend"}
    first = _command_key(123456789, "START_SESSION", payload, "request-123")
    second = _command_key(123456789, "START_SESSION", payload, "request-123")
    assert first == second == "request-123"


def test_generated_command_key_changes_with_payload():
    first = _command_key(123456789, "START_SESSION", {"amount": 1}, None)
    second = _command_key(123456789, "START_SESSION", {"amount": 2}, None)
    assert first != second
