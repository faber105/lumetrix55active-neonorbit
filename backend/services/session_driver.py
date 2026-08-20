from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, utcnow
from backend.services.auto_trade import MIN_AUTO_PAYOUT, get_demo_account_snapshot
from backend.services.control import admin_id
from backend.services.pocketoption_otc import OTC_ASSETS
from backend.services.session_engine import session_tick

TICK_MIN_INTERVAL_SECONDS = 2.0


def _display_asset(asset: str) -> str:
    raw = str(asset or "").replace("_otc", "").replace("-OTC", "").upper()
    if re.fullmatch(r"[A-Z]{6}", raw):
        return f"{raw[:3]}/{raw[3:]} OTC"
    return f"{raw} OTC"


async def _refresh_live_otc_universe() -> dict:
    """Populate the scanner from Pocket's current DEMO asset/payout snapshot.

    The original build shipped with ten hard-coded pairs. Pocket exposes a wider
    OTC universe in its live account snapshot, so register every currently
    visible *_otc symbol before each scanner cycle. This keeps the static ten as
    a fallback when Pocket temporarily returns no asset list.
    """
    try:
        snapshot = await get_demo_account_snapshot(max_age=2.0)
    except Exception:
        return {"discovered": len(OTC_ASSETS), "eligible": None}

    payouts = snapshot.get("payouts", {}) or {}
    available = snapshot.get("available_assets", {}) or {}
    keys = set(payouts) | set(available)
    for asset in keys:
        if str(asset).lower().endswith("_otc"):
            OTC_ASSETS.setdefault(str(asset), _display_asset(str(asset)))

    eligible = 0
    for asset in OTC_ASSETS:
        try:
            payout = float(payouts.get(asset))
        except (TypeError, ValueError):
            continue
        if payout >= MIN_AUTO_PAYOUT and available.get(asset, True) is not False:
            eligible += 1
    return {"discovered": len(OTC_ASSETS), "eligible": eligible}


async def claim_session_tick(min_interval_seconds: float = TICK_MIN_INTERVAL_SECONDS) -> int | None:
    """Atomically claim the active session for one scanner iteration."""
    tid = admin_id()
    if tid <= 0:
        return None
    now = utcnow()
    cutoff = now - timedelta(seconds=max(1.0, float(min_interval_seconds)))
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
    result = await session_tick()
    if isinstance(result, dict):
        result.setdefault("session_id", session_id)
        result.setdefault("driver", "atomic-db-throttle")
        result.setdefault("otc_universe", universe)
    return result
