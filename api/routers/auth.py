import hmac
import json
from datetime import datetime
from hashlib import sha256
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import create_access_token
from api.models.database import get_db
from api.models.user import User
from api.schemas import AuthResponse, AuthTelegramRequest
from config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


def verify_telegram_init_data(init_data: str, bot_token: str) -> dict[str, str]:
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Telegram initData hash is missing")

    auth_date = parsed.get("auth_date")
    if auth_date:
        age_seconds = datetime.utcnow().timestamp() - int(auth_date)
        if age_seconds > 86400:
            raise HTTPException(status_code=401, detail="Telegram initData expired")

    data_check = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), sha256).digest()
    computed_hash = hmac.new(secret_key, data_check.encode(), sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram initData")
    return parsed


@router.post("/telegram", response_model=AuthResponse)
async def auth_telegram(payload: AuthTelegramRequest, db: AsyncSession = Depends(get_db)) -> AuthResponse:
    settings = get_settings()
    parsed = verify_telegram_init_data(payload.initData, settings.bot_token)

    raw_user = parsed.get("user")
    if not raw_user:
        raise HTTPException(status_code=400, detail="Telegram user payload is missing")

    tg_user = json.loads(raw_user)
    user_id = int(tg_user["id"])
    user = await db.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            username=tg_user.get("username"),
            first_name=tg_user.get('first_name'),
            full_name=' '.join(x for x in [tg_user.get('first_name'), tg_user.get('last_name')] if x),
            language_code=tg_user.get('language_code'),
        )
        db.add(user)
    else:
        user.username = tg_user.get("username")
        user.first_name = tg_user.get('first_name')
        user.full_name = ' '.join(x for x in [tg_user.get('first_name'), tg_user.get('last_name')] if x)
        user.language_code = tg_user.get('language_code')

    await db.commit()
    await db.refresh(user)
    return AuthResponse(access_token=create_access_token(user.id, settings), user=user)

