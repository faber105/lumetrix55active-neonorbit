from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.db_models import MLState, UserSettings, utcnow
from backend.services.database import get_db
from backend.telegram_auth import TelegramMiniAppUser, telegram_user

router = APIRouter()


class Update(BaseModel):
    vip_enabled: bool | None = None
    notification_frequency: str | None = None
    signal_mode: str | None = None


class TimezoneUpdate(BaseModel):
    name: str | None = None
    offset_minutes: int


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
async def get(telegram_id: int, db: AsyncSession = Depends(get_db)):
    return ser(await row(db, telegram_id))


@router.patch('/user/{telegram_id}')
async def patch(telegram_id: int, data: Update, db: AsyncSession = Depends(get_db)):
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
    # Browser timezone is the only reliable way to know the user's device time.
    # Telegram Bot API itself does not expose a user's timezone.
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
