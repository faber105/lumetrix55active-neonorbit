from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request

EXPECTED_BOT_ID = "8347664656"
DEFAULT_BASE_URL = "https://lumetrix55active-neonorbit.vercel.app"


def _api(token: str, method: str, data: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    encoded = urllib.parse.urlencode(data or {}).encode("utf-8") if data is not None else None
    request = urllib.request.Request(url, data=encoded, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed")
    return body


def main() -> int:
    token = str(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in Vercel Production environment")
    token_bot_id = token.split(":", 1)[0]
    if token_bot_id != EXPECTED_BOT_ID:
        raise RuntimeError(f"TELEGRAM_BOT_TOKEN belongs to unexpected bot id {token_bot_id!r}")

    explicit = str(os.getenv("PUBLIC_BACKEND_URL") or os.getenv("BACKEND_URL") or "").strip().rstrip("/")
    base_url = explicit or DEFAULT_BASE_URL
    if not base_url.startswith("https://"):
        raise RuntimeError("BACKEND_URL must be https://")

    # api/index.py is Vercel's FastAPI catch-all for /api/*.
    webhook_url = f"{base_url}/api/telegram/webhook"
    secret = hashlib.sha256(f"alphapulsesbot:{token}".encode("utf-8")).hexdigest()

    me = _api(token, "getMe").get("result") or {}
    actual_id = str(me.get("id") or "")
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
    if actual != webhook_url:
        raise RuntimeError(f"Webhook mismatch: expected {webhook_url}, got {actual}")

    print(
        "Telegram webhook verified:",
        json.dumps(
            {
                "bot_id": actual_id,
                "url": actual,
                "pending_update_count": int(info.get("pending_update_count") or 0),
                "last_error_message": info.get("last_error_message"),
            },
            ensure_ascii=False,
        ),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Telegram webhook build setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
