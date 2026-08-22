from __future__ import annotations

from backend.services.auto_realtime import realtime_driver_enabled


def test_driver_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("APP_RUNTIME_ROLE", raising=False)
    monkeypatch.delenv("AUTO_REALTIME_DRIVER", raising=False)
    assert realtime_driver_enabled() is False


def test_driver_requires_worker_role_and_flag(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("APP_RUNTIME_ROLE", "worker")
    monkeypatch.setenv("AUTO_REALTIME_DRIVER", "true")
    assert realtime_driver_enabled() is True

    monkeypatch.setenv("APP_RUNTIME_ROLE", "web")
    assert realtime_driver_enabled() is False


def test_vercel_can_never_start_persistent_driver(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("APP_RUNTIME_ROLE", "worker")
    monkeypatch.setenv("AUTO_REALTIME_DRIVER", "true")
    assert realtime_driver_enabled() is False
