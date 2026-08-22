from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from backend.main import _health_payload
from backend.routers import settings
from backend.services.pocketoption_otc import PocketOptionOTCService
from backend.services.manual_worker_tasks import analyze_market, candles
from backend.telegram_auth import TelegramMiniAppUser


def test_public_health_does_not_disclose_network_or_repository_details():
    payload = asyncio.run(_health_payload())
    assert "backend_url" not in payload
    assert "source" not in payload
    assert "market" not in payload


def test_user_cannot_read_another_users_settings():
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            settings.get(
                telegram_id=222,
                user=TelegramMiniAppUser(id=111),
                db=None,
            )
        )
    assert caught.value.status_code == 403


def test_web_runtime_ignores_pocket_credential(monkeypatch):
    monkeypatch.setenv("APP_RUNTIME_ROLE", "web")
    monkeypatch.setenv("POCKET_OPTION_SSID", "sensitive-session")
    assert PocketOptionOTCService().configured is False


def test_worker_runtime_can_read_local_pocket_credential(monkeypatch):
    monkeypatch.setenv("APP_RUNTIME_ROLE", "worker")
    monkeypatch.setenv("POCKET_OPTION_SSID", "local-worker-session")
    assert PocketOptionOTCService().configured is True


def test_worker_market_tasks_reject_untrusted_symbols_before_network_access():
    with pytest.raises(ValueError, match="Unsupported OTC pair"):
        asyncio.run(analyze_market({"pair": "ATTACK/USD", "timeframe": "1m"}))
    with pytest.raises(ValueError, match="Unsupported OTC pair"):
        asyncio.run(candles({"pair": "ATTACK/USD", "timeframe": "1m", "count": 60}))
