from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from backend.models.db_models import (
    AsyncSessionLocal,
    PaperPosition,
    Signal,
    SignalDirection,
    SignalResult,
    utcnow,
)
from backend.services.pocketoption_otc import MarketDataUnavailable, market_data


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def serialize_position(position: PaperPosition) -> dict:
    return {
        "id": position.id,
        "telegram_id": int(position.telegram_id),
        "signal_id": position.signal_id,
        "source": position.source,
        "pair": position.pair,
        "asset": position.asset,
        "timeframe": position.timeframe,
        "strategy": position.strategy,
        "direction": position.direction.value,
        "status": position.status,
        "entry_price": position.entry_price,
        "close_price": position.close_price,
        "entry_time": position.entry_time.isoformat() + "Z",
        "expiry_time": position.expiry_time.isoformat() + "Z",
        "result": position.result.value,
        "created_at": position.created_at.isoformat() + "Z",
        "closed_at": position.closed_at.isoformat() + "Z" if position.closed_at else None,
    }


async def take_signal(telegram_id: int, signal_id: int) -> PaperPosition:
    current = _now()
    async with AsyncSessionLocal() as db:
        signal = await db.get(Signal, signal_id)
        if signal is None:
            raise ValueError("Signal not found")
        if signal.expiry_time <= current:
            raise ValueError("Signal has already expired")
        existing = (
            await db.execute(
                select(PaperPosition).where(
                    PaperPosition.telegram_id == telegram_id,
                    PaperPosition.signal_id == signal_id,
                    PaperPosition.status == "OPEN",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    price = await market_data.latest_price(signal.asset)
    async with AsyncSessionLocal() as db:
        signal = await db.get(Signal, signal_id)
        if signal is None:
            raise ValueError("Signal not found")
        current = _now()
        if signal.expiry_time <= current:
            raise ValueError("Signal has already expired")
        position = PaperPosition(
            telegram_id=telegram_id,
            signal_id=signal.id,
            source="vip" if signal.is_vip else "regular",
            pair=signal.pair,
            asset=signal.asset,
            timeframe=signal.timeframe,
            strategy=signal.strategy,
            direction=signal.direction,
            status="OPEN",
            entry_price=float(price),
            entry_time=current,
            expiry_time=signal.expiry_time,
            result=SignalResult.PENDING,
        )
        db.add(position)
        await db.commit()
        await db.refresh(position)
        return position


async def reconcile_positions(limit: int = 100) -> dict:
    current = _now()
    async with AsyncSessionLocal() as db:
        ids = list(
            (
                await db.execute(
                    select(PaperPosition.id)
                    .where(
                        PaperPosition.status == "OPEN",
                        PaperPosition.expiry_time <= current,
                    )
                    .order_by(PaperPosition.expiry_time)
                    .limit(limit)
                )
            ).scalars().all()
        )

    closed = 0
    errors: list[dict] = []
    for position_id in ids:
        try:
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    position = (
                        await db.execute(
                            select(PaperPosition)
                            .where(PaperPosition.id == position_id, PaperPosition.status == "OPEN")
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one_or_none()
                    if position is None:
                        continue
                    close_price = await market_data.boundary_price(position.asset, position.expiry_time)
                    position.close_price = float(close_price)
                    delta = float(position.close_price) - float(position.entry_price)
                    epsilon = max(abs(float(position.entry_price)) * 1e-10, 1e-10)
                    if abs(delta) <= epsilon:
                        position.result = SignalResult.DRAW
                    elif position.direction == SignalDirection.BUY:
                        position.result = SignalResult.WIN if delta > 0 else SignalResult.LOSS
                    else:
                        position.result = SignalResult.WIN if delta < 0 else SignalResult.LOSS
                    position.status = "CLOSED"
                    position.closed_at = utcnow()
                    closed += 1
        except MarketDataUnavailable:
            errors.append({"id": position_id, "type": "market_data"})
        except Exception as exc:
            errors.append({"id": position_id, "type": type(exc).__name__})
    return {"closed": closed, "checked": len(ids), "errors": errors}
