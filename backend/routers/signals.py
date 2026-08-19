from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.db_models import Signal, SignalDirection, SignalResult, StrategyPerformance
from backend.services.database import get_db
from backend.services.online_ml import get_model
from backend.services.pocketoption_otc import DISPLAY_TO_ASSET, MarketDataUnavailable, OTC_ASSETS, TF_SECONDS, market_data
from backend.services.signal_engine import signal_engine
from backend.services.strategies import STRATEGY_LABELS

router = APIRouter()
logger = logging.getLogger('alphapulse.signals')


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def out(signal):
    return {
        'id': signal.id,
        'pair': signal.pair,
        'asset': signal.asset,
        'timeframe': signal.timeframe,
        'strategy': signal.strategy,
        'strategy_label': STRATEGY_LABELS.get(signal.strategy, signal.strategy),
        'direction': signal.direction.value,
        'confidence': signal.confidence,
        'model_probability': signal.model_probability,
        'is_vip': signal.is_vip,
        'reason': signal.reason,
        'indicators': {'RSI': signal.rsi, 'EMA': signal.ema_signal, 'MACD': signal.macd_signal},
        'analysis_price': signal.analysis_price,
        'entry_price': signal.entry_price,
        'close_price': signal.close_price,
        'entry_time': signal.entry_time.isoformat() + 'Z',
        'expiry_time': signal.expiry_time.isoformat() + 'Z',
        'result': signal.result.value,
        'created_at': signal.created_at.isoformat() + 'Z',
        'closed_at': signal.closed_at.isoformat() + 'Z' if signal.closed_at else None,
    }


