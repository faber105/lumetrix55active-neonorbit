from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

TOKEN_KEY = "__runtime_telegram_bot__"
DIAG_KEY = "__telegram_gateway_diag__"
DEFAULT_BASE_URL = "https://lumetrix55active-neonorbit.vercel.app"
_TOKEN_CACHE = ""


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


async def _load_token() -> str:
    global _TOKEN_CACHE
    env_token = str(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    if env_token:
        _TOKEN_CACHE = env_token
        return env_token
    if _TOKEN_CACHE:
        return _TOKEN_CACHE
    conn = await _db_connect()
    if conn is None:
        return ""
    try:
        value = await conn.fetchval("SELECT payload FROM ml_state WHERE strategy=$1", TOKEN_KEY)
        _TOKEN_CACHE = str(value or "").strip()
        return _TOKEN_CACHE
    finally:
        await conn.close()


async def _write_diag(payload: dict) -> None:
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
    except Exception:
        pass


def _webhook_secret(token: str) -> str:
    return hashlib.sha256(f"alphapulsesbot:{token}".encode("utf-8")).hexdigest()


def _legacy_secret(token: str) -> str:
    return hashlib.sha256(f"alphapulse-webhook:{token}".encode("utf-8")).hexdigest()


def _valid_secret(value: str, token: str) -> bool:
    if not value or not token:
        return False
    return hmac.compare_digest(value, _webhook_secret(token)) or hmac.compare_digest(value, _legacy_secret(token))


def _telegram_send(token: str, chat_id: int, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "HTML",
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


async def _process_update(payload: dict, token: str) -> dict:
    message = payload.get("message") if isinstance(payload, dict) else None
    text = str((message or {}).get("text") or "") if isinstance(message, dict) else ""
    chat = (message or {}).get("chat") if isinstance(message, dict) else None
    chat_id = int((chat or {}).get("id") or 0) if isinstance(chat, dict) else 0
    is_start = text.startswith("/start")

    os.environ["TELEGRAM_BOT_TOKEN"] = token
    os.environ["BOT_TOKEN"] = token
    os.environ["BACKEND_URL"] = str(os.getenv("BACKEND_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    os.environ["MINI_APP_URL"] = f"{os.environ['BACKEND_URL']}?v=20260821-2450"

    try:
        from bot.main import feed_update

        await feed_update(payload)
        await _write_diag(
            {
                "ok": True,
                "received": True,
                "handled": True,
                "is_start": is_start,
                "update_id": payload.get("update_id"),
                "chat_id": chat_id or None,
            }
        )
        return {"ok": True, "handled": True}
    except Exception as exc:
        await _write_diag(
            {
                "ok": False,
                "received": True,
                "handled": False,
                "is_start": is_start,
                "update_id": payload.get("update_id"),
                "chat_id": chat_id or None,
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }
        )
        # Telegram must not keep retrying the same update forever. For /start,
        # send a minimal fallback so the user gets an immediate response while
        # the deeper backend error is repaired from the stored diagnostic.
        if is_start and chat_id:
            try:
                await asyncio.to_thread(
                    _telegram_send,
                    token,
                    chat_id,
                    "⚡ <b>AlphaPulse</b> запущен.\n\nСервис восстанавливает подключение к Mini App. Попробуйте /start ещё раз через несколько секунд.",
                )
            except Exception:
                pass
        return {"ok": True, "handled": False}


class handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            token = asyncio.run(_load_token())
            if not token:
                self._json(503, {"ok": False, "error": "telegram_not_configured"})
                return

            supplied = str(self.headers.get("X-Telegram-Bot-Api-Secret-Token") or "")
            if not _valid_secret(supplied, token):
                asyncio.run(_write_diag({"ok": False, "received": True, "error": "invalid_secret"}))
                self._json(403, {"ok": False, "error": "invalid_secret"})
                return

            result = asyncio.run(_process_update(payload, token))
            self._json(200, result)
        except Exception as exc:
            try:
                asyncio.run(
                    _write_diag(
                        {
                            "ok": False,
                            "received": False,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:500],
                        }
                    )
                )
            except Exception:
                pass
            # Return 200 so Telegram does not create an endless retry storm.
            self._json(200, {"ok": True, "handled": False})
