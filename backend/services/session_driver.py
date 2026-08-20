from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, utcnow
from backend.services.auto_scan_scope import set_auto_scan_scope
from backend.services.auto_trade import MIN_AUTO_PAYOUT, get_demo_account_snapshot
from backend.services.control import admin_id
from backend.services.pocketoption_otc import OTC_ASSETS
from backend.services.preload_next import preload_cycle
from backend.services.session_engine import session_tick

# The Mini App polls active AUTO state roughly every 750 ms. Keep the DB claim
# interval aligned with that cadence so the very first poll after Pocket reports
# a CLOSED deal can settle it and immediately continue into the next market scan.
TICK_MIN_INTERVAL_SECONDS = 0.7


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


async def drive_session_tick(*, min_interval_seconds: float = TICK_MIN_INTERVAL_SECONDS) -> dict:
    session_id = await claim_session_tick(min_interval_seconds)
    if session_id is None:
        return {"status": "THROTTLED_OR_IDLE"}

    universe = await _refresh_live_otc_universe()
    preload = None
    try:
        preload = await preload_cycle()
    except Exception as exc:
        preload = {"status": "PRELOAD_ERROR", "error": type(exc).__name__, "block": False}

    if preload and preload.get("block"):
        result = dict(preload)
    else:
        result = await session_tick()

    if isinstance(result, dict):
        result.setdefault("session_id", session_id)
        result.setdefault("driver", "atomic-db-throttle")
        result.setdefault("otc_universe", universe)
        if preload:
            result.setdefault("preload", preload)
    return result
