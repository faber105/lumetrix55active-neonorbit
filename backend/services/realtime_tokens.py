from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


def _secret() -> bytes:
    value = str(os.getenv("WORKER_SHARED_SECRET") or "").strip()
    if len(value) < 32:
        raise RuntimeError("WORKER_SHARED_SECRET must contain at least 32 characters")
    return value.encode("utf-8")


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_realtime_token(*, telegram_id: int, account_id: int, ttl_seconds: int = 60) -> str:
    now = int(time.time())
    payload = {
        "sub": int(telegram_id),
        "account_id": int(account_id),
        "iat": now,
        "exp": now + max(15, min(120, int(ttl_seconds))),
        "scope": "realtime:read",
    }
    body = _encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _encode(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_realtime_token(token: str) -> dict[str, Any]:
    try:
        body, signature = token.split(".", 1)
        expected = _encode(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        payload = json.loads(_decode(body))
        now = int(time.time())
        if payload.get("scope") != "realtime:read" or int(payload.get("exp") or 0) < now:
            raise ValueError("expired or invalid scope")
        if int(payload.get("iat") or 0) > now + 30:
            raise ValueError("token issued in the future")
        payload["sub"] = int(payload["sub"])
        payload["account_id"] = int(payload["account_id"])
        return payload
    except Exception as exc:
        raise ValueError("Invalid realtime token") from exc
