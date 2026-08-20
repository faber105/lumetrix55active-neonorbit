from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.models.db_models import AsyncSessionLocal, Signal, SignalDirection, SignalResult
from backend.services.pocketoption_otc import TF_SECONDS
from backend.services.strategies import STRATEGY_LABELS


def _parse(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _fresh_times(candidate: dict) -> tuple[datetime, datetime]:
    entry_time = _parse(candidate['entry_time'])
    expiry_time = _parse(candidate['expiry_time'])
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # A full OTC scan can take longer than the original 4-second lead. The old
    # code then saved a perfectly good setup with an entry timestamp already in
    # the past, so AUTO found SIGNAL_FOUND but correctly refused to open it.
    # Rebase only stale candidates at persistence time; fresh manual/VIP signals
    # keep their original scheduled timestamp.
    if entry_time <= now + timedelta(seconds=1):
        lead = 4
        seconds = int(TF_SECONDS.get(str(candidate.get('timeframe') or ''), 60))
        entry_time = now + timedelta(seconds=lead)
        expiry_time = entry_time + timedelta(seconds=seconds)
    return entry_time, expiry_time


def serialize_signal(signal):
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
        'is_vip': bool(signal.is_vip),
        'reason': signal.reason,
        'analysis_price': signal.analysis_price,
        'entry_price': signal.entry_price,
        'close_price': signal.close_price,
        'entry_time': signal.entry_time.isoformat() + 'Z',
        'expiry_time': signal.expiry_time.isoformat() + 'Z',
        'result': signal.result.value,
        'created_at': signal.created_at.isoformat() + 'Z',
        'closed_at': signal.closed_at.isoformat() + 'Z' if signal.closed_at else None,
    }


async def save_signal(candidate, *, is_vip=False):
    entry_time, expiry_time = _fresh_times(candidate)
    strategy = str(candidate['strategy'])
    asset = str(candidate['asset'])
    timeframe = str(candidate['timeframe'])
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(Signal).where(
                    Signal.asset == asset,
                    Signal.timeframe == timeframe,
                    Signal.strategy == strategy,
                    Signal.entry_time == entry_time,
                )
            )
        ).scalar_one_or_none()
        if existing:
            payload = serialize_signal(existing)
            payload['confirmations'] = candidate.get('confirmations', [])
            payload['indicators'] = candidate.get('indicators', {})
            return payload, True

        indicators = candidate.get('indicators', {}) or {}
        row = Signal(
            pair=candidate['pair'],
            asset=asset,
            timeframe=timeframe,
            strategy=strategy,
            direction=SignalDirection(candidate['direction']),
            confidence=float(candidate['confidence']),
            model_probability=candidate.get('model_probability'),
            is_vip=bool(is_vip),
            rsi=indicators.get('rsi'),
            ema_signal='Bull' if candidate['direction'] == 'BUY' else 'Bear',
            macd_signal='Positive' if candidate['direction'] == 'BUY' else 'Negative',
            trend_strength=indicators.get('adx') or indicators.get('atr_expansion'),
            reason=candidate['reason'],
            features_json=json.dumps(candidate.get('features', []), separators=(',', ':')),
            analysis_price=candidate.get('analysis_price'),
            entry_time=entry_time,
            expiry_time=expiry_time,
            result=SignalResult.PENDING,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        payload = serialize_signal(row)
        payload['confirmations'] = candidate.get('confirmations', [])
        payload['indicators'] = candidate.get('indicators', {})
        return payload, False
