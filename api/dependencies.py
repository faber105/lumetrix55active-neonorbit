from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import get_db
from api.models.subscription import Subscription
from api.models.user import User
from config import Settings, get_settings

security = HTTPBearer(auto_error=False)


def create_access_token(user_id: int, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    expires_at = datetime.utcnow() + timedelta(hours=cfg.jwt_expires_hours)
    payload = {'sub': str(user_id), 'exp': expires_at}
    return jwt.encode(payload, cfg.jwt_secret, algorithm='HS256')


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing bearer token')
    try:
        payload = jwt.decode(credentials.credentials, get_settings().jwt_secret, algorithms=['HS256'])
        user_id = int(payload['sub'])
    except (JWTError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid bearer token') from exc
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found')
    if user.is_banned or user.verification_status == 'BLOCKED':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User is blocked')
    return user


async def get_current_verified_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    settings = get_settings()
    if user.id == settings.admin_telegram_id:
        return user
    if user.verification_status != 'VERIFIED':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Verification required')
    return user


async def has_active_subscription(db: AsyncSession, user_id: int) -> bool:
    settings = get_settings()
    if user_id == settings.admin_telegram_id or not settings.require_subscription_for_signals:
        return True
    stmt = select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.is_active.is_(True),
        Subscription.expires_at > datetime.utcnow(),
    ).limit(1)
    return await db.scalar(stmt) is not None


async def get_current_subscribed_user(
    user: Annotated[User, Depends(get_current_verified_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if not await has_active_subscription(db, user.id):
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail='Active subscription required')
    return user


async def require_admin_token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]) -> None:
    if credentials is None or credentials.credentials != get_settings().admin_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Admin token required')