async def save_candidate(db, candidate):
    entry_time = parse(candidate['entry_time'])
    expiry_time = parse(candidate['expiry_time'])
    existing = (
        await db.execute(
            select(Signal).where(
                Signal.asset == candidate['asset'],
                Signal.timeframe == candidate['timeframe'],
                Signal.strategy == candidate['strategy'],
                Signal.entry_time == entry_time,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing, True

    indicators = candidate.get('indicators', {})
    signal = Signal(
        pair=candidate['pair'],
        asset=candidate['asset'],
        timeframe=candidate['timeframe'],
        strategy=candidate['strategy'],
        direction=SignalDirection(candidate['direction']),
        confidence=candidate['confidence'],
        model_probability=candidate.get('model_probability'),
        is_vip=candidate['confidence'] >= 80,
        rsi=indicators.get('rsi'),
        ema_signal='Bull' if candidate['direction'] == 'BUY' else 'Bear',
        macd_signal='Positive' if candidate['direction'] == 'BUY' else 'Negative',
        trend_strength=indicators.get('atr_expansion') or indicators.get('ema_gap_atr'),
        reason=candidate['reason'],
        features_json=json.dumps(candidate['features']),
        analysis_price=candidate.get('analysis_price'),
        entry_time=entry_time,
        expiry_time=expiry_time,
        result=SignalResult.PENDING,
    )
    db.add(signal)
    await db.commit()
    await db.refresh(signal)
    return signal, False


class AnalyzeRequest(BaseModel):
    pair: str
    timeframe: str = '5m'
    user_id: Optional[int] = None


@router.post('/analyze')
async def analyze(req: AnalyzeRequest, db: AsyncSession = Depends(get_db)):
    asset = DISPLAY_TO_ASSET.get(req.pair.replace(' OTC', '').strip())
    if not asset:
        raise HTTPException(400, 'Unsupported OTC pair')
    if req.timeframe not in TF_SECONDS:
        raise HTTPException(400, 'Unsupported timeframe')
    try:
        candidate = await signal_engine.evaluate_asset_best(asset, req.timeframe)
    except MarketDataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if not candidate:
        return {'error': 'No confirmed strategy setup right now. Try another pair or timeframe.'}
    signal, _ = await save_candidate(db, candidate)
    data = out(signal)
    data['indicators'] = candidate.get('indicators', {})
    return data


class ScanRequest(BaseModel):
    timeframe: str = '1m'
    assets: list[str] = Field(default_factory=lambda: list(OTC_ASSETS.keys()))
    min_confidence: float = 72.0


@router.post('/scan-best')
async def scan_best(req: ScanRequest, db: AsyncSession = Depends(get_db)):
    assets = [asset for asset in req.assets if asset in OTC_ASSETS]
    try:
        candidate = await signal_engine.scan_best(req.timeframe, assets)
    except MarketDataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if not candidate or candidate['confidence'] < req.min_confidence:
        return {'status': 'NO_SIGNAL', 'signal': None}
    signal, duplicate = await save_candidate(db, candidate)
    return {'status': 'SIGNAL', 'signal': out(signal), 'duplicate': duplicate}


def _new_performance(strategy: str) -> StrategyPerformance:
    return StrategyPerformance(strategy=strategy, samples=0, wins=0, losses=0, draws=0)


@router.post('/reconcile')
async def reconcile(db: AsyncSession = Depends(get_db)):
    pending = (
        await db.execute(
            select(Signal)
            .where(Signal.result == SignalResult.PENDING)
            .order_by(Signal.entry_time)
            .limit(100)
        )
    ).scalars().all()

    closed = []
    entered = 0
    trained = 0
    errors = 0
    current = now()

    for signal in pending:
        try:
            if signal.entry_price is None and signal.entry_time <= current:
                signal.entry_price = await market_data.boundary_price(signal.asset, signal.entry_time)
                entered += 1

            if signal.entry_price is not None and signal.expiry_time <= current:
                signal.close_price = await market_data.boundary_price(signal.asset, signal.expiry_time)
                delta = signal.close_price - signal.entry_price
                epsilon = max(abs(signal.entry_price) * 1e-10, 1e-10)

                if abs(delta) <= epsilon:
                    signal.result = SignalResult.DRAW
                elif signal.direction == SignalDirection.BUY:
                    signal.result = SignalResult.WIN if delta > 0 else SignalResult.LOSS
                else:
                    signal.result = SignalResult.WIN if delta < 0 else SignalResult.LOSS

                signal.closed_at = current
                closed.append(out(signal))

                performance = (
                    await db.execute(
                        select(StrategyPerformance).where(
                            StrategyPerformance.strategy == signal.strategy
                        )
                    )
                ).scalar_one_or_none()
                if performance is None:
                    performance = _new_performance(signal.strategy)
                    db.add(performance)

                if signal.result in {SignalResult.WIN, SignalResult.LOSS} and signal.trained_at is None:
                    await get_model(signal.strategy).learn(
                        json.loads(signal.features_json),
                        signal.result == SignalResult.WIN,
                    )
                    signal.trained_at = current
                    trained += 1
                    performance.samples = int(performance.samples or 0) + 1
                    if signal.result == SignalResult.WIN:
                        performance.wins = int(performance.wins or 0) + 1
                    else:
                        performance.losses = int(performance.losses or 0) + 1
                elif signal.result == SignalResult.DRAW:
                    performance.draws = int(performance.draws or 0) + 1
        except MarketDataUnavailable as exc:
            logger.warning('Reconcile market data unavailable for signal %s: %s', signal.id, exc)
            continue
        except Exception as exc:
            errors += 1
            logger.exception('Reconcile failed for signal id=%s asset=%s strategy=%s: %s', signal.id, signal.asset, signal.strategy, exc)
            try:
                await db.rollback()
            except Exception:
                pass
            continue

    await db.commit()
    return {
        'entered': entered,
        'closed': len(closed),
        'trained': trained,
        'errors': errors,
        'closed_signals': closed,
    }


@router.get('/history')
async def history(limit: int = Query(30, ge=1, le=200), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Signal).order_by(desc(Signal.created_at)).limit(limit))
    ).scalars().all()
    return [out(signal) for signal in rows]


@router.get('/vip')
async def vip(limit: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(Signal)
            .where(Signal.is_vip == True)
            .order_by(desc(Signal.created_at))
            .limit(limit)
        )
    ).scalars().all()
    return [out(signal) for signal in rows]


@router.get('/ml')
async def ml():
    result = {}
    for key in STRATEGY_LABELS:
        model = get_model(key)
        await model.hydrate()
        result[key] = model.stats()
    return result
