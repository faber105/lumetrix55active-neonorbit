from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, utcnow
from backend.services.multi_strategy import session_tick
from backend.services.preload_next import _candidate as _preload_candidate
from backend.services.session_driver import drive_session_tick as _full_drive_session_tick
from backend.services.session_engine import _active

_SCHEMA_READY = False

# Fresh full-market analysis cadence. 15s keeps a very fast cadence; slower
# timeframes stop burning Vercel CPU by rescanning 100+ OTC pairs every few sec.
_ANALYSIS_INTERVAL = {
    "15s": 3.0,
    "1m": 10.0,
    "3m": 24.0,
    "5m": 40.0,
}

# Lightweight polling cadence while an order/position is time-sensitive.
_POLL_INTERVAL = {
    "15s": 1.0,
    "1m": 3.0,
    "3m": 6.0,
    "5m": 8.0,
}

_PRELOAD_PRIORITY = {"PREPARED", "WAIT_CLOSE"}
_PENDING_FAST = {"WAIT_ENTRY", "SCHEDULED", "OPENING", "PRELOAD_PRIORITY", "PREPARED", "WAIT_CLOSE"}


async def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS auto_scan_runtime (
                session_id BIGINT PRIMARY KEY,
                last_full_scan_at TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        await db.commit()
    _SCHEMA_READY = True


async def _claim_full_scan(session_id: int, interval_seconds: float) -> bool:
    await _ensure_schema()
    now = utcnow()
    cutoff = now - timedelta(seconds=max(1.0, float(interval_seconds)))
    async with AsyncSessionLocal() as db:
        claimed = (
            await db.execute(
                text("""
                    INSERT INTO auto_scan_runtime(session_id,last_full_scan_at,updated_at)
                    VALUES (:sid,:now,:now)
                    ON CONFLICT (session_id) DO UPDATE
                       SET last_full_scan_at=:now,
                           updated_at=:now
                     WHERE auto_scan_runtime.last_full_scan_at IS NULL
                        OR auto_scan_runtime.last_full_scan_at <= :cutoff
                    RETURNING session_id
                """),
                {"sid": int(session_id), "now": now, "cutoff": cutoff},
            )
        ).scalar_one_or_none()
        await db.commit()
    return claimed is not None


async def _has_priority_preload(session_id: int) -> bool:
    try:
        candidate = await _preload_candidate(int(session_id))
    except Exception:
        return False
    return bool(
        candidate
        and candidate.get("signal_id")
        and str(candidate.get("status") or "") in _PRELOAD_PRIORITY
    )


def _poll_for(timeframe: str, status: str | None = None) -> float:
    if str(status or "") in _PENDING_FAST:
        return 1.0
    return float(_POLL_INTERVAL.get(str(timeframe or "5m"), 8.0))


async def adaptive_drive_session_tick() -> dict:
    """Drive AUTO without repeating expensive full-market work unnecessarily.

    Exact pending/preloaded entries always use the original full driver. While a
    position is already open we reconcile/settle it directly, skipping the
    expensive OTC-universe refresh. A fresh 100+ pair analysis is performed only
    at a timeframe-aware cadence and still uses live Pocket candles each time.
    """
    session = await _active()
    if not session:
        return {"status": "IDLE", "poll_after": 20.0, "cpu_mode": "idle"}

    sid = int(session["id"])
    timeframe = str(session.get("timeframe") or "5m")

    # Exact entry path must never be throttled.
    if session.get("pending_signal_id") or await _has_priority_preload(sid):
        result = await _full_drive_session_tick(min_interval_seconds=0.5)
        if isinstance(result, dict):
            result.setdefault("poll_after", _poll_for(timeframe, result.get("status")))
            result.setdefault("cpu_mode", "exact-entry")
        return result

    # An already-open deal does not need another whole-market refresh. The
    # session engine reconciles broker truth and, once closed, immediately moves
    # on to the next analysis cycle.
    if session.get("active_position_id"):
        result = await _full_drive_session_tick(min_interval_seconds=0.5)
        if isinstance(result, dict):
            result.setdefault("poll_after", _poll_for(timeframe, result.get("status")))
            result.setdefault("cpu_mode", "position-watch-locked")
        return result

    interval = float(_ANALYSIS_INTERVAL.get(timeframe, 40.0))
    if not await _claim_full_scan(sid, interval):
        return {
            "status": "ANALYSIS_WAIT",
            "session_id": sid,
            "timeframe": timeframe,
            "poll_after": _poll_for(timeframe),
            "cpu_mode": "cadence-throttle",
        }

    result = await _full_drive_session_tick(min_interval_seconds=0.5)
    if isinstance(result, dict):
        result.setdefault("timeframe", timeframe)
        result.setdefault("poll_after", _poll_for(timeframe, result.get("status")))
        result.setdefault("cpu_mode", "fresh-full-analysis")
    return result
