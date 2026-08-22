from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.models.db_models import utcnow
from backend.services.control import get_control, update_control
from backend.services.pocketoption_otc import DISPLAY_TO_ASSET
from backend.services.worker_protocol import await_command, enqueue_command, ensure_demo_account, owns_lease
from backend.telegram_auth import TelegramMiniAppUser, admin_user, telegram_user

router = APIRouter()
MANUAL_TIMEFRAMES = {'15s', '1m', '3m', '5m', '15m'}


class AnalyzeRequest(BaseModel):
    pair: str
    timeframe: str = '1m'


def _is_local_worker(account_id: int) -> bool:
    if str(os.getenv('APP_RUNTIME_ROLE') or '').strip().lower() != 'worker':
        return False
    try:
        return int(os.getenv('WORKER_ACCOUNT_ID') or 0) == int(account_id)
    except (TypeError, ValueError):
        return False


@router.post('/analyze')
async def analyze(req: AnalyzeRequest, user: TelegramMiniAppUser = Depends(telegram_user)):
    if req.timeframe not in MANUAL_TIMEFRAMES:
        raise HTTPException(400, 'Unsupported timeframe')
    if req.pair.replace(' OTC', '').strip() not in DISPLAY_TO_ASSET:
        raise HTTPException(400, 'Unsupported OTC pair')
    try:
        account_id = await ensure_demo_account(int(user.id))
        if _is_local_worker(account_id) and await owns_lease(account_id):
            from backend.services.manual_worker_tasks import analyze_market

            return await analyze_market(req.model_dump())
        command = await enqueue_command(
            account_id=account_id,
            command_type='ANALYZE_SIGNAL',
            payload=req.model_dump(),
            idempotency_key=f'manual:{int(user.id)}:{req.pair}:{req.timeframe}:{int(utcnow().timestamp() // 3)}',
        )
        return await await_command(int(command['id']), account_id)
    except TimeoutError as exc:
        raise HTTPException(503, 'Windows worker is not responding') from exc
    except RuntimeError as exc:
        raise HTTPException(503, 'Windows worker could not analyze the market') from exc


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
    control = await get_control()
    if control is None:
        raise HTTPException(503, 'VIP control is not configured')
    now = utcnow()
    await update_control(
        vip_enabled=True,
        next_vip_at=now,
        last_vip_status='QUEUED_FOR_WORKER',
    )
    return {
        'status': 'QUEUED',
        'worker_driven': True,
        'timeframe': '5m',
        'strategy': 'VIP 5M Confluence',
        'queued_at': now.isoformat() + 'Z',
    }
