from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import get_db
from api.models.user import User
from config import get_settings

router = APIRouter(tags=['verification'])
ALLOWED = {'NEW', 'CLICKED', 'PENDING', 'VERIFIED', 'BLOCKED'}


class VerificationStatusUpdate(BaseModel):
    telegram_id: int
    status: str


@router.get('/go')
async def track_registration_click(uid: int = Query(...), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, uid)
    if user is None:
        raise HTTPException(404, 'Пользователь не найден. Сначала напишите /start боту.')
    if user.verification_status == 'BLOCKED' or user.is_banned:
        raise HTTPException(403, 'Доступ заблокирован.')
    if user.verification_status == 'NEW':
        user.verification_status = 'CLICKED'
        user.click_time = datetime.utcnow()
        await db.commit()
    return RedirectResponse(url=get_settings().referral_url, status_code=302)


@router.get('/verification/user/{telegram_id}')
async def verification_user(telegram_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    user = await db.get(User, telegram_id)
    if user is None:
        raise HTTPException(404, 'Пользователь не найден')
    return _serialize(user)


@router.post('/verification/attempt/{telegram_id}')
async def verification_attempt(telegram_id: int, token: str = Query(...), db: AsyncSession = Depends(get_db)) -> dict:
    if token != get_settings().admin_token:
        raise HTTPException(403, 'Admin token required')
    user = await db.get(User, telegram_id)
    if user is None:
        raise HTTPException(404, 'Пользователь не найден')
    user.attempts_count += 1
    await db.commit()
    return {'attempts_count': user.attempts_count}


@router.post('/verification/status')
async def verification_status(payload: VerificationStatusUpdate, token: str = Query(...), db: AsyncSession = Depends(get_db)) -> dict:
    if token != get_settings().admin_token:
        raise HTTPException(403, 'Admin token required')
    if payload.status not in ALLOWED:
        raise HTTPException(400, f'Invalid status: {payload.status}')
    user = await db.get(User, payload.telegram_id)
    if user is None:
        raise HTTPException(404, 'Пользователь не найден')
    user.verification_status = payload.status
    if payload.status == 'PENDING':
        user.pending_time = datetime.utcnow()
    elif payload.status == 'VERIFIED':
        user.verified_time = datetime.utcnow()
        user.is_banned = False
    elif payload.status == 'BLOCKED':
        user.is_banned = True
    elif payload.status == 'NEW':
        user.is_banned = False
    await db.commit()
    return {'ok': True, 'telegram_id': user.id, 'status': user.verification_status}


@router.get('/verification/users')
async def verification_users(token: str = Query(...), db: AsyncSession = Depends(get_db)) -> list[dict]:
    if token != get_settings().admin_token:
        raise HTTPException(403, 'Admin token required')
    users = list((await db.scalars(select(User).order_by(desc(User.created_at)).limit(200))).all())
    return [_serialize(user) for user in users]


def _serialize(user: User) -> dict:
    return {
        'telegram_id': user.id,
        'username': user.username,
        'full_name': user.full_name or user.first_name,
        'status': user.verification_status,
        'click_time': user.click_time,
        'pending_time': user.pending_time,
        'verified_time': user.verified_time,
        'attempts_count': user.attempts_count,
        'created_at': user.created_at,
    }
