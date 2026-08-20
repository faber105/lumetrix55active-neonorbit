from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.db_models import PaperPosition, Signal, SignalResult, TradeExecution
from backend.services.database import get_db
from backend.services.online_ml import get_model
from backend.services.strategies import STRATEGY_LABELS
from backend.telegram_auth import TelegramMiniAppUser, telegram_user

router = APIRouter()


def _bucket(rows: list[Signal]) -> dict:
    total = len(rows)
    wins = sum(row.result == SignalResult.WIN for row in rows)
    losses = sum(row.result == SignalResult.LOSS for row in rows)
    draws = sum(row.result == SignalResult.DRAW for row in rows)
    pending = sum(row.result == SignalResult.PENDING for row in rows)
    resolved = wins + losses
    return {
        'total': total,
        'wins': wins,
        'losses': losses,
        'draws': draws,
        'pending': pending,
        'winrate': round(wins / resolved * 100, 2) if resolved else None,
    }


def _breakdown(rows: list[Signal], key_fn) -> list[dict]:
    grouped: dict[str, list[Signal]] = defaultdict(list)
    for row in rows:
        grouped[str(key_fn(row))].append(row)
    result = []
    for key, group in grouped.items():
        result.append({'key': key, **_bucket(group)})
    result.sort(key=lambda item: (-item['total'], item['key']))
    return result


async def build(db: AsyncSession, user_id: int):
    rows = (await db.execute(select(Signal).order_by(desc(Signal.created_at)).limit(2000))).scalars().all()
    regular_rows = [row for row in rows if not row.is_vip]
    vip_rows = [row for row in rows if row.is_vip]

    positions = (
        await db.execute(
            select(PaperPosition)
            .where(PaperPosition.telegram_id == user_id)
            .order_by(desc(PaperPosition.created_at))
            .limit(1000)
        )
    ).scalars().all()
    auto_positions = [row for row in positions if row.source == 'auto']
    closed_positions = [row for row in positions if row.status == 'CLOSED']
    position_wins = sum(row.result == SignalResult.WIN for row in closed_positions)
    position_losses = sum(row.result == SignalResult.LOSS for row in closed_positions)
    position_draws = sum(row.result == SignalResult.DRAW for row in closed_positions)

    executions = (
        await db.execute(
            select(TradeExecution)
            .where(TradeExecution.telegram_id == user_id)
            .order_by(desc(TradeExecution.created_at))
            .limit(500)
        )
    ).scalars().all()

    ml = {}
    for key in STRATEGY_LABELS:
        model = get_model(key)
        await model.hydrate()
        ml[key] = model.stats()

    return {
        **_bucket(rows),
        'regular': _bucket(regular_rows),
        'vip': _bucket(vip_rows),
        'trading': {
            'opened': len(positions),
            'closed': len(closed_positions),
            'wins': position_wins,
            'losses': position_losses,
            'draws': position_draws,
            'winrate': round(position_wins / (position_wins + position_losses) * 100, 2)
            if position_wins + position_losses
            else None,
            'auto_opened': len(auto_positions),
            'execution_attempts': len(executions),
            'execution_failures': sum(row.status == 'FAILED' for row in executions),
        },
        'by_strategy': _breakdown(rows, lambda row: row.strategy),
        'by_pair': _breakdown(rows, lambda row: row.pair)[:12],
        'by_timeframe': _breakdown(rows, lambda row: row.timeframe),
        'ml': ml,
    }


@router.get('')
async def root(user: TelegramMiniAppUser = Depends(telegram_user), db: AsyncSession = Depends(get_db)):
    return await build(db, user.id)


@router.get('/summary')
async def summary(user: TelegramMiniAppUser = Depends(telegram_user), db: AsyncSession = Depends(get_db)):
    return await build(db, user.id)
