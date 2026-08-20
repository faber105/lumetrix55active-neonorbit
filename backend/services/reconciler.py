from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select, update

from backend.models.db_models import (
    AsyncSessionLocal,
    PaperPosition,
    Signal,
    SignalDirection,
    SignalResult,
    StrategyPerformance,
    utcnow,
)
from backend.services.candle_outcome import exact_signal_candle_prices
from backend.services.online_ml import get_model
from backend.services.pocketoption_otc import MarketDataUnavailable, market_data

logger = logging.getLogger("alphapulse.reconciler")
STALE_MARKET_DATA_AGE = timedelta(hours=4)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _update_performance(strategy: str, result: SignalResult) -> None:
    async with AsyncSessionLocal() as db:
        async with db.begin():
            perf = (
                await db.execute(
                    select(StrategyPerformance)
                    .where(StrategyPerformance.strategy == strategy)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if perf is None:
                perf = StrategyPerformance(strategy=strategy)
                db.add(perf)
                await db.flush()
            samples = int(perf.samples or 0)
            wins = int(perf.wins or 0)
            losses = int(perf.losses or 0)
            draws = int(perf.draws or 0)
            if result == SignalResult.WIN:
                perf.samples = samples + 1
                perf.wins = wins + 1
            elif result == SignalResult.LOSS:
                perf.samples = samples + 1
                perf.losses = losses + 1
            elif result == SignalResult.DRAW:
                perf.draws = draws + 1


async def _train_once(signal_id: int, strategy: str, features_json: str, won: bool) -> bool:
    claimed_at = utcnow()
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(Signal)
            .where(Signal.id == signal_id, Signal.trained_at.is_(None))
            .values(trained_at=claimed_at)
            .returning(Signal.id)
        )
        claimed = result.scalar_one_or_none()
        await db.commit()
    if claimed is None:
        return False
    try:
        await get_model(strategy).learn(json.loads(features_json), won)
        return True
    except Exception:
        logger.exception("ML training failed for signal %s; releasing training claim", signal_id)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Signal)
                .where(Signal.id == signal_id, Signal.trained_at == claimed_at)
                .values(trained_at=None)
            )
            await db.commit()
        return False


async def _close_irrecoverable_stale(signal_id: int) -> bool:
    current = _now()
    async with AsyncSessionLocal() as db:
        signal = (
            await db.execute(
                select(Signal)
                .where(Signal.id == signal_id, Signal.result == SignalResult.PENDING)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if signal is None or signal.expiry_time > current - STALE_MARKET_DATA_AGE:
            return False
        signal.result = SignalResult.DRAW
        signal.closed_at = current
        await db.commit()
        logger.info("Closed stale unrecoverable signal %s as DRAW", signal_id)
        return True


async def reconcile_pending(limit: int = 100) -> dict:
    async with AsyncSessionLocal() as db:
        ids = list((await db.execute(
            select(Signal.id).where(Signal.result == SignalResult.PENDING).order_by(Signal.entry_time).limit(limit)
        )).scalars().all())

    entered = closed = trained = stale_closed = 0
    errors: list[dict] = []

    for signal_id in ids:
        closed_snapshot = None
        try:
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    signal = (
                        await db.execute(
                            select(Signal)
                            .where(Signal.id == signal_id, Signal.result == SignalResult.PENDING)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one_or_none()
                    if signal is None:
                        continue

                    current = _now()
                    auto_position = (
                        await db.execute(
                            select(PaperPosition)
                            .where(PaperPosition.signal_id == signal.id, PaperPosition.source == "auto")
                            .order_by(desc(PaperPosition.created_at))
                            .limit(1)
                        )
                    ).scalar_one_or_none()

                    # Executed AUTO signals must use the actual Pocket deal result.
                    # Never train or score an AUTO trade from a separately fetched
                    # candle, because its quote can differ from the broker option.
                    if auto_position is not None:
                        if auto_position.status != "CLOSED" or auto_position.result == SignalResult.PENDING:
                            continue
                        signal.entry_price = auto_position.entry_price
                        signal.close_price = auto_position.close_price
                        signal.result = auto_position.result
                        signal.closed_at = auto_position.closed_at or current
                        closed += 1
                        closed_snapshot = {
                            "id": signal.id,
                            "strategy": signal.strategy,
                            "features_json": signal.features_json,
                            "result": signal.result,
                        }
                    else:
                        if signal.entry_price is None and signal.entry_time <= current:
                            signal.entry_price = await market_data.boundary_price(signal.asset, signal.entry_time)
                            entered += 1
                        if signal.expiry_time <= current:
                            candle_open, candle_close = await exact_signal_candle_prices(
                                signal.asset, signal.timeframe, signal.entry_time
                            )
                            signal.entry_price = candle_open
                            signal.close_price = candle_close
                            delta = float(candle_close) - float(candle_open)
                            epsilon = max(abs(float(candle_open)) * 1e-10, 1e-10)
                            if abs(delta) <= epsilon:
                                signal.result = SignalResult.DRAW
                            elif signal.direction == SignalDirection.BUY:
                                signal.result = SignalResult.WIN if delta > 0 else SignalResult.LOSS
                            else:
                                signal.result = SignalResult.WIN if delta < 0 else SignalResult.LOSS
                            signal.closed_at = current
                            closed += 1
                            closed_snapshot = {
                                "id": signal.id,
                                "strategy": signal.strategy,
                                "features_json": signal.features_json,
                                "result": signal.result,
                            }

            if closed_snapshot is not None:
                result = closed_snapshot["result"]
                if result in {SignalResult.WIN, SignalResult.LOSS}:
                    if await _train_once(
                        closed_snapshot["id"], closed_snapshot["strategy"],
                        closed_snapshot["features_json"], result == SignalResult.WIN,
                    ):
                        trained += 1
                        await _update_performance(closed_snapshot["strategy"], result)
                elif result == SignalResult.DRAW:
                    await _update_performance(closed_snapshot["strategy"], result)

        except MarketDataUnavailable as exc:
            if await _close_irrecoverable_stale(signal_id):
                stale_closed += 1
                closed += 1
                continue
            logger.warning("Reconcile market data unavailable for signal %s: %s", signal_id, exc)
            errors.append({"id": signal_id, "type": "market_data"})
        except Exception as exc:
            logger.exception("Reconcile failed for signal %s: %s", signal_id, exc)
            errors.append({"id": signal_id, "type": type(exc).__name__})

    return {
        "entered": entered, "closed": closed, "stale_closed": stale_closed,
        "trained": trained, "pending_checked": len(ids), "errors": errors,
    }
