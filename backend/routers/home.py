from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.models.db_models import utcnow
from backend.services.control import get_control, update_control
from backend.services.pocketoption_otc import DISPLAY_TO_ASSET, MarketDataUnavailable, OTC_ASSETS, TF_SECONDS
from backend.services.scanner import notify_signal
from backend.services.signal_engine import signal_engine
from backend.services.signal_store import save_signal
from backend.telegram_auth import TelegramMiniAppUser, admin_user, telegram_user

router = APIRouter()
MANUAL_TIMEFRAMES = {'15s', '1m', '3m', '5m', '15m'}
MIN_MANUAL_CONFIDENCE = 70.0
VIP_CONFIDENCE = 82.0


class AnalyzeRequest(BaseModel):
    pair: str
    timeframe: str = '1m'


@router.post('/analyze')
async def analyze(req: AnalyzeRequest, _: TelegramMiniAppUser = Depends(telegram_user)):
    asset = DISPLAY_TO_ASSET.get(req.pair.replace(' OTC', '').strip())
    if not asset:
        raise HTTPException(400, 'Unsupported OTC pair')
    if req.timeframe not in MANUAL_TIMEFRAMES or req.timeframe not in TF_SECONDS:
        raise HTTPException(400, 'Unsupported timeframe')
    try:
        candidate = await signal_engine.evaluate_asset_composite(asset, req.timeframe)
    except MarketDataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if not candidate or float(candidate.get('confidence') or 0) < MIN_MANUAL_CONFIDENCE:
        return {
            'status': 'NO_SIGNAL',
            'pair': OTC_ASSETS[asset],
            'timeframe': req.timeframe,
            'signal': None,
            'reason': 'Сейчас нет подтверждённой точки входа. Trend, momentum и volatility-фильтры не дали достаточного совпадения.',
        }
    signal, duplicate = await save_signal(candidate, is_vip=False)
    return {
        'status': 'SIGNAL',
        'signal': signal,
        'duplicate': duplicate,
        'analysis': {
            'engine': 'Composite Analysis',
            'strategy': candidate.get('strategy_label'),
            'confirmations': candidate.get('confirmations', []),
            'indicators': candidate.get('indicators', {}),
        },
    }


@router.get('/vip-status')
async def vip_status(_: TelegramMiniAppUser = Depends(telegram_user)):
    control = await get_control()
    if control is None:
        return {
            'enabled': False,
            'interval_seconds': 300,
            'next_vip_at': None,
            'seconds_remaining': None,
            'last_status': None,
        }
    remaining = max(0, int((control.next_vip_at - utcnow()).total_seconds())) if control.next_vip_at else 0
    return {
        'enabled': bool(control.vip_enabled),
        'interval_seconds': int(control.vip_interval_seconds or 300),
        'next_vip_at': control.next_vip_at.isoformat() + 'Z' if control.next_vip_at else None,
        'seconds_remaining': remaining,
        'last_status': control.last_vip_status,
        'timeframe': '5m',
        'strategy': 'VIP 5M Confluence',
    }


@router.post('/vip-scan-now')
async def vip_scan_now(_: TelegramMiniAppUser = Depends(admin_user)):
    """Run the same 5m VIP engine used by the background scanner.

    The older /admin/vip-now path used the generic selected admin timeframe and
    strategy, so it could accidentally create a 1m signal while calling it VIP.
    This endpoint always runs the real VIP 5m scan and sends the same Telegram
    notification as the scheduled scanner.
    """
    control = await get_control()
    if control is None:
        raise HTTPException(503, 'VIP control is not configured')
    try:
        candidate = await signal_engine.scan_vip(list(OTC_ASSETS.keys()))
    except MarketDataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

    now = utcnow()
    interval = max(60, min(86400, int(control.vip_interval_seconds or 300)))
    next_vip = now + timedelta(seconds=interval)
    if not candidate or float(candidate.get('confidence') or 0) < VIP_CONFIDENCE:
        await update_control(
            vip_enabled=True,
            last_vip_at=now,
            last_vip_status='NO_CONFIRMED_SETUP',
            next_vip_at=next_vip,
            last_scan_at=now,
        )
        return {
            'status': 'NO_SIGNAL',
            'timeframe': '5m',
            'threshold': VIP_CONFIDENCE,
            'next_vip_at': next_vip.isoformat() + 'Z',
        }

    signal, duplicate = await save_signal(candidate, is_vip=True)
    notification = {'notified': 0, 'notification_errors': 0}
    if not duplicate:
        from bot.main import bot
        notification = await notify_signal(bot, signal)
    await update_control(
        vip_enabled=True,
        last_vip_at=now,
        last_vip_status='DUPLICATE' if duplicate else 'ISSUED',
        next_vip_at=next_vip,
        last_scan_at=now,
    )
    return {
        'status': 'DUPLICATE' if duplicate else 'SIGNAL',
        'signal': signal,
        'duplicate': duplicate,
        'next_vip_at': next_vip.isoformat() + 'Z',
        **notification,
    }
