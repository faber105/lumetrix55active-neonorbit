from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class TelegramMiniAppUser:
    id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


def _telegram_hash(token: str, values: dict[str, str]) -> str:
    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()


def _verify_init_data(init_data: str) -> TelegramMiniAppUser:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise HTTPException(503, "Telegram auth is not configured")
    if not init_data:
        raise HTTPException(401, "Telegram Mini App initData required")

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise HTTPException(401, "Telegram initData hash missing")

    # Bot API 9.x Mini Apps may include the new `signature` field in initData.
    # For bot-token HMAC validation Telegram's current data-check-string contains
    # all received fields except `hash`, so `signature` must not be discarded.
    # Keep a legacy variant as a compatibility fallback for older clients that
    # generated the hash before the signature field was introduced.
    expected_hash = _telegram_hash(token, values)
    valid = hmac.compare_digest(expected_hash, received_hash)
    if not valid and "signature" in values:
        legacy_values = dict(values)
        legacy_values.pop("signature", None)
        valid = hmac.compare_digest(_telegram_hash(token, legacy_values), received_hash)
    if not valid:
        raise HTTPException(401, "Invalid Telegram Mini App initData")

    try:
        auth_date = int(values.get("auth_date", "0") or 0)
    except ValueError as exc:
        raise HTTPException(401, "Invalid Telegram auth date") from exc
    max_age = int(os.getenv("TELEGRAM_INITDATA_MAX_AGE", "86400"))
    if auth_date <= 0 or abs(int(time.time()) - auth_date) > max_age:
        raise HTTPException(401, "Telegram Mini App session expired")

    try:
        payload = json.loads(values.get("user", "{}"))
        user_id = int(payload["id"])
    except Exception as exc:
        raise HTTPException(401, "Telegram user missing from initData") from exc

    return TelegramMiniAppUser(
        id=user_id,
        username=payload.get("username"),
        first_name=payload.get("first_name"),
        last_name=payload.get("last_name"),
    )


async def telegram_user(
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> TelegramMiniAppUser:
    return _verify_init_data(x_telegram_init_data or "")


async def admin_user(
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> TelegramMiniAppUser:
    user = _verify_init_data(x_telegram_init_data or "")
    admin_id = int(os.getenv("ADMIN_ID", "0") or 0)
    if admin_id <= 0 or user.id != admin_id:
        raise HTTPException(403, "Admin access required")
    return user
