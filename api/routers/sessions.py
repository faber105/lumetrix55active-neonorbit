from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_subscribed_user
from api.models.database import get_db
from api.models.session import SessionTrade, TradingSession
from api.models.signal import Signal
from api.models.user import User
from api.schemas import (
    SessionEndRequest,
    SessionMarkRequest,
    SessionMarkResponse,
    SessionSchema,
    SessionStartRequest,
    SessionStatsResponse,
)
from config import get_settings

router = APIRouter(prefix="/sessions", tags=["sessions"])
PAYOUT_RATE = Decimal("0.80")


async def _get_user_active_session(db: AsyncSession, user_id: int) -> TradingSession | None:
    stmt = (
        select(TradingSession)
        .where(TradingSession.user_id == user_id, TradingSession.status == "active")
        .order_by(desc(TradingSession.started_at))
        .limit(1)
    )
    return await db.scalar(stmt)


@router.post("/start", response_model=SessionSchema)
async def start_session(
    payload: SessionStartRequest,
    user: User = Depends(get_current_subscribed_user),
    db: AsyncSession = Depends(get_db),
) -> TradingSession:
    active = await _get_user_active_session(db, user.id)
    if active is not None:
        raise HTTPException(status_code=409, detail="Active session already exists")

    session = TradingSession(
        user_id=user.id,
        goal_amount=payload.goal_amount,
        trade_amount=payload.trade_amount,
        timeframe_filter=payload.timeframe_filter,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.post("/end", response_model=SessionSchema)
async def end_session(
    payload: SessionEndRequest,
    user: User = Depends(get_current_subscribed_user),
    db: AsyncSession = Depends(get_db),
) -> TradingSession:
    session = await db.get(TradingSession, payload.session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != "active":
        raise HTTPException(status_code=409, detail="Session is already closed")

    session.status = payload.status
    session.ended_at = datetime.utcnow()
    await db.commit()
    await db.refresh(session)
    return session


@router.get("/active", response_model=SessionSchema | None)
async def get_active_session(
    user: User = Depends(get_current_subscribed_user),
    db: AsyncSession = Depends(get_db),
) -> TradingSession | None:
    return await _get_user_active_session(db, user.id)


@router.post("/mark", response_model=SessionMarkResponse)
async def mark_trade(
    payload: SessionMarkRequest,
    user: User = Depends(get_current_subscribed_user),
    db: AsyncSession = Depends(get_db),
) -> SessionMarkResponse:
    session = await db.get(TradingSession, payload.session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != "active":
        raise HTTPException(status_code=409, detail="Session is not active")

    signal = await db.get(Signal, payload.signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    if session.timeframe_filter and signal.timeframe != session.timeframe_filter:
        raise HTTPException(status_code=400, detail="Signal timeframe does not match session filter")

    duplicate_stmt = select(SessionTrade).where(
        SessionTrade.session_id == session.id,
        SessionTrade.signal_id == signal.id,
    )
    if await db.scalar(duplicate_stmt) is not None:
        raise HTTPException(status_code=409, detail="Signal is already marked in this session")

    last_trade = await db.scalar(
        select(SessionTrade)
        .where(SessionTrade.user_id == user.id)
        .order_by(desc(SessionTrade.marked_at))
        .limit(1)
    )
    if last_trade is not None:
        elapsed = (datetime.utcnow() - last_trade.marked_at).total_seconds()
        remaining = get_settings().cooldown_seconds - elapsed
        if remaining > 0:
            raise HTTPException(status_code=429, detail={"cooldown_remaining": int(remaining) + 1})

    pnl_delta = session.trade_amount * PAYOUT_RATE if payload.result == "WIN" else -session.trade_amount
    trade = SessionTrade(
        session_id=session.id,
        signal_id=signal.id,
        user_id=user.id,
        result=payload.result,
        trade_amount=session.trade_amount,
        pnl=pnl_delta,
    )
    db.add(trade)

    session.total_trades += 1
    if payload.result == "WIN":
        session.wins += 1
    else:
        session.losses += 1
    session.pnl += pnl_delta
    session.goal_reached = session.pnl >= session.goal_amount

    await db.commit()
    await db.refresh(session)
    await db.refresh(trade)
    return SessionMarkResponse(session=session, trade=trade, pnl_delta=pnl_delta, goal_reached=session.goal_reached)


@router.get("/history", response_model=list[SessionSchema])
async def get_session_history(
    user: User = Depends(get_current_subscribed_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[TradingSession]:
    stmt = (
        select(TradingSession)
        .where(TradingSession.user_id == user.id)
        .order_by(desc(TradingSession.started_at))
        .limit(limit)
        .offset(offset)
    )
    return list((await db.scalars(stmt)).all())


@router.get("/stats", response_model=SessionStatsResponse)
async def get_session_stats(
    user: User = Depends(get_current_subscribed_user),
    db: AsyncSession = Depends(get_db),
    period: str = Query(default="all", pattern="^(week|month|all)$"),
) -> SessionStatsResponse:
    sessions_stmt = select(TradingSession).where(TradingSession.user_id == user.id)
    trades_stmt = select(SessionTrade).where(SessionTrade.user_id == user.id).order_by(SessionTrade.marked_at)
    if period != "all":
        days = 7 if period == "week" else 30
        cutoff = datetime.utcnow().timestamp() - (days * 86400)
        cutoff_dt = datetime.fromtimestamp(cutoff)
        sessions_stmt = sessions_stmt.where(TradingSession.started_at >= cutoff_dt)
        trades_stmt = trades_stmt.where(SessionTrade.marked_at >= cutoff_dt)

    sessions = list((await db.scalars(sessions_stmt)).all())
    trades = list((await db.scalars(trades_stmt)).all())

    wins = sum(1 for trade in trades if trade.result == "WIN")
    losses = sum(1 for trade in trades if trade.result == "LOSS")
    total_trades = wins + losses
    total_pnl = sum((trade.pnl for trade in trades), Decimal("0.00"))

    best_streak = 0
    current_streak = 0
    for trade in trades:
        if trade.result == "WIN":
            current_streak += 1
            best_streak = max(best_streak, current_streak)
        else:
            current_streak = 0

    return SessionStatsResponse(
        total_sessions=len(sessions),
        total_trades=total_trades,
        wins=wins,
        losses=losses,
        winrate=round((wins / total_trades) * 100, 2) if total_trades else 0.0,
        total_pnl=total_pnl,
        best_streak=best_streak,
    )

