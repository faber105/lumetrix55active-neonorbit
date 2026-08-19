from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select

from backend.models.db_models import (
    AsyncSessionLocal,
    PaperPosition,
    Signal,
    SignalDirection,
    SignalResult,
    utcnow,
)
from backend.services.pocketoption_otc import MarketDataUnavailable, market_data

logger = logging.getLogger("alphapulse.positions")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            ts = float(value)
            if ts > 10_000_000_000:
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        else:
            text = str(value).strip()
            try:
                ts = float(text)
                if ts > 10_000_000_000:
                    ts /= 1000.0
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            except ValueError:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.replace(tzinfo=None)
    except Exception:
        return None


def _deal_direction(deal: dict) -> SignalDirection | None:
    raw = deal.get("action", deal.get("direction"))
    if raw is not None:
        text = str(raw).strip().lower()
        if text in {"call", "buy", "up", "higher"}:
            return SignalDirection.BUY
        if text in {"put", "sell", "down", "lower"}:
            return SignalDirection.SELL
    # Pocket's protocol serializes Action::Call as command 0 and Put as 1.
    command = deal.get("command")
    try:
        command = int(command)
        if command == 0:
            return SignalDirection.BUY
        if command == 1:
            return SignalDirection.SELL
    except Exception:
        pass
    return None


def _deal_asset(deal: dict) -> str | None:
    value = deal.get("asset", deal.get("symbol"))
    return str(value).strip() if value else None


def _deal_open_time(deal: dict) -> datetime | None:
    for key in ("openTimestamp", "open_timestamp", "openTime", "open_time", "timestamp", "time"):
        dt = _to_datetime(deal.get(key))
        if dt:
            return dt
    return None


def _deal_close_time(deal: dict) -> datetime | None:
    for key in ("closeTimestamp", "close_timestamp", "closeTime", "close_time", "expiration", "expiry"):
        dt = _to_datetime(deal.get(key))
        if dt:
            return dt
    return None


def _deal_open_price(deal: dict) -> float | None:
    for key in ("openPrice", "open_price", "price", "entryPrice", "entry_price"):
        try:
            value = float(deal.get(key))
            if value > 0:
                return value
        except Exception:
            continue
    return None


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


async def sync_broker_positions(telegram_id: int) -> dict:
    """Detect trades opened manually in Pocket and attach them to recent AlphaPulse signals.

    This is intentionally read-only. It only calls the direct client's passive
    deal-state reader and never sends openOrder/buy/sell messages.
    """
    if not ADMIN_ID or int(telegram_id) != ADMIN_ID:
        return {"supported": False, "seen": 0, "matched": 0, "created": 0}

    try:
        client = await market_data.connect()
        getter = getattr(client, "get_opened_deals", None)
        if getter is None:
            return {"supported": False, "seen": 0, "matched": 0, "created": 0}
        deals = await getter(listen_seconds=0.65)
    except Exception as exc:
        logger.warning("Pocket opened-deals sync failed: %s", type(exc).__name__)
        return {"supported": True, "seen": 0, "matched": 0, "created": 0, "error": type(exc).__name__}

    now = _now()
    matched = 0
    created = 0
    for deal in deals or []:
        if not isinstance(deal, dict):
            continue
        asset = _deal_asset(deal)
        direction = _deal_direction(deal)
        if not asset or direction is None:
            continue
        open_time = _deal_open_time(deal) or now
        close_time = _deal_close_time(deal)
        entry_price = _deal_open_price(deal)

        async with AsyncSessionLocal() as db:
            signals = (
                await db.execute(
                    select(Signal)
                    .where(
                        Signal.asset == asset,
                        Signal.direction == direction,
                        Signal.created_at >= now - timedelta(minutes=12),
                    )
                    .order_by(desc(Signal.created_at))
                    .limit(20)
                )
            ).scalars().all()

            if not signals:
                continue

            signal = min(
                signals,
                key=lambda s: min(
                    abs((open_time - s.entry_time).total_seconds()),
                    abs((open_time - s.created_at).total_seconds()),
                ),
            )
            distance = min(
                abs((open_time - signal.entry_time).total_seconds()),
                abs((open_time - signal.created_at).total_seconds()),
            )
            if distance > 360:
                continue
            matched += 1

            existing = (
                await db.execute(
                    select(PaperPosition).where(
                        PaperPosition.telegram_id == telegram_id,
                        PaperPosition.signal_id == signal.id,
                    ).order_by(desc(PaperPosition.created_at)).limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue

            if entry_price is None:
                try:
                    entry_price = await market_data.latest_price(asset)
                except Exception:
                    entry_price = float(signal.analysis_price or signal.entry_price or 0)
            if not entry_price:
                continue

            expiry_time = close_time or signal.expiry_time
            if expiry_time <= open_time:
                expiry_time = signal.expiry_time if signal.expiry_time > open_time else open_time + timedelta(minutes=1)

            position = PaperPosition(
                telegram_id=telegram_id,
                signal_id=signal.id,
                source="broker",
                pair=signal.pair,
                asset=signal.asset,
                timeframe=signal.timeframe,
                strategy=signal.strategy,
                direction=signal.direction,
                status="OPEN",
                entry_price=float(entry_price),
                entry_time=open_time,
                expiry_time=expiry_time,
                result=SignalResult.PENDING,
            )
            db.add(position)
            await db.commit()
            created += 1
            logger.info("Auto-tracking manually opened Pocket deal for signal=%s asset=%s", signal.id, asset)

    return {"supported": True, "seen": len(deals or []), "matched": matched, "created": created}


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
