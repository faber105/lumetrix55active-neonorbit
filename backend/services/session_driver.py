from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, utcnow
from backend.services.control import admin_id
from backend.services.session_engine import session_tick

TICK_MIN_INTERVAL_SECONDS = 3.0


async def claim_session_tick(min_interval_seconds: float = TICK_MIN_INTERVAL_SECONDS) -> int | None:
    """Atomically claim the active session for one scanner iteration.

    GitHub Actions and the Mini App can both drive the same AUTO session.  The
    conditional UPDATE is the cross-instance throttle: only one caller gets a
    session id during the interval, so a second Vercel instance cannot process
    the same market setup at the same time.
    """
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
    result = await session_tick()
    if isinstance(result, dict):
        result.setdefault("session_id", session_id)
        result.setdefault("driver", "atomic-db-throttle")
    return result
