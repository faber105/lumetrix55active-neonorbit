from __future__ import annotations

import asyncio

from backend.services import auto_realtime, worker_protocol


def run(awaitable):
    return asyncio.run(awaitable)


def test_worker_without_lease_never_drives_auto(monkeypatch):
    calls = {"drive": 0, "command": 0}

    async def no_lease(_account_id):
        return False

    async def command(_account_id):
        calls["command"] += 1
        return None

    async def drive():
        calls["drive"] += 1
        return {"status": "OPEN"}

    monkeypatch.setattr(worker_protocol, "owns_lease", no_lease)
    monkeypatch.setattr(worker_protocol, "process_one_command", command)
    monkeypatch.setattr(auto_realtime, "adaptive_drive_session_tick", drive)

    result = run(auto_realtime._drive_worker_iteration(7))

    assert result == {"status": "STANDBY", "reason": "LEASE_NOT_OWNED"}
    assert calls == {"drive": 0, "command": 0}


def test_lease_is_rechecked_before_broker_tick(monkeypatch):
    calls = {"lease": 0, "drive": 0}

    async def lease_then_lost(_account_id):
        calls["lease"] += 1
        return calls["lease"] == 1

    async def command(_account_id):
        return None

    async def drive():
        calls["drive"] += 1
        return {"status": "OPEN"}

    monkeypatch.setattr(worker_protocol, "owns_lease", lease_then_lost)
    monkeypatch.setattr(worker_protocol, "process_one_command", command)
    monkeypatch.setattr(auto_realtime, "adaptive_drive_session_tick", drive)

    result = run(auto_realtime._drive_worker_iteration(7))

    assert result == {"status": "LEASE_LOST", "reason": "LEASE_NOT_OWNED"}
    assert calls["lease"] == 2
    assert calls["drive"] == 0


def test_worker_with_lease_can_drive_auto(monkeypatch):
    calls = {"drive": 0}

    async def owns(_account_id):
        return True

    async def command(_account_id):
        return None

    async def drive():
        calls["drive"] += 1
        return {"status": "SCANNING"}

    monkeypatch.setattr(worker_protocol, "owns_lease", owns)
    monkeypatch.setattr(worker_protocol, "process_one_command", command)
    monkeypatch.setattr(auto_realtime, "adaptive_drive_session_tick", drive)

    result = run(auto_realtime._drive_worker_iteration(7))

    assert result["status"] == "SCANNING"
    assert calls["drive"] == 1
