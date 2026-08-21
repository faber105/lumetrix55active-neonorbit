from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

EXPECTED_BOT_ID = "8347664656"
DEFAULT_BASE_URL = "https://lumetrix55active-neonorbit.vercel.app"
DIAG_KEY = "__vercel_build_telegram_diag__"
TOKEN_KEY = "__runtime_telegram_bot__"


def _safe_error(exc: Exception, token: str) -> str:
    text = str(exc or "")
    if token:
        text = text.replace(token, "<redacted>")
    return text[:500]


def _api(token: str, method: str, data: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    encoded = urllib.parse.urlencode(data or {}).encode("utf-8") if data is not None else None
    request = urllib.request.Request(url, data=encoded, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        raise RuntimeError(f"Telegram {method} HTTP {exc.code}: {raw[:250]}") from None
    body = json.loads(raw)
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} returned ok=false: {str(body.get('description') or '')[:250]}")
    return body


async def _db_connect():
    import asyncpg
    from sqlalchemy.engine import make_url

    raw = str(os.getenv("DATABASE_URL") or "").strip()
    if not raw:
        return None
    url = make_url(raw)
    return await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
        ssl="require" if url.host and "neon.tech" in url.host else None,
        statement_cache_size=0,
        timeout=12,
    )


async def _load_token_from_neon() -> str:
    conn = await _db_connect()
    if conn is None:
        return ""
    try:
        value = await conn.fetchval("SELECT payload FROM ml_state WHERE strategy=$1", TOKEN_KEY)
        return str(value or "").strip()
    finally:
        await conn.close()


async def _persist_diag(payload: dict) -> None:
    try:
        conn = await _db_connect()
        if conn is None:
            return
        try:
            body = json.dumps(
                {"at": datetime.now(timezone.utc).isoformat(), **payload},
                ensure_ascii=False,
            )
            await conn.execute(
                """
                INSERT INTO ml_state(strategy, payload, samples, updated_at)
                VALUES($1, $2, 0, NOW())
                ON CONFLICT(strategy)
                DO UPDATE SET payload=EXCLUDED.payload, updated_at=NOW()
                """,
                DIAG_KEY,
                body,
            )
        finally:
            await conn.close()
    except Exception as exc:
        print(f"Cannot persist Telegram build diagnostic: {type(exc).__name__}: {exc}", file=sys.stderr)


def _run_setup(token: str, token_source: str) -> dict:
    diag = {
        "token_present": bool(token),
        "token_source": token_source,
        "token_bot_id": token.split(":", 1)[0] if token and ":" in token else (token[:20] if token else None),
        "database_present": bool(str(os.getenv("DATABASE_URL") or "").strip()),
        "backend_env_present": bool(str(os.getenv("BACKEND_URL") or os.getenv("PUBLIC_BACKEND_URL") or "").strip()),
        "success": False,
    }

    if not token:
        diag.update(error_type="MissingToken", error="Telegram token is missing in both Vercel env and Neon runtime store")
        return diag

    if diag["token_bot_id"] != EXPECTED_BOT_ID:
        diag.update(error_type="WrongBotId", error=f"token belongs to bot id {diag['token_bot_id']!r}")
        return diag

    explicit = str(os.getenv("PUBLIC_BACKEND_URL") or os.getenv("BACKEND_URL") or "").strip().rstrip("/")
    base_url = explicit or DEFAULT_BASE_URL
    diag["base_url"] = base_url
    webhook_url = f"{base_url}/api/telegram/webhook"
    diag["target_webhook"] = webhook_url

    try:
        if not base_url.startswith("https://"):
            raise RuntimeError("BACKEND_URL must be https://")

        secret = hashlib.sha256(f"alphapulsesbot:{token}".encode("utf-8")).hexdigest()
        me = _api(token, "getMe").get("result") or {}
        actual_id = str(me.get("id") or "")
        diag["getme_bot_id"] = actual_id
        diag["getme_username"] = str(me.get("username") or "")[:100] or None
        if actual_id != EXPECTED_BOT_ID:
            raise RuntimeError(f"Telegram getMe returned unexpected bot id {actual_id!r}")

        _api(
            token,
            "setWebhook",
            {
                "url": webhook_url,
                "secret_token": secret,
                "drop_pending_updates": "false",
                "allowed_updates": json.dumps(["message", "callback_query"]),
            },
        )
        info = _api(token, "getWebhookInfo").get("result") or {}
        actual = str(info.get("url") or "")
        diag.update(
            actual_webhook=actual,
            pending_update_count=int(info.get("pending_update_count") or 0),
            last_error_message=str(info.get("last_error_message") or "")[:300] or None,
        )
        if actual != webhook_url:
            raise RuntimeError(f"Webhook mismatch: expected {webhook_url}, got {actual}")
        diag["success"] = True
    except Exception as exc:
        diag.update(error_type=type(exc).__name__, error=_safe_error(exc, token))
    return diag


async def _main_async() -> int:
    env_token = str(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    token = env_token
    source = "vercel_env" if env_token else "none"

    if not token and str(os.getenv("DATABASE_URL") or "").strip():
        try:
            token = await _load_token_from_neon()
            if token:
                source = "neon_runtime_store"
        except Exception as exc:
            print(f"Cannot load Telegram token from Neon: {type(exc).__name__}: {exc}", file=sys.stderr)

    diag = _run_setup(token, source)
    await _persist_diag(diag)
    print("Telegram build diagnostic:", json.dumps(diag, ensure_ascii=False))
    return 0


def main() -> int:
    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
