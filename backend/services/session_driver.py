from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, utcnow
from backend.services.auto_scan_scope import set_auto_scan_scope
from backend.services.auto_trade import (
    MIN_AUTO_PAYOUT,
    get_demo_account_snapshot,
    process_pending_auto_trade,
)
from backend.services.control import admin_id
from backend.services.pocketoption_otc import OTC_ASSETS
from backend.services.multi_strategy import preload_cycle, session_tick
from backend.services.preload_next import _candidate as _preload_candidate
from backend.services.session_engine import _active, _load_signal, _register_open, _update
from backend.services.trade_runtime import get_trade_runtime

# This remains only a lightweight cadence throttle. Real mutual exclusion is
# provided by the PostgreSQL advisory lock held for the whole trading tick.
TICK_MIN_INTERVAL_SECONDS = 0.7
_ADVISORY_LOCK_BASE = 918_420_000_000
_PRELOAD_PRIORITY_STATES = {"PREPARED", "WAIT_CLOSE"}


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


async def _drive_pending_entry(session: dict) -> dict | None:
    """Handle a scheduled entry before any market refresh or reconciliation work.

    Exact candle-boundary orders are latency sensitive. Once a signal has already
    been selected, refreshing the whole OTC universe first can consume the entry
    window and incorrectly turn a valid setup into MISSED_ENTRY.
    """
    signal_id = session.get("pending_signal_id")
    if not signal_id or session.get("active_position_id"):
        return None

    pending = await _load_signal(int(signal_id))
    trade = await process_pending_auto_trade()
    status = str(trade.get("status") or "")

    if status == "OPEN" and pending:
        runtime = await get_trade_runtime()
        amount = float(runtime.get("amount") or session.get("base_amount") or 1.0)
        await _register_open(
            session,
            pending,
            trade,
            amount,
            trade.get("payout") or runtime.get("payout_percent"),
        )
        return {"status": "OPEN", "trade": trade, "fast_path": "pending-entry"}

    if status in {"WAIT_ENTRY", "SCHEDULED", "OPENING"}:
        runtime = await get_trade_runtime()
        await _update(
            int(session["id"]),
            stage=status,
            last_message=runtime.get("message") or "Жду точное время входа",
        )
        trade["fast_path"] = "pending-entry"
        return trade

    await _update(
        int(session["id"]),
        pending_signal_id=None,
        stage="SCANNING",
        last_message=f"Вход пропущен: {status or 'UNKNOWN'} · продолжаю анализ всех пар",
    )
    trade["fast_path"] = "pending-entry"
    return trade


async def _preload_is_priority(session_id: int) -> bool:
    try:
        candidate = await _preload_candidate(int(session_id))
    except Exception:
        return False
    return bool(candidate and str(candidate.get("status") or "") in _PRELOAD_PRIORITY_STATES and candidate.get("signal_id"))


async def drive_session_tick(*, min_interval_seconds: float = TICK_MIN_INTERVAL_SECONDS) -> dict:
    """Run exactly one AUTO engine iteration at a time across all Vercel workers.

    Pending and preloaded entries are latency-sensitive and always outrank a new
    full-market scan. A PREPARED/WAIT_CLOSE candidate stays authoritative until
    it is opened or explicitly cancelled by the preload engine.
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

            session = await _active()

            # First priority: an ordinary pending signal that is already waiting
            # for its exact candle boundary.
            if session and session.get("pending_signal_id") and not session.get("active_position_id"):
                pending_result = await _drive_pending_entry(session)
                if pending_result is not None:
                    pending_result.setdefault("session_id", session_id)
                    pending_result.setdefault("driver", "postgres-advisory-lock")
                    return pending_result

            # Second priority: a signal prepared while the previous position was
            # still open. Never let a fresh 114-pair scan overtake it. We also run
            # preload before refreshing the OTC universe to minimize entry latency.
            had_preload_priority = await _preload_is_priority(session_id)
            preload = None
            try:
                preload = await preload_cycle()
            except Exception as exc:
                # If a prepared entry already exists, a transient Pocket/DB error
                # must not drop us into a normal scan. Keep retrying that candidate.
                preload = {
                    "status": "PRELOAD_ERROR",
                    "error": type(exc).__name__,
                    "block": had_preload_priority,
                }

            still_preload_priority = await _preload_is_priority(session_id)
            if still_preload_priority:
                if preload is None:
                    preload = {"status": "PRELOAD_PRIORITY", "block": True}
                elif not preload.get("block"):
                    preload = {**preload, "block": True, "priority": True}

            if preload and preload.get("block"):
                result = dict(preload)
                if preload.get("status") == "OPEN" and preload.get("preloaded"):
                    await _reset_preload_after_open(session_id)
                result.setdefault("session_id", session_id)
                result.setdefault("driver", "postgres-advisory-lock")
                return result

            # Only when no prepared entry is outstanding may the normal engine
            # refresh the market universe and search for a brand-new setup.
            universe = await _refresh_live_otc_universe()
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
