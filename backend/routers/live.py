from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select

from backend.models.db_models import AsyncSessionLocal, PaperPosition, Signal
from backend.services.pocketoption_otc import MarketDataUnavailable, TF_SECONDS, market_data
from backend.services.positions import reconcile_positions, serialize_position, sync_broker_positions, take_signal
from backend.telegram_auth import TelegramMiniAppUser, telegram_user

router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TakeRequest(BaseModel):
    signal_id: int


@router.post('/take')
async def take(
    data: TakeRequest,
    user: TelegramMiniAppUser = Depends(telegram_user),
):
    try:
        position = await take_signal(user.id, data.signal_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except MarketDataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return serialize_position(position)


@router.get('/active')
async def active(user: TelegramMiniAppUser = Depends(telegram_user)):
    # For the admin's connected Pocket session, passively detect a deal opened
    # manually in Pocket and attach it to the matching recent AlphaPulse signal.
    broker_sync = await sync_broker_positions(user.id)
    await reconcile_positions()
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(PaperPosition)
                .where(PaperPosition.telegram_id == user.id)
                .order_by(desc(PaperPosition.created_at))
                .limit(20)
            )
        ).scalars().all()
        payload = [serialize_position(row) for row in rows]
    # Keep the existing array API for Mini App compatibility. The sync status is
    # intentionally not exposed because deal counts are account-private.
    return payload


@router.get('/position/{position_id}')
async def position(
    position_id: int,
    count: int = Query(50, ge=20, le=120),
    user: TelegramMiniAppUser = Depends(telegram_user),
):
    await reconcile_positions()
    async with AsyncSessionLocal() as db:
        row = await db.get(PaperPosition, position_id)
        if row is None or int(row.telegram_id) != user.id:
            raise HTTPException(404, 'Position not found')
        payload = serialize_position(row)

    try:
        candles, current_price = await asyncio.gather(
            market_data.get_candles(payload['asset'], payload['timeframe'], count),
            market_data.latest_price(payload['asset']),
        )
    except MarketDataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

    current_price = float(current_price)
    # Pocket history can return only completed higher-timeframe candles. Merge the
    # freshest 1m price into the current selected-timeframe candle so the chart
    # moves during the trade rather than jumping only after candle close.
    period = int(TF_SECONDS.get(payload['timeframe'], 60))
    now_ts = int(time.time())
    bucket = now_ts - (now_ts % period)
    if candles:
        last = dict(candles[-1])
        last_time = int(last.get('time') or 0)
        if last_time == bucket:
            last['close'] = current_price
            last['high'] = max(float(last.get('high', current_price)), current_price)
            last['low'] = min(float(last.get('low', current_price)), current_price)
            candles[-1] = last
        elif last_time < bucket:
            open_price = float(last.get('close', current_price))
            candles.append({
                'time': bucket,
                'open': open_price,
                'high': max(open_price, current_price),
                'low': min(open_price, current_price),
                'close': current_price,
            })
            candles = candles[-count:]

    now = _now()
    expiry = datetime.fromisoformat(payload['expiry_time'].replace('Z', '+00:00')).replace(tzinfo=None)
    entry = float(payload['entry_price'])
    direction = payload['direction']
    delta = current_price - entry
    floating = 'DRAW'
    if abs(delta) > max(abs(entry) * 1e-10, 1e-10):
        if direction == 'BUY':
            floating = 'WIN' if delta > 0 else 'LOSS'
        else:
            floating = 'WIN' if delta < 0 else 'LOSS'

    return {
        'position': payload,
        'current_price': current_price,
        'floating_result': payload['result'] if payload['status'] == 'CLOSED' else floating,
        'seconds_to_expiry': max(0, int((expiry - now).total_seconds())),
        'server_time': datetime.now(timezone.utc).isoformat(),
        'candles': candles,
    }


@router.get('/feed')
async def feed(
    kind: str = Query('all', pattern='^(all|regular|vip)$'),
    limit: int = Query(30, ge=1, le=100),
):
    async with AsyncSessionLocal() as db:
        query = select(Signal).order_by(desc(Signal.created_at)).limit(limit)
        if kind == 'vip':
            query = select(Signal).where(Signal.is_vip == True).order_by(desc(Signal.created_at)).limit(limit)
        elif kind == 'regular':
            query = select(Signal).where(Signal.is_vip == False).order_by(desc(Signal.created_at)).limit(limit)
        rows = (await db.execute(query)).scalars().all()
    return [
        {
            'id': s.id,
            'pair': s.pair,
            'asset': s.asset,
            'timeframe': s.timeframe,
            'strategy': s.strategy,
            'direction': s.direction.value,
            'confidence': s.confidence,
            'is_vip': s.is_vip,
            'analysis_price': s.analysis_price,
            'entry_price': s.entry_price,
            'close_price': s.close_price,
            'entry_time': s.entry_time.isoformat() + 'Z',
            'expiry_time': s.expiry_time.isoformat() + 'Z',
            'result': s.result.value,
            'reason': s.reason,
            'created_at': s.created_at.isoformat() + 'Z',
        }
        for s in rows
    ]
