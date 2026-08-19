from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from backend.models.db_models import AsyncSessionLocal, PaperPosition, Signal, utcnow
from backend.routers.signals import out, save_candidate
from backend.services.control import (
    VALID_STRATEGIES,
    VALID_TIMEFRAMES,
    get_control,
    serialize_control,
    update_control,
)
from backend.services.pocketoption_otc import MarketDataUnavailable, OTC_ASSETS, market_data
from backend.services.scanner import notify_signal
from backend.services.signal_engine import signal_engine
from backend.telegram_auth import TelegramMiniAppUser, admin_user

router = APIRouter()
VIP_CONFIDENCE = 80.0
REGULAR_CONFIDENCE = 72.0


class ControlPatch(BaseModel):
    selected_strategy: str | None = None
    selected_timeframe: str | None = None
    regular_enabled: bool | None = None
    vip_enabled: bool | None = None
    vip_interval_seconds: int | None = None


async def _scan_and_publish(*, vip: bool) -> dict:
    control = await get_control()
    if control is None:
        raise HTTPException(503, 'Admin control is not configured')
    try:
        candidate = await signal_engine.scan_strategy(
            control.selected_timeframe,
            list(OTC_ASSETS.keys()),
            control.selected_strategy,
        )
    except MarketDataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

    threshold = VIP_CONFIDENCE if vip else REGULAR_CONFIDENCE
    if not candidate or float(candidate.get('confidence') or 0) < threshold:
        if vip:
            now = utcnow()
            await update_control(
                last_vip_at=now,
                last_vip_status='NO_CONFIRMED_SETUP',
                next_vip_at=now + timedelta(seconds=max(60, int(control.vip_interval_seconds or 300))),
                last_scan_at=now,
            )
        return {
            'status': 'NO_SIGNAL',
            'vip': vip,
            'strategy': control.selected_strategy,
            'timeframe': control.selected_timeframe,
            'threshold': threshold,
        }

    if vip and float(candidate.get('confidence') or 0) < VIP_CONFIDENCE:
        return {'status': 'NO_SIGNAL', 'vip': True}

    async with AsyncSessionLocal() as db:
        row, duplicate = await save_candidate(db, candidate)
        signal = out(row)

    notification = {'notified': 0, 'notification_errors': 0}
    if not duplicate:
        from bot.main import bot
        notification = await notify_signal(bot, signal)

    now = utcnow()
    if vip:
        await update_control(
            last_vip_at=now,
            last_vip_status='DUPLICATE' if duplicate else 'ISSUED',
            next_vip_at=now + timedelta(seconds=max(60, int(control.vip_interval_seconds or 300))),
            last_scan_at=now,
        )
    else:
        await update_control(last_scan_at=now)

    return {
        'status': 'SIGNAL',
        'vip': bool(signal.get('is_vip')),
        'duplicate': duplicate,
        'signal': signal,
        **notification,
    }


@router.get('/state')
async def state(_: TelegramMiniAppUser = Depends(admin_user)):
    control = await get_control()
    market = await market_data.health()
    async with AsyncSessionLocal() as db:
        latest = (
            await db.execute(select(Signal).order_by(desc(Signal.created_at)).limit(1))
        ).scalar_one_or_none()
        open_positions = int(
            (
                await db.execute(
                    select(func.count()).select_from(PaperPosition).where(PaperPosition.status == 'OPEN')
                )
            ).scalar_one()
            or 0
        )
    payload = serialize_control(control)
    payload.update({
        'market': market,
        'open_positions': open_positions,
        'latest_signal': out(latest) if latest else None,
    })
    if control and control.next_vip_at:
        payload['vip_seconds_remaining'] = max(0, int((control.next_vip_at - utcnow()).total_seconds()))
    else:
        payload['vip_seconds_remaining'] = None
    return payload


@router.patch('/state')
async def patch_state(
    data: ControlPatch,
    _: TelegramMiniAppUser = Depends(admin_user),
):
    changes = data.model_dump(exclude_none=True)
    if 'selected_strategy' in changes and changes['selected_strategy'] not in VALID_STRATEGIES:
        raise HTTPException(400, 'Unknown strategy')
    if 'selected_timeframe' in changes and changes['selected_timeframe'] not in VALID_TIMEFRAMES:
        raise HTTPException(400, 'Unknown timeframe')
    if 'vip_interval_seconds' in changes:
        changes['vip_interval_seconds'] = max(60, min(86400, int(changes['vip_interval_seconds'])))
    control = await update_control(**changes)
    return serialize_control(control)


@router.post('/scan-now')
async def scan_now(_: TelegramMiniAppUser = Depends(admin_user)):
    return await _scan_and_publish(vip=False)


@router.post('/vip-now')
async def vip_now(_: TelegramMiniAppUser = Depends(admin_user)):
    return await _scan_and_publish(vip=True)
