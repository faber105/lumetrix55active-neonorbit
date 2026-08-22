from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException


BUILTIN_ADMIN_IDS: set[int] = set()


def admin_ids() -> set[int]:
    values = {int(os.getenv("ADMIN_ID", "0") or 0), *BUILTIN_ADMIN_IDS}
    for raw in (os.getenv("ADMIN_IDS", ""), os.getenv("SECONDARY_ADMIN_IDS", "")):
        for item in raw.replace(";", ",").split(","):
            try:
                values.add(int(item.strip()))
            except (TypeError, ValueError):
                continue
    return {value for value in values if value > 0}


def is_admin_id(telegram_id: int) -> bool:
    return int(telegram_id) in admin_ids()


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

    default_age = 86400 if str(os.getenv("APP_RUNTIME_ROLE") or "").strip().lower() == "worker" else 3600
    try:
        configured_age = int(os.getenv("TELEGRAM_INITDATA_MAX_AGE", str(default_age)) or default_age)
    except ValueError:
        configured_age = default_age
    max_age = max(300, min(604800, configured_age))
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


async def _sole_worker_broker_owner(telegram_id: int) -> bool:
    """Authorize the owner of the broker account actually leased by this worker.

    Old test/duplicate broker rows must not lock the real Pocket owner out of AUTO.
    The lease is the authoritative ownership boundary for the persistent Windows
    broker process, so unrelated ACTIVE rows are ignored.
    """
    try:
        from sqlalchemy import text
        from backend.models.db_models import AsyncSessionLocal
        from backend.services.worker_protocol import worker_id

        async with AsyncSessionLocal() as db:
            owner = (
                await db.execute(
                    text("""SELECT b.owner_telegram_id
                        FROM worker_leases l
                        JOIN broker_accounts b ON b.id=l.account_id
                        WHERE l.worker_id=:worker
                          AND l.lease_until>NOW()
                          AND b.status='ACTIVE'
                        ORDER BY l.updated_at DESC
                        LIMIT 1"""),
                    {"worker": worker_id()},
                )
            ).scalar_one_or_none()
        return owner is not None and int(owner) == int(telegram_id)
    except Exception:
        return False


async def admin_user(
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> TelegramMiniAppUser:
    user = _verify_init_data(x_telegram_init_data or "")
    if is_admin_id(user.id) or await _sole_worker_broker_owner(user.id):
        return user
    raise HTTPException(403, "Admin access required")
