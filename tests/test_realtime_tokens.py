from __future__ import annotations

import pytest

from backend.services.realtime_tokens import issue_realtime_token, verify_realtime_token


def test_realtime_token_round_trip(monkeypatch):
    monkeypatch.setenv("WORKER_SHARED_SECRET", "a" * 32)
    token = issue_realtime_token(telegram_id=7591614041, account_id=7)
    payload = verify_realtime_token(token)
    assert payload["sub"] == 7591614041
    assert payload["account_id"] == 7
    assert payload["scope"] == "realtime:read"


def test_realtime_token_rejects_tampering(monkeypatch):
    monkeypatch.setenv("WORKER_SHARED_SECRET", "b" * 32)
    token = issue_realtime_token(telegram_id=1, account_id=2)
    with pytest.raises(ValueError):
        verify_realtime_token(token[:-1] + ("A" if token[-1] != "A" else "B"))
