from __future__ import annotations

from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, utcnow
from backend.services.pocketoption_otc import OTC_ASSETS
from backend.services.preload_guard import preload_cycle as _base_preload_cycle
from backend.services.session_engine import _active, _event
from backend.services.trade_runtime import update_trade_runtime

_PROGRESS_INTERVAL_SECONDS = 15.0


async def _should_log(session_id: int) -> bool:
    async with AsyncSessionLocal() as db:
        created_at = (
            await db.execute(
                text("""
                    SELECT created_at
                      FROM auto_trade_events
                     WHERE session_id=:sid AND stage='PRE_ANALYSIS_SCAN'
                     ORDER BY id DESC
                     LIMIT 1
                """),
                {"sid": int(session_id)},
            )
        ).scalar_one_or_none()
    if created_at is None:
        return True
    return (utcnow() - created_at).total_seconds() >= _PROGRESS_INTERVAL_SECONDS


async def preload_cycle() -> dict | None:
    result = await _base_preload_cycle()
    if not isinstance(result, dict) or str(result.get("status")) != "PRELOAD_ACTIVE":
        return result

    preparation = result.get("preparation") or {}
    if str(preparation.get("status")) != "SEARCHING":
        return result

    session = await _active()
    if not session or str(session.get("mode")) != "profit" or not session.get("active_position_id"):
        return result
    if not await _should_log(int(session["id"])):
        return result

    remaining = max(0, int(float(preparation.get("seconds_to_expiry") or 0)))
    scanned = len(OTC_ASSETS)
    message = f"Преданализ: проход {scanned} OTC завершён · подтверждённого сетапа пока нет · до закрытия {remaining}с"
    await _event(
        int(session["id"]),
        "PRE_ANALYSIS_SCAN",
        message,
        {"scanned_count": scanned, "seconds_to_expiry": remaining, "confirmed": 0},
    )
    await update_trade_runtime(
        stage="PRE_ANALYSIS_SCAN",
        scanned_count=scanned,
        message=message,
    )
    return result
