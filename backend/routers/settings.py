from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.db_models import AsyncSessionLocal, MLState, UserSettings, utcnow
from backend.services.database import get_db
from backend.telegram_auth import TelegramMiniAppUser, admin_user, is_admin_id, telegram_user

router = APIRouter()


class Update(BaseModel):
    vip_enabled: bool | None = None
    notification_frequency: str | None = None
    signal_mode: str | None = None


class TimezoneUpdate(BaseModel):
    name: str | None = None
    offset_minutes: int


class PocketCredentialUpdate(BaseModel):
    mode: str
    ssid: str


def ser(s):
    return {
        'telegram_id': s.telegram_id,
        'vip_enabled': s.vip_enabled,
        'notification_frequency': s.notification_frequency,
        'signal_mode': s.signal_mode,
    }


async def row(db, tid):
    s = (
        await db.execute(select(UserSettings).where(UserSettings.telegram_id == tid))
    ).scalar_one_or_none()
    if s is None:
        s = UserSettings(telegram_id=tid)
        db.add(s)
        await db.commit()
        await db.refresh(s)
    return s


@router.get('/user/{telegram_id}')
async def get(
    telegram_id: int,
    user: TelegramMiniAppUser = Depends(telegram_user),
    db: AsyncSession = Depends(get_db),
):
    if int(user.id) != int(telegram_id) and not is_admin_id(user.id):
        raise HTTPException(403, 'Cannot read another user settings')
    return ser(await row(db, telegram_id))


@router.patch('/user/{telegram_id}')
async def patch(
    telegram_id: int,
    data: Update,
    user: TelegramMiniAppUser = Depends(telegram_user),
    db: AsyncSession = Depends(get_db),
):
    if int(user.id) != int(telegram_id) and not is_admin_id(user.id):
        raise HTTPException(403, 'Cannot edit another user settings')
    s = await row(db, telegram_id)
    if data.vip_enabled is not None:
        s.vip_enabled = data.vip_enabled
    if data.notification_frequency in {'rarely', 'standard', 'often'}:
        s.notification_frequency = data.notification_frequency
    if data.signal_mode in {'all', 'vip', 'mixed'}:
        s.signal_mode = data.signal_mode
    await db.commit()
    await db.refresh(s)
    return ser(s)


@router.post('/timezone')
async def save_timezone(
    data: TimezoneUpdate,
    user: TelegramMiniAppUser = Depends(telegram_user),
    db: AsyncSession = Depends(get_db),
):
    offset = max(-840, min(840, int(data.offset_minutes)))
    name = (data.name or '').strip()[:80]
    key = f'__user_tz__:{user.id}'
    payload = json.dumps({'name': name, 'offset_minutes': offset}, separators=(',', ':'))
    state = await db.get(MLState, key)
    if state is None:
        state = MLState(strategy=key, payload=payload, samples=0, updated_at=utcnow())
        db.add(state)
    else:
        state.payload = payload
        state.updated_at = utcnow()
    await db.commit()
    return {'ok': True, 'name': name, 'offset_minutes': offset}


async def _credential_change_is_safe() -> bool:
    async with AsyncSessionLocal() as db:
        active = int((await db.execute(text("SELECT COUNT(*) FROM auto_trade_sessions WHERE status='ACTIVE'"))).scalar_one() or 0)
        positions = int((await db.execute(text("SELECT COUNT(*) FROM auto_trade_legs WHERE result IN ('PENDING','UNKNOWN')"))).scalar_one() or 0)
        executions = int((await db.execute(text("SELECT COUNT(*) FROM trade_executions WHERE status IN ('EXECUTING','UNKNOWN')"))).scalar_one() or 0)
    return not any((active, positions, executions))


@router.get('/pocket-credentials')
async def pocket_credentials(_: TelegramMiniAppUser = Depends(admin_user)):
    from backend.services.pocket_credentials import credential_status
    from backend.services.trade_mode import get_trade_account_mode
    return {
        'selected_mode': await get_trade_account_mode(),
        'credentials': await credential_status(),
    }


@router.post('/pocket-credentials')
async def save_pocket_credentials(
    data: PocketCredentialUpdate,
    _: TelegramMiniAppUser = Depends(admin_user),
):
    mode = str(data.mode or '').strip().lower()
    if mode not in {'demo', 'real'}:
        raise HTTPException(400, 'mode must be demo or real')
    if not await _credential_change_is_safe():
        raise HTTPException(409, 'Stop the active AUTO session before replacing Pocket credentials')

    from backend.services.pocket_credentials import credential_status, save_pocket_credential
    from backend.services.trade_mode import get_trade_account_mode
    try:
        await save_pocket_credential(mode, str(data.ssid or '').strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    selected = await get_trade_account_mode()
    if selected == mode:
        from backend.services.auto_trade import close_demo_trading_client
        from backend.services.pocketoption_otc import market_data
        await close_demo_trading_client()
        await market_data.close()
        await market_data._refresh_private_ssid(force=True)

    return {
        'ok': True,
        'mode': mode,
        'selected_mode': selected,
        'credentials': await credential_status(),
        'reconnect': selected == mode,
    }
