from __future__ import annotations

import json
import logging
from datetime import timezone

from sqlalchemy import desc, select, text

from backend.models.db_models import (
    AsyncSessionLocal,
    PaperPosition,
    Signal,
    SignalResult,
    TradeExecution,
    utcnow,
)
from backend.services.pocket_demo_trading import DirectDemoTradingClient
from backend.services.pocketoption_otc import market_data

logger = logging.getLogger("alphapulse.execution_recovery")


def _naive(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None):
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(tzinfo=None) if hasattr(value, "replace") else value


async def _runtime_payout() -> float:
    try:
        async with AsyncSessionLocal() as db:
            raw = (
                await db.execute(
                    text("SELECT payload FROM ml_state WHERE strategy='__auto_trade_runtime__'")
                )
            ).scalar_one_or_none()
        payload = json.loads(raw or "{}") if isinstance(raw, str) else (raw or {})
        return float(payload.get("payout_percent") or 92.0)
    except Exception:
        return 92.0


async def _attach_recovered_position(execution: TradeExecution, signal: Signal, recovered, recovered_entry_price: float | None = None) -> dict:
    broker_order_id = str(recovered.order_id)
    placed_at = _naive(recovered.placed_at) or utcnow()
    expires_at = _naive(recovered.expires_at) or signal.expiry_time
    entry_price = recovered_entry_price or signal.entry_price or signal.analysis_price
    if entry_price is None:
        # Do not invent a price for a real broker execution. If the broker deal
        # does not expose one, use a live quote only as a last observable value.
        try:
            entry_price = await market_data.latest_price(signal.asset)
        except Exception:
            return {"status": "UNRESOLVED_PRICE", "execution_id": int(execution.id)}
    payout = await _runtime_payout()

    async with AsyncSessionLocal() as db:
        async with db.begin():
            locked_execution = (
                await db.execute(
                    select(TradeExecution)
                    .where(TradeExecution.id == execution.id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked_execution is None:
                return {"status": "MISSING"}
            if locked_execution.position_id:
                return {
                    "status": "ALREADY_ATTACHED",
                    "position_id": int(locked_execution.position_id),
                    "broker_order_id": locked_execution.broker_order_id,
                }

            existing = (
                await db.execute(
                    select(PaperPosition)
                    .where(
                        PaperPosition.telegram_id == execution.telegram_id,
                        PaperPosition.signal_id == signal.id,
                        PaperPosition.source == "auto",
                    )
                    .order_by(desc(PaperPosition.id))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = PaperPosition(
                    telegram_id=execution.telegram_id,
                    signal_id=signal.id,
                    source="auto",
                    pair=signal.pair,
                    asset=signal.asset,
                    timeframe=signal.timeframe,
                    strategy=signal.strategy,
                    direction=signal.direction,
                    status="OPEN",
                    entry_price=float(entry_price),
                    entry_time=placed_at,
                    expiry_time=expires_at,
                    result=SignalResult.PENDING,
                )
                db.add(existing)
                await db.flush()

            locked_execution.position_id = existing.id
            locked_execution.broker_order_id = broker_order_id
            locked_execution.status = "OPEN"
            locked_execution.error = None

            session = (
                await db.execute(
                    text("""SELECT * FROM auto_trade_sessions
                        WHERE telegram_id=:tid AND status='ACTIVE'
                        ORDER BY id DESC LIMIT 1 FOR UPDATE"""),
                    {"tid": int(execution.telegram_id)},
                )
            ).mappings().first()
            if session and not session.get("active_position_id"):
                level = int(session.get("current_level") or 0)
                series = int(session.get("wins") or 0) + int(session.get("failed_series") or 0) + 1
                idem = f"session:{int(session['id'])}:series:{series}:level:{level}:signal:{int(signal.id)}"
                await db.execute(
                    text("""INSERT INTO auto_trade_legs
                        (session_id,series_no,martingale_level,signal_id,position_id,broker_order_id,idempotency_key,
                         pair,asset,direction,amount,payout,result,opened_at)
                        VALUES (:sid,:series,:level,:signal,:position,:broker,:idem,:pair,:asset,:direction,
                                :amount,:payout,'PENDING',:opened)
                        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING"""),
                    {
                        "sid": int(session["id"]),
                        "series": series,
                        "level": level,
                        "signal": int(signal.id),
                        "position": int(existing.id),
                        "broker": broker_order_id,
                        "idem": idem,
                        "pair": signal.pair,
                        "asset": signal.asset,
                        "direction": signal.direction.value,
                        "amount": float(execution.amount),
                        "payout": payout,
                        "opened": placed_at,
                    },
                )
                await db.execute(
                    text("""UPDATE auto_trade_sessions SET
                        stage='OPEN',active_position_id=:position,pending_signal_id=NULL,
                        total_legs=total_legs+1,last_message=:message,updated_at=:now,version=version+1
                        WHERE id=:sid AND status='ACTIVE' AND active_position_id IS NULL"""),
                    {
                        "position": int(existing.id),
                        "message": "Pocket сделка восстановлена после неопределённого ответа · LIVE отслеживание продолжено",
                        "now": utcnow(),
                        "sid": int(session["id"]),
                    },
                )

    return {
        "status": "RECOVERED",
        "execution_id": int(execution.id),
        "position_id": int(existing.id),
        "broker_order_id": broker_order_id,
    }


async def reconcile_uncertain_executions(limit: int = 10) -> dict:
    """Resolve UNKNOWN/EXECUTING broker sends without ever resending an order."""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(TradeExecution)
                .where(TradeExecution.status.in_(["UNKNOWN", "EXECUTING"]))
                .order_by(TradeExecution.created_at)
                .limit(max(1, min(int(limit), 50)))
            )
        ).scalars().all()

    if not rows:
        return {"checked": 0, "recovered": 0, "unresolved": 0}

    await market_data._refresh_private_ssid()
    if not market_data.configured:
        return {"checked": len(rows), "recovered": 0, "unresolved": len(rows), "reason": "POCKET_NOT_CONFIGURED"}

    client = DirectDemoTradingClient(market_data.ssid)
    recovered_count = 0
    unresolved = 0
    try:
        for execution in rows:
            async with AsyncSessionLocal() as db:
                signal = await db.get(Signal, int(execution.signal_id))
            if signal is None:
                unresolved += 1
                continue
            request_id = f"execution:{int(execution.id)}"
            try:
                recovered = await client.find_order(request_id)
            except Exception as exc:
                logger.warning("Uncertain execution lookup failed id=%s: %s", execution.id, type(exc).__name__)
                unresolved += 1
                continue
            if recovered is None:
                unresolved += 1
                continue
            attached = await _attach_recovered_position(execution, signal, recovered, client.last_open_price)
            if attached.get("status") in {"RECOVERED", "ALREADY_ATTACHED"}:
                recovered_count += 1
            else:
                unresolved += 1
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return {"checked": len(rows), "recovered": recovered_count, "unresolved": unresolved}
