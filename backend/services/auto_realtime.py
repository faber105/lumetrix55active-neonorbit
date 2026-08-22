from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from backend.services.cpu_guard import adaptive_drive_session_tick

logger = logging.getLogger("alphapulse.auto_realtime")

_driver_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_wake_event: asyncio.Event | None = None
_condition: asyncio.Condition | None = None
_revision = 0
_last_result: dict[str, Any] = {"status": "NOT_STARTED"}
_last_tick_at: float | None = None


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def realtime_driver_enabled() -> bool:
    """Enable the persistent engine only in the explicit worker runtime.

    A deployment flag alone is intentionally insufficient: an accidentally
    copied environment variable must never start a trading loop in Vercel or in
    the public web process.
    """
    if _truthy(os.getenv("VERCEL")):
        return False
    role = str(os.getenv("APP_RUNTIME_ROLE") or "web").strip().lower()
    return role == "worker" and _truthy(os.getenv("AUTO_REALTIME_DRIVER"))


def _change_condition() -> asyncio.Condition:
    global _condition
    if _condition is None:
        _condition = asyncio.Condition()
    return _condition


async def _announce_change() -> None:
    global _revision
    condition = _change_condition()
    async with condition:
        _revision += 1
        condition.notify_all()


async def notify_auto_change(*, wake_driver: bool = False) -> None:
    """Wake the persistent engine after a user action and notify UI streams."""
    if wake_driver and _wake_event is not None:
        _wake_event.set()
    await _announce_change()


async def wait_for_auto_change(revision: int, timeout: float = 0.35) -> int:
    """Wait until the engine completes another broker/session iteration."""
    condition = _change_condition()
    if _revision != revision:
        return _revision
    try:
        async with condition:
            await asyncio.wait_for(
                condition.wait_for(lambda: _revision != revision),
                timeout=max(0.05, float(timeout)),
            )
    except asyncio.TimeoutError:
        pass
    return _revision


def current_revision() -> int:
    return _revision


def driver_health() -> dict[str, Any]:
    task = _driver_task
    return {
        "enabled": realtime_driver_enabled(),
        "running": bool(task and not task.done()),
        "last_tick_at": _last_tick_at,
        "last_result": dict(_last_result),
        "revision": _revision,
    }


def _next_delay(result: dict[str, Any], elapsed: float) -> float:
    """Keep time-sensitive stages hot without repeating full market scans.

    adaptive_drive_session_tick already throttles expensive analysis by
    timeframe. Calling it frequently here therefore speeds up entry/settlement
    and state transitions without reloading every OTC history on every pass.
    """
    status = str(result.get("status") or "").upper()
    cpu_mode = str(result.get("cpu_mode") or "").lower()

    if status in {"IDLE", "NOT_STARTED"}:
        target = 3.0
    elif status in {"STANDBY", "LEASE_LOST"}:
        target = 0.5
    elif status in {"ERROR", "WAIT_MARKET", "FAILED"}:
        target = 0.45
    elif (
        "exact-entry" in cpu_mode
        or "position-watch" in cpu_mode
        or status in {
            "OPEN",
            "OPENING",
            "WAIT_ENTRY",
            "SCHEDULED",
            "PREPARED",
            "WAIT_CLOSE",
            "PRELOAD_RETRY",
        }
    ):
        target = 0.08
    else:
        target = 0.18

    return max(0.02, target - max(0.0, elapsed))


async def _drive_worker_iteration(account_id: int) -> dict[str, Any]:
    """Run one worker iteration only while this process owns the account lease.

    The database lease is the final authority for broker ownership. A worker that
    loses Neon connectivity long enough for its lease to expire must stop all AUTO
    broker/session work immediately; otherwise an already-promoted standby worker
    could trade the same account concurrently.
    """
    if account_id <= 0:
        return {"status": "ERROR", "error": "WORKER_ACCOUNT_ID_MISSING"}

    from backend.services.worker_protocol import owns_lease, process_one_command

    if not await owns_lease(account_id):
        return {"status": "STANDBY", "reason": "LEASE_NOT_OWNED"}

    command = await process_one_command(account_id)
    if command is not None:
        return dict(command)

    # Re-check immediately before broker/session driving. The lease can be lost
    # between command polling and the trading tick during failover.
    if not await owns_lease(account_id):
        return {"status": "LEASE_LOST", "reason": "LEASE_NOT_OWNED"}

    value = await adaptive_drive_session_tick()
    return dict(value) if isinstance(value, dict) else {"status": "UNKNOWN"}


async def _driver_loop() -> None:
    global _last_result, _last_tick_at
    assert _stop_event is not None
    logger.info("Persistent AUTO realtime driver started")
    while not _stop_event.is_set():
        started = time.monotonic()
        result: dict[str, Any]
        try:
            account_id = int(os.getenv("WORKER_ACCOUNT_ID") or 0)
            result = await _drive_worker_iteration(account_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Realtime AUTO iteration recovered after %s", type(exc).__name__)
            result = {"status": "ERROR", "error": type(exc).__name__}

        _last_result = result
        _last_tick_at = time.time()
        await _announce_change()

        delay = _next_delay(result, time.monotonic() - started)
        if _wake_event is None:
            try:
                await asyncio.wait_for(_stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
        else:
            try:
                await asyncio.wait_for(_wake_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            finally:
                _wake_event.clear()
    logger.info("Persistent AUTO realtime driver stopped")


async def start_auto_realtime_driver() -> bool:
    global _driver_task, _stop_event, _wake_event
    if not realtime_driver_enabled():
        logger.info("Persistent AUTO driver disabled for this runtime")
        return False
    if _driver_task is not None and not _driver_task.done():
        return True
    _stop_event = asyncio.Event()
    _wake_event = asyncio.Event()
    _driver_task = asyncio.create_task(_driver_loop(), name="alphapulse-auto-realtime")
    return True


async def stop_auto_realtime_driver() -> None:
    global _driver_task, _stop_event, _wake_event
    task = _driver_task
    if task is None:
        return
    if _stop_event is not None:
        _stop_event.set()
    if _wake_event is not None:
        _wake_event.set()
    try:
        await asyncio.wait_for(task, timeout=5)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        _driver_task = None
        _stop_event = None
        _wake_event = None
