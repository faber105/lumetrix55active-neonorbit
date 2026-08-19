from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_subscribed_user
from api.models.database import get_db
from api.models.signal import Signal
from api.models.user import User
from api.schemas import SignalAnalyzeRequest, SignalAnalyzeResponse, SignalSchema
from config import get_settings
from signal_engine.otc_engine import OTCSignalEngine
from signal_engine.ml_store import load_online_model_from_db
from signal_engine.otc_provider import PocketOptionOTCProvider, TIMEFRAME_SECONDS

router = APIRouter(prefix='/signals', tags=['signals'])


@router.get('/active', response_model=list[SignalSchema])
async def get_active_signals(
    user: User = Depends(get_current_subscribed_user),
    db: AsyncSession = Depends(get_db),
    timeframe: str | None = None,
    category: str | None = None,
    asset: str | None = None,
) -> list[Signal]:
    stmt = (
        select(Signal)
        .where(
            Signal.expires_at > datetime.utcnow(),
            Signal.result == 'PENDING',
            or_(Signal.requested_by_user_id.is_(None), Signal.requested_by_user_id == user.id),
        )
        .order_by(desc(Signal.confidence), desc(Signal.created_at))
        .limit(get_settings().max_active_signals)
    )
    if timeframe:
        stmt = stmt.where(Signal.timeframe == timeframe)
    if category:
        stmt = stmt.where(Signal.asset_category == category)
    if asset:
        stmt = stmt.where(Signal.asset.ilike(f'%{asset}%'))
    return list((await db.scalars(stmt)).all())


@router.get('/history', response_model=list[SignalSchema])
async def get_signal_history(
    user: User = Depends(get_current_subscribed_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    from_date: datetime | None = Query(default=None, alias='from'),
    to_date: datetime | None = Query(default=None, alias='to'),
) -> list[Signal]:
    stmt = (
        select(Signal)
        .where(or_(Signal.requested_by_user_id.is_(None), Signal.requested_by_user_id == user.id))
        .order_by(desc(Signal.created_at))
        .limit(limit)
        .offset(offset)
    )
    if from_date:
        stmt = stmt.where(Signal.created_at >= from_date)
    if to_date:
        stmt = stmt.where(Signal.created_at <= to_date)
    return list((await db.scalars(stmt)).all())


@router.post('/analyze', response_model=SignalAnalyzeResponse)
async def analyze_signal(
    payload: SignalAnalyzeRequest,
    user: User = Depends(get_current_subscribed_user),
    db: AsyncSession = Depends(get_db),
) -> SignalAnalyzeResponse:
    settings = get_settings()
    if payload.timeframe not in TIMEFRAME_SECONDS:
        raise HTTPException(status_code=400, detail='OTC engine supports 1m, 3m and 5m timeframes')

    raw = payload.asset.strip()
    normalized = raw.upper().replace('/', '').replace(' OTC', '') + '_otc' if 'OTC' in raw.upper() and not raw.endswith('_otc') else raw
    allowed = {x.lower(): x for x in settings.parsed_otc_assets}
    asset = allowed.get(normalized.lower())
    if asset is None:
        raise HTTPException(status_code=400, detail='Asset is not enabled in OTC_ASSETS')

    await load_online_model_from_db()
    provider = PocketOptionOTCProvider()
    try:
        frame = await provider.fetch(asset, payload.timeframe, count=250)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f'Pocket Option OTC data unavailable: {exc}') from exc
    finally:
        await provider.close()

    analysis = OTCSignalEngine().analyze_frame(asset, payload.timeframe, frame)
    if analysis.status != 'SIGNAL' or not analysis.direction or not analysis.entry_time or not analysis.expires_at:
        return SignalAnalyzeResponse(status='NO_SIGNAL', signal=None, message=f'Сейчас нет качественного входа. Режим: {analysis.regime}; стратегия: {analysis.strategy}.')

    signal = Signal(
        asset=analysis.asset,
        asset_category='otc',
        direction=analysis.direction,
        timeframe=analysis.timeframe,
        duration_sec=TIMEFRAME_SECONDS[analysis.timeframe],
        open_price=Decimal(str(analysis.entry_price_reference)) if analysis.entry_price_reference is not None else None,
        close_price=None,
        confidence=analysis.confidence,
        indicator_score=analysis.strategy_score * (1 if analysis.direction == 'CALL' else -1),
        ml_confidence=analysis.ml_confidence,
        strategy=analysis.strategy,
        market_regime=analysis.regime,
        data_source='pocketoption_otc_websocket',
        feature_snapshot=analysis.features,
        created_at=datetime.utcnow(),
        entry_time=analysis.entry_time,
        expires_at=analysis.expires_at,
        result='PENDING',
        agent_id='manual_analysis',
        requested_by_user_id=user.id,
    )
    db.add(signal)
    await db.commit()
    await db.refresh(signal)
    return SignalAnalyzeResponse(status='SIGNAL', signal=signal, message=f'{analysis.strategy}: {analysis.reason}. Вход указан по следующей границе свечи.')
