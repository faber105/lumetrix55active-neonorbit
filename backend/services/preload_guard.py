from __future__ import annotations

from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, PaperPosition, utcnow
from backend.services import preload_next
from backend.services.session_engine import _active, _event, _load_signal, _update
from backend.services.trade_runtime import update_trade_runtime


async def _candidate(session_id: int) -> dict | None:
    await preload_next.ensure_preload_schema()
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text("SELECT * FROM auto_preload_candidates WHERE session_id=:sid"),
                {"sid": int(session_id)},
            )
        ).mappings().first()
    return dict(row) if row else None


async def _reset_consumed(session_id: int) -> None:
    """A consumed candidate belongs to the previous trade and must never stop the next preload scan."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                UPDATE auto_preload_candidates
                   SET signal_id=NULL,
                       entry_time=NULL,
                       expiry_time=NULL,
                       amount=NULL,
                       payout=NULL,
                       opened_position_id=NULL,
                       status='SEARCHING',
                       updated_at=:now
                 WHERE session_id=:sid AND status='CONSUMED'
            """),
            {"sid": int(session_id), "now": utcnow()},
        )
        await db.commit()


async def _sync_prepared_pair(session: dict, candidate: dict) -> None:
    """The pair shown in UI/logs must come from the exact signal_id that will be executed."""
    signal_id = candidate.get("signal_id")
    if not signal_id:
        return
    signal = await _load_signal(int(signal_id))
    if not signal:
        return
    payout = candidate.get("payout")
    await update_trade_runtime(
        stage="PRE_ANALYSIS_READY" if str(candidate.get("status")) == "PREPARED" else "PRELOAD_WAIT_CLOSE",
        pending_signal_id=int(signal["id"]),
        pair=signal["pair"],
        asset=signal["asset"],
        strategy=signal["strategy"],
        timeframe=signal["timeframe"],
        payout_percent=payout,
        entry_time=signal["entry_time"],
        expiry_time=signal["expiry_time"],
        message=f"Следующий вход зафиксирован: {signal['pair']} {signal['direction']} · signal #{signal['id']}",
    )


async def _announce_profit_preanalysis(session: dict) -> None:
    """Make the 120-second profit-mode preload window visible and auditable without spamming events."""
    if str(session.get("mode")) != "profit" or not session.get("active_position_id"):
        return
    async with AsyncSessionLocal() as db:
        position = await db.get(PaperPosition, int(session["active_position_id"]))
    if position is None or not position.expiry_time:
        return
    expiry = preload_next._to_naive_utc(position.expiry_time)
    remaining = (expiry - utcnow()).total_seconds()
    if not (0 < remaining <= 120):
        return

    candidate = await _candidate(int(session["id"]))
    status = str((candidate or {}).get("status") or "")
    if status in {"PREPARED", "WAIT_CLOSE"}:
        return

    # One event per trade/preanalysis window. The current active position id is stored in payload.
    async with AsyncSessionLocal() as db:
        exists = (
            await db.execute(
                text("""
                    SELECT 1 FROM auto_trade_events
                     WHERE session_id=:sid AND stage='PRE_ANALYSIS_START'
                       AND payload LIKE :needle
                     LIMIT 1
                """),
                {"sid": int(session["id"]), "needle": f'%\"position_id\": {int(session["active_position_id"])}%"},
            )
        ).scalar_one_or_none()
    if not exists:
        message = f"До закрытия {max(0, int(remaining))}с · запускаю поиск следующего 5m входа по рынку"
        await _event(
            int(session["id"]),
            "PRE_ANALYSIS_START",
            message,
            {"position_id": int(session["active_position_id"]), "seconds_to_expiry": round(remaining, 1)},
        )
        await _update(int(session["id"]), last_message=message)


async def preload_cycle() -> dict | None:
    session = await _active()
    if not session or str(session.get("status")) != "ACTIVE":
        return await preload_next.preload_cycle()

    candidate = await _candidate(int(session["id"]))
    if candidate and str(candidate.get("status")) == "CONSUMED":
        await _reset_consumed(int(session["id"]))
        candidate = await _candidate(int(session["id"]))

    if candidate and str(candidate.get("status")) in {"PREPARED", "WAIT_CLOSE"}:
        await _sync_prepared_pair(session, candidate)

    await _announce_profit_preanalysis(session)
    result = await preload_next.preload_cycle()

    # After the base cycle chooses a candidate, immediately re-read the DB row and
    # publish the exact signal that is actually eligible for execution.
    latest = await _candidate(int(session["id"]))
    if latest and str(latest.get("status")) in {"PREPARED", "WAIT_CLOSE"}:
        await _sync_prepared_pair(session, latest)
    return result
