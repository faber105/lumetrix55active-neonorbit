from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from backend.models.db_models import (
    AsyncSessionLocal,
    Signal,
    SignalDirection,
    SignalResult,
    StrategyPerformance,
    utcnow,
)
from backend.services.online_ml import get_model
from backend.services.pocketoption_otc import MarketDataUnavailable, market_data

logger = logging.getLogger("alphapulse.reconciler")


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
            if result == SignalResult.WIN:
                perf.samples += 1
                perf.wins += 1
            elif result == SignalResult.LOSS:
                perf.samples += 1
                perf.losses += 1
            elif result == SignalResult.DRAW:
                perf.draws += 1


async def _train_once(signal_id: int, strategy: str, features_json: str, won: bool) -> bool:
    """Atomically claim one signal for ML so overlapping scanner calls cannot double-train."""
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


async def reconcile_pending(limit: int = 100) -> dict:
    """Resolve entry/expiry prices from Pocket data without letting one row kill the scanner."""
    async with AsyncSessionLocal() as db:
        ids = list(
            (
                await db.execute(
                    select(Signal.id)
                    .where(Signal.result == SignalResult.PENDING)
                    .order_by(Signal.entry_time)
                    .limit(limit)
                )
            ).scalars().all()
        )

    entered = 0
    closed = 0
    trained = 0
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
                    if signal.entry_price is None and signal.entry_time <= current:
                        signal.entry_price = await market_data.boundary_price(signal.asset, signal.entry_time)
                        entered += 1

                    if signal.entry_price is not None and signal.expiry_time <= current:
                        signal.close_price = await market_data.boundary_price(signal.asset, signal.expiry_time)
                        delta = float(signal.close_price) - float(signal.entry_price)
                        epsilon = max(abs(float(signal.entry_price)) * 1e-10, 1e-10)
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
                            "entry_price": float(signal.entry_price),
                            "close_price": float(signal.close_price),
                        }

            if closed_snapshot is not None:
                result = closed_snapshot["result"]
                if result in {SignalResult.WIN, SignalResult.LOSS}:
                    if await _train_once(
                        closed_snapshot["id"],
                        closed_snapshot["strategy"],
                        closed_snapshot["features_json"],
                        result == SignalResult.WIN,
                    ):
                        trained += 1
                        await _update_performance(closed_snapshot["strategy"], result)
                elif result == SignalResult.DRAW:
                    await _update_performance(closed_snapshot["strategy"], result)

        except MarketDataUnavailable as exc:
            # A transient broker/history failure should not poison other signals.
            logger.warning("Reconcile market data unavailable for signal %s: %s", signal_id, exc)
            errors.append({"id": signal_id, "type": "market_data"})
        except Exception as exc:
            logger.exception("Reconcile failed for signal %s: %s", signal_id, exc)
            errors.append({"id": signal_id, "type": type(exc).__name__})

    return {
        "entered": entered,
        "closed": closed,
        "trained": trained,
        "pending_checked": len(ids),
        "errors": errors,
    }
