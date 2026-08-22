from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.db_models import Signal
from backend.services.database import get_db
from backend.services.online_ml import get_model
from backend.services.pocketoption_otc import OTC_ASSETS
from backend.services.strategies import STRATEGY_LABELS
from backend.services.worker_protocol import await_command, enqueue_command, ensure_demo_account
from backend.telegram_auth import TelegramMiniAppUser, admin_user, telegram_user

router = APIRouter()


def out(signal: Signal) -> dict:
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
        'source': 'vip' if signal.is_vip else 'scanner',
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


async def _worker_call(user_id: int, command_type: str, payload: dict, key: str, timeout: float = 25.0) -> dict:
    # Read-only market analysis for all authenticated Mini App users is served by
    # the one broker account whose lease is owned by the Windows worker. The
    # requesting user id remains part of command idempotency but never creates a
    # second, unserviced Pocket credential slot.
    account_id = await ensure_demo_account()
    command = await enqueue_command(
        account_id=account_id,
        command_type=command_type,
        payload=payload,
        idempotency_key=key[:128],
    )
    try:
        return await await_command(int(command['id']), account_id, timeout_seconds=timeout)
    except TimeoutError as exc:
        raise HTTPException(503, 'Windows worker is not responding') from exc
    except RuntimeError as exc:
        raise HTTPException(503, 'Windows worker could not complete market analysis') from exc


class AnalyzeRequest(BaseModel):
    pair: str
    timeframe: str = '1m'
    strategy: str = 'trend_pulse'
    min_confidence: float = Field(0.0, ge=0, le=99)
    user_id: Optional[int] = None


@router.post('/analyze')
async def analyze(req: AnalyzeRequest, user: TelegramMiniAppUser = Depends(telegram_user)):
    payload = req.model_dump(exclude={'user_id'})
    bucket = int(datetime.now(timezone.utc).timestamp() // 2)
    result = await _worker_call(
        int(user.id),
        'ANALYZE_SIGNAL',
        payload,
        f'analyze:{int(user.id)}:{req.pair}:{req.timeframe}:{req.strategy}:{bucket}',
    )
    if result.get('signal'):
        result['signal']['source'] = 'manual'
    return result


class StrategyScanRequest(BaseModel):
    strategy: str
    timeframe: str = '1m'
    min_confidence: float = Field(72.0, ge=0, le=99)


@router.post('/scan-strategy')
async def scan_strategy(req: StrategyScanRequest, user: TelegramMiniAppUser = Depends(telegram_user)):
    bucket = int(datetime.now(timezone.utc).timestamp() // 3)
    return await _worker_call(
        int(user.id),
        'SCAN_STRATEGY',
        req.model_dump(),
        f'scan-strategy:{int(user.id)}:{req.strategy}:{req.timeframe}:{bucket}',
    )


class ScanRequest(BaseModel):
    timeframe: str = '1m'
    assets: list[str] = Field(default_factory=lambda: list(OTC_ASSETS.keys()))
    min_confidence: float = Field(72.0, ge=0, le=99)


@router.post('/scan-best')
async def scan_best(req: ScanRequest, user: TelegramMiniAppUser = Depends(telegram_user)):
    bucket = int(datetime.now(timezone.utc).timestamp() // 3)
    return await _worker_call(
        int(user.id),
        'SCAN_BEST',
        req.model_dump(),
        f'scan-best:{int(user.id)}:{req.timeframe}:{bucket}',
    )


@router.post('/reconcile')
async def reconcile(user: TelegramMiniAppUser = Depends(admin_user)):
    bucket = int(datetime.now(timezone.utc).timestamp() // 3)
    return await _worker_call(
        int(user.id),
        'RECONCILE_SIGNALS',
        {},
        f'reconcile-signals:{int(user.id)}:{bucket}',
    )


@router.get('/history')
async def history(
    limit: int = Query(30, ge=1, le=200),
    _: TelegramMiniAppUser = Depends(telegram_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(Signal).order_by(desc(Signal.created_at)).limit(limit))).scalars().all()
    return [out(signal) for signal in rows]


@router.get('/vip')
async def vip(
    limit: int = Query(20, ge=1, le=100),
    _: TelegramMiniAppUser = Depends(telegram_user),
    db: AsyncSession = Depends(get_db),
):
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
async def ml(_: TelegramMiniAppUser = Depends(telegram_user)):
    result = {}
    for key in STRATEGY_LABELS:
        model = get_model(key)
        await model.hydrate()
        result[key] = model.stats()
    return result
