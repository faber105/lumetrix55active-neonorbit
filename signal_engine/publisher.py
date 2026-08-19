from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import Bot
from sqlalchemy import desc, select

from api.models.database import AsyncSessionLocal
from api.models.signal import Signal
from api.models.subscription import Subscription
from api.models.user import User
from config import get_settings
from signal_engine.otc_engine import OTCAnalysis
from signal_engine.otc_provider import display_asset

logger = logging.getLogger(__name__)


def _local_time(value: datetime) -> str:
    settings = get_settings()
    zone = ZoneInfo(settings.signal_timezone)
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return aware.astimezone(zone).strftime('%H:%M:%S')


def format_signal_message(signal: Signal, reason: str = '') -> str:
    direction = 'BUY / CALL' if signal.direction == 'CALL' else 'SELL / PUT'
    arrow = '🟢' if signal.direction == 'CALL' else '🔴'
    result = (
        f'<b>AlphaPulse OTC Signal</b>\n\n'
        f'Актив: <b>{display_asset(signal.asset)}</b>\n'
        f'Направление: <b>{arrow} {direction}</b>\n'
        f'Стратегия: <b>{signal.strategy}</b>\n'
        f'Режим рынка: {signal.market_regime}\n'
        f'Таймфрейм: {signal.timeframe}\n'
        f'Уверенность: {signal.confidence:.0%}\n\n'
        f'⏰ Вход: <b>{_local_time(signal.entry_time)}</b> ({get_settings().signal_timezone})\n'
        f'⌛ Экспирация: <b>{_local_time(signal.expires_at)}</b>\n'
        f'Цена при анализе: {signal.open_price or "-"}\n'
    )
    if reason:
        result += f'\nПочему: <i>{reason}</i>\n'
    result += '\n⚠️ Сигнал вероятностный. Бот не открывает сделки автоматически.'
    return result


async def _recipient_ids(db) -> list[int]:
    settings = get_settings()
    verified = select(User.id).where(User.verification_status == 'VERIFIED', User.is_banned.is_(False))
    if not settings.require_subscription_for_signals:
        return list((await db.scalars(verified)).all())
    now = datetime.utcnow()
    stmt = select(User.id).join(Subscription, Subscription.user_id == User.id).where(User.verification_status == 'VERIFIED',User.is_banned.is_(False),Subscription.is_active.is_(True),Subscription.expires_at > now).distinct()
    return list((await db.scalars(stmt)).all())


async def publish_analysis(bot: Bot, analysis: OTCAnalysis, *, agent_id: str = 'otc_scanner') -> Signal | None:
    if analysis.status != 'SIGNAL' or not analysis.direction or not analysis.entry_time or not analysis.expires_at:
        return None
    settings = get_settings()
    now = datetime.utcnow()
    async with AsyncSessionLocal() as db:
        active_count = len((await db.scalars(select(Signal.id).where(Signal.result == 'PENDING', Signal.expires_at > now, Signal.requested_by_user_id.is_(None)))).all())
        if active_count >= settings.max_active_signals:
            return None
        last = await db.scalar(select(Signal).where(Signal.asset == analysis.asset, Signal.timeframe == analysis.timeframe, Signal.requested_by_user_id.is_(None)).order_by(desc(Signal.created_at)).limit(1))
        if last and (now - last.created_at).total_seconds() < settings.signal_cooldown_seconds:
            return None
        signal = Signal(asset=analysis.asset,asset_category='otc',direction=analysis.direction,timeframe=analysis.timeframe,duration_sec=int((analysis.expires_at-analysis.entry_time).total_seconds()),open_price=Decimal(str(analysis.entry_price_reference)) if analysis.entry_price_reference is not None else None,close_price=None,confidence=analysis.confidence,indicator_score=(analysis.strategy_score*(1 if analysis.direction=='CALL' else -1)),ml_confidence=analysis.ml_confidence,strategy=analysis.strategy,market_regime=analysis.regime,data_source='pocketoption_otc_websocket',feature_snapshot=analysis.features,created_at=now,entry_time=analysis.entry_time,expires_at=analysis.expires_at,result='PENDING',agent_id=agent_id,requested_by_user_id=None)
        db.add(signal)
        await db.commit()
        await db.refresh(signal)
        recipients = await _recipient_ids(db)
    text = format_signal_message(signal, analysis.reason)
    for user_id in recipients:
        try:
            await bot.send_message(user_id, text)
        except Exception as exc:
            logger.warning('Could not send signal %s to %s: %s', signal.id, user_id, exc)
    return signal
