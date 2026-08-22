from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import desc, select

from backend.models.db_models import AsyncSessionLocal, PaperPosition, Signal, SignalResult
from backend.services.pocketoption_otc import TF_SECONDS
from backend.services.positions import serialize_position
from backend.services.realtime_tokens import issue_realtime_token
from backend.services.worker_protocol import await_command, enqueue_command, ensure_demo_account
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


async def _worker_candles(user_id: int, pair: str, timeframe: str, count: int) -> dict:
    account_id = await ensure_demo_account()
    bucket = int(datetime.now(timezone.utc).timestamp() // 2)
    command = await enqueue_command(
        account_id=account_id,
        command_type='MARKET_CANDLES',
        payload={'pair': pair, 'timeframe': timeframe, 'count': count},
        idempotency_key=f'live-chart:{int(user_id)}:{pair}:{timeframe}:{count}:{bucket}'[:128],
    )
    try:
        return await await_command(int(command['id']), account_id, timeout_seconds=15.0)
    except TimeoutError as exc:
        raise HTTPException(503, 'Windows worker is not responding') from exc
    except RuntimeError as exc:
        raise HTTPException(503, 'Windows worker could not load Pocket market data') from exc


@router.post('/realtime-token')
async def realtime_token(user: TelegramMiniAppUser = Depends(admin_user)):
    transport = str(os.getenv('REALTIME_TRANSPORT') or 'polling').strip().lower()
    public_url = str(os.getenv('REALTIME_PUBLIC_URL') or '').strip().rstrip('/')
    if transport != 'wss' or not public_url:
        return {'transport': 'polling', 'poll_interval_ms': 1000}
    account_id = await ensure_demo_account()
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
    # This endpoint creates a paper/live-view position only; it never sends a
    # Pocket order. Protected broker execution belongs exclusively to Windows worker.
    current = _now()
    async with AsyncSessionLocal() as db:
        signal = await db.get(Signal, int(data.signal_id))
        if signal is None:
            raise HTTPException(404, 'Signal not found')
        if signal.expiry_time <= current:
            raise HTTPException(409, 'Signal has already expired')
        existing = (
            await db.execute(
                select(PaperPosition).where(
                    PaperPosition.telegram_id == int(user.id),
                    PaperPosition.signal_id == signal.id,
                    PaperPosition.status == 'OPEN',
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return serialize_position(existing)
        price = signal.entry_price or signal.analysis_price
        if price is None:
            raise HTTPException(409, 'Signal does not have a broker-derived reference price yet')
        position = PaperPosition(
            telegram_id=int(user.id), signal_id=signal.id,
            source='vip' if signal.is_vip else 'regular',
            pair=signal.pair, asset=signal.asset, timeframe=signal.timeframe,
            strategy=signal.strategy, direction=signal.direction,
            status='OPEN', entry_price=float(price), entry_time=current,
            expiry_time=signal.expiry_time, result=SignalResult.PENDING,
        )
        db.add(position)
        await db.commit()
        await db.refresh(position)
        return serialize_position(position)


@router.get('/active')
async def active(response: Response, user: TelegramMiniAppUser = Depends(telegram_user)):
    _no_cache(response)
    # AUTO/broker reconciliation is worker-owned. The public API only reads Neon.
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(PaperPosition)
                .where(PaperPosition.telegram_id == int(user.id))
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
    async with AsyncSessionLocal() as db:
        row = await db.get(PaperPosition, position_id)
        if row is None or int(row.telegram_id) != int(user.id):
            raise HTTPException(404, 'Position not found')
        payload = serialize_position(row)

    chart_count = max(40, min(120, count))
    timeframe = str(payload.get('timeframe') or '15s')
    if timeframe not in TF_SECONDS:
        timeframe = '15s'
    market = await _worker_candles(int(user.id), payload['pair'], timeframe, chart_count)
    candles = [dict(candle) for candle in (market.get('candles') or [])[-chart_count:]]
    if not candles:
        raise HTTPException(503, 'Windows worker returned no Pocket candles')
    current_price = float(market.get('current_price') or candles[-1]['close'])

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

    # Non-AUTO paper positions may be settled from worker-sourced market data.
    # AUTO positions are settled only from the actual Pocket deal result by worker.
    if payload['status'] == 'OPEN' and payload['source'] != 'auto' and expiry <= now:
        async with AsyncSessionLocal() as db:
            locked = await db.get(PaperPosition, int(position_id))
            if locked is not None and locked.status == 'OPEN':
                locked.close_price = current_price
                locked.result = SignalResult(floating)
                locked.status = 'CLOSED'
                locked.closed_at = now
                await db.commit()
                await db.refresh(locked)
                payload = serialize_position(locked)

    return {
        'position': payload,
        'current_price': current_price,
        'floating_result': payload['result'] if payload['status'] == 'CLOSED' else floating,
        'seconds_to_expiry': max(0, int((expiry - now).total_seconds())),
        'server_time': datetime.now(timezone.utc).isoformat(),
        'chart_timeframe': timeframe,
        'chart_period_seconds': int(TF_SECONDS[timeframe]),
        'chart_source': 'windows-worker-pocket',
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
            'id': s.id, 'pair': s.pair, 'asset': s.asset, 'timeframe': s.timeframe,
            'strategy': s.strategy, 'direction': s.direction.value,
            'confidence': s.confidence, 'is_vip': s.is_vip,
            'analysis_price': s.analysis_price, 'entry_price': s.entry_price,
            'close_price': s.close_price,
            'entry_time': s.entry_time.isoformat() + 'Z',
            'expiry_time': s.expiry_time.isoformat() + 'Z',
            'result': s.result.value, 'reason': s.reason,
            'created_at': s.created_at.isoformat() + 'Z',
        }
        for s in rows
    ]
