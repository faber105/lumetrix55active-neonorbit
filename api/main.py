from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import asyncpg
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]

# New deployments need only a restricted database bootstrap credential. Telegram,
# admin and Pocket runtime secrets live in Neon and are loaded before bot imports.
bootstrap = ROOT / "runtime_bootstrap.json"
legacy = ROOT / "runtime_secrets.json"
for source in (bootstrap, legacy):
    if source.exists():
        try:
            for key, value in json.loads(source.read_text()).items():
                os.environ.setdefault(str(key), str(value))
        except Exception:
            pass


async def _load_runtime_secrets_from_db() -> None:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        return
    url = make_url(raw)
    conn = None
    try:
        conn = await asyncpg.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            database=url.database,
            ssl="require" if url.host and "neon.tech" in url.host else None,
            statement_cache_size=0,
            timeout=12,
        )
        rows = await conn.fetch(
            "SELECT strategy, payload FROM ml_state WHERE strategy = ANY($1::text[])",
            [
                "__runtime_telegram_bot__",
                "__runtime_admin_secret__",
                "__runtime_admin_id__",
                "__runtime_pocket__",
            ],
        )
        values = {str(row["strategy"]): str(row["payload"] or "") for row in rows}
        bot_token = values.get("__runtime_telegram_bot__", "").strip()
        admin_secret = values.get("__runtime_admin_secret__", "").strip()
        admin_id = values.get("__runtime_admin_id__", "").strip()
        pocket = values.get("__runtime_pocket__", "").strip()
        if bot_token:
            os.environ.setdefault("TELEGRAM_BOT_TOKEN", bot_token)
            os.environ.setdefault("BOT_TOKEN", bot_token)
        if admin_secret:
            os.environ.setdefault("ADMIN_SECRET", admin_secret)
        if admin_id:
            os.environ.setdefault("ADMIN_ID", admin_id)
            os.environ.setdefault("ADMIN_TELEGRAM_ID", admin_id)
        if pocket:
            os.environ.setdefault("POCKET_OPTION_SSID", pocket)
    finally:
        if conn is not None:
            await conn.close()


if os.getenv("DATABASE_URL", "").strip() and not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
    try:
        asyncio.run(_load_runtime_secrets_from_db())
    except Exception as exc:
        # backend.main can still expose /health in degraded mode; logs contain only
        # the exception type and never secret values.
        print(f"AlphaPulse runtime secret bootstrap failed: {type(exc).__name__}")

# Use the minimal read-only Socket.IO transport for Pocket market data. It sends
# the captured browser auth frame unchanged and never exposes trading methods to
# AlphaPulse's market-data service.
from backend.services import pocketoption_otc as _po_service
from backend.services.pocket_direct import DirectPocketOptionClient


def _make_direct_market_client(self):
    return DirectPocketOptionClient(self.ssid, is_demo=self.demo)


_po_service.PocketOptionOTCService._make_client = _make_direct_market_client

from backend.main import app
