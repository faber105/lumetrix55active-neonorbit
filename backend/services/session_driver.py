from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, utcnow
from backend.services.auto_scan_scope import set_auto_scan_scope
from backend.services.auto_trade import MIN_AUTO_PAYOUT, get_demo_account_snapshot
from backend.services.control import admin_id
from backend.services.pocketoption_otc import OTC_ASSETS
from backend.services.multi_strategy import preload_cycle, session_tick

# This remains only a lightweight cadence throttle. Real mutual exclusion is
# provided by the PostgreSQL advisory lock held for the whole trading tick.
TICK_MIN_INTERVAL_SECONDS = 0.7
_ADVISORY_LOCK_BASE = 918_420_000_000


def _display_asset(asset: str) -> str:
    raw = str(asset or "").replace("_otc", "").replace("-OTC", "").upper()
    if re.fullmatch(r"[A-Z]{6}", raw):
        return f"{raw[:3]}/{raw[3:]} OTC"
    return f"{raw} OTC"


async def _refresh_live_otc_universe() -> dict:
    try:
        snapshot = await get_demo_account_snapshot(max_age=1.0)
    except Exception:
        set_auto_scan_scope([], len(OTC_ASSETS))
        return {"discovered": len(OTC_ASSETS), "eligible": None}

    payouts = snapshot.get("payouts", {}) or {}
    available = snapshot.get("available_assets", {}) or {}
    keys = set(payouts) | set(available)
    for asset in keys:
        if str(asset).lower().endswith("_otc"):
            OTC_ASSETS.setdefault(str(asset), _display_asset(str(asset)))

    eligible_assets: list[str] = []
    for asset in OTC_ASSETS:
        try:
            payout = float(payouts.get(asset))
        except (TypeError, ValueError):
            continue
        if payout >= MIN_AUTO_PAYOUT and available.get(asset, True) is not False:
            eligible_assets.append(asset)

    set_auto_scan_scope(eligible_assets, len(OTC_ASSETS))
    return {
        "discovered": len(OTC_ASSETS),
        "eligible": len(eligible_assets),
        "eligible_assets": eligible_assets,
    }


async def claim_session_tick(min_interval_seconds: float = TICK_MIN_INTERVAL_SECONDS) -> int | None:
    """Atomically claim the active session for one scanner iteration."""
    tid = admin_id()
    if tid <= 0:
        return None
    now = utcnow()
    cutoff = now - timedelta(seconds=max(0.5, float(min_interval_seconds)))
    async with AsyncSessionLocal() as db:
        claimed = (
            await db.execute(
                text(
                    """
                    UPDATE auto_trade_sessions
                       SET updated_at = :now
                     WHERE id = (
                         SELECT id
                           FROM auto_trade_sessions
                          WHERE telegram_id = :tid
                            AND status = 'ACTIVE'
                          ORDER BY id DESC
                          LIMIT 1
                     )
                       AND updated_at <= :cutoff
                    RETURNING id
                    """
                ),
                {"tid": tid, "now": now, "cutoff": cutoff},
            )
        ).scalar_one_or_none()
        await db.commit()
    return int(claimed) if claimed is not None else None


async def _reset_preload_after_open(session_id: int) -> None:
    """The next open trade must start a fresh preload search later in its lifetime."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("""
                UPDATE auto_preload_candidates
                   SET signal_id=NULL,
                       entry_time=NULL,
                       expiry_time=NULL,
                       amount=NULL,
                       payout=NULL,
                       opened_position_id=NULL,
                       status='SEARCHING',
                       updated_at=:now
                 WHERE session_id=:sid
            """), {"sid": int(session_id), "now": utcnow()})
            await db.commit()
    except Exception:
        pass


async def drive_session_tick(*, min_interval_seconds: float = TICK_MIN_INTERVAL_SECONDS) -> dict:
    """Run exactly one AUTO engine iteration at a time across all Vercel workers.

    The previous timestamp-only throttle allowed a second serverless invocation to
    start while the first market scan was still running. That stale invocation
    could emit SIGNAL_FOUND after another invocation had already opened a trade.
    A PostgreSQL advisory lock is connection-scoped, so it safely serializes ticks
    even when they execute in different Vercel instances.
    """
    tid = admin_id()
    if tid <= 0:
        return {"status": "IDLE"}

    lock_key = _ADVISORY_LOCK_BASE + int(tid)
    async with AsyncSessionLocal() as lock_db:
        locked = bool((await lock_db.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": lock_key},
        )).scalar())
        if not locked:
            return {"status": "BUSY", "driver": "postgres-advisory-lock"}

        try:
            session_id = await claim_session_tick(min_interval_seconds)
            if session_id is None:
                return {"status": "THROTTLED_OR_IDLE", "driver": "postgres-advisory-lock"}

            universe = await _refresh_live_otc_universe()
            preload = None
            try:
                preload = await preload_cycle()
            except Exception as exc:
                preload = {"status": "PRELOAD_ERROR", "error": type(exc).__name__, "block": False}

            if preload and preload.get("block"):
                result = dict(preload)
                if preload.get("status") == "OPEN" and preload.get("preloaded"):
                    await _reset_preload_after_open(session_id)
            else:
                result = await session_tick()

            if isinstance(result, dict):
                result.setdefault("session_id", session_id)
                result.setdefault("driver", "postgres-advisory-lock")
                result.setdefault("otc_universe", universe)
                if preload:
                    result.setdefault("preload", preload)
            return result
        finally:
            try:
                await lock_db.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": lock_key},
                )
                await lock_db.commit()
            except Exception:
                pass
