from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import desc, select

from backend.models.db_models import AsyncSessionLocal, PaperPosition, Signal
from backend.services.live_quotes import broker_live_chart
from backend.services.pocketoption_otc import MarketDataUnavailable, TF_SECONDS, market_data
from backend.services.positions import reconcile_positions, serialize_position, sync_broker_positions, take_signal
from backend.services.realtime_tokens import issue_realtime_token
from backend.services.worker_protocol import ensure_demo_account
from backend.telegram_auth import TelegramMiniAppUser, admin_user, telegram_user

router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _no_cache(response: Response | None) -> None:
    if response is None:
        return
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'


class TakeRequest(BaseModel):
    signal_id: int


@router.post('/realtime-token')
async def realtime_token(user: TelegramMiniAppUser = Depends(admin_user)):
    transport = str(os.getenv('REALTIME_TRANSPORT') or 'polling').strip().lower()
    public_url = str(os.getenv('REALTIME_PUBLIC_URL') or '').strip().rstrip('/')
    if transport != 'wss' or not public_url:
        return {'transport': 'polling', 'poll_interval_ms': 1000}
    account_id = await ensure_demo_account(int(user.id))
    try:
        token = issue_realtime_token(telegram_id=int(user.id), account_id=account_id)
    except RuntimeError:
        return {'transport': 'polling', 'poll_interval_ms': 1000}
    return {
        'transport': 'wss',
        'url': f"{public_url}/ws/live",
        'token': token,
        'expires_in': 60,
        'poll_interval_ms': 1000,
    }


@router.post('/take')
async def take(data: TakeRequest, user: TelegramMiniAppUser = Depends(telegram_user)):
    try:
        position = await take_signal(user.id, data.signal_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except MarketDataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return serialize_position(position)


@router.get('/active')
async def active(response: Response, user: TelegramMiniAppUser = Depends(telegram_user)):
    _no_cache(response)
    await sync_broker_positions(user.id)
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
        return [serialize_position(row) for row in rows]


@router.get('/position/{position_id}')
async def position(
    position_id: int,
    response: Response,
    count: int = Query(60, ge=20, le=120),
    user: TelegramMiniAppUser = Depends(telegram_user),
):
    _no_cache(response)
    await reconcile_positions()
    async with AsyncSessionLocal() as db:
        row = await db.get(PaperPosition, position_id)
        if row is None or int(row.telegram_id) != user.id:
            raise HTTPException(404, 'Position not found')
        payload = serialize_position(row)

    chart_count = max(40, min(120, count))
    timeframe = str(payload.get('timeframe') or '15s')
    if timeframe not in TF_SECONDS:
        timeframe = '15s'

    try:
        candles, current_price, source = await broker_live_chart(payload['asset'], timeframe, chart_count)
    except Exception:
        source = f'broker-history-{timeframe}-fallback'
        try:
            candles = await market_data.get_candles(payload['asset'], timeframe, chart_count)
        except MarketDataUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        if not candles:
            raise HTTPException(503, 'Pocket live chart returned no candles')
        current_price = float(candles[-1]['close'])

    candles = [dict(candle) for candle in candles[-chart_count:]]
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
        'current_price': float(current_price),
        'floating_result': payload['result'] if payload['status'] == 'CLOSED' else floating,
        'seconds_to_expiry': max(0, int((expiry - now).total_seconds())),
        'server_time': datetime.now(timezone.utc).isoformat(),
        'chart_timeframe': timeframe,
        'chart_period_seconds': int(TF_SECONDS[timeframe]),
        'chart_source': source,
        'candles': candles,
    }


@router.get('/feed')
async def feed(kind: str = Query('all', pattern='^(all|regular|vip)$'), limit: int = Query(30, ge=1, le=100)):
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
