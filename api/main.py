from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path

import asyncpg
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
ALPHAPULSE_DATABASE = "alphapulsesbot"

bootstrap = ROOT / "runtime_bootstrap.json"
legacy = ROOT / "runtime_secrets.json"
for source in (bootstrap, legacy):
    if source.exists():
        try:
            for key, value in json.loads(source.read_text()).items():
                os.environ.setdefault(str(key), str(value))
        except Exception:
            pass


def _asyncpg_kwargs(url):
    return {
        "host": url.host,
        "port": url.port or 5432,
        "user": url.username,
        "password": url.password,
        "database": url.database,
        "ssl": "require" if url.host and "neon.tech" in url.host else None,
        "statement_cache_size": 0,
        "timeout": 12,
    }


async def _load_runtime_secrets_from_db() -> None:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        return
    original = make_url(raw)
    candidates = [original]
    if original.host and "neon.tech" in original.host and original.database != ALPHAPULSE_DATABASE:
        candidates.append(original.set(database=ALPHAPULSE_DATABASE))

    last_error: Exception | None = None
    for candidate in candidates:
        conn = None
        try:
            conn = await asyncpg.connect(**_asyncpg_kwargs(candidate))
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
            if not bot_token and candidate.database != ALPHAPULSE_DATABASE and len(candidates) > 1:
                continue
            if candidate.database != original.database:
                os.environ["DATABASE_URL"] = candidate.render_as_string(hide_password=False)
            if bot_token:
                os.environ["TELEGRAM_BOT_TOKEN"] = bot_token
                os.environ["BOT_TOKEN"] = bot_token
            if admin_secret:
                os.environ["ADMIN_SECRET"] = admin_secret
            if admin_id:
                os.environ["ADMIN_ID"] = admin_id
                os.environ["ADMIN_TELEGRAM_ID"] = admin_id
            if pocket:
                os.environ["POCKET_OPTION_SSID"] = pocket
            return
        except (asyncpg.UndefinedTableError, asyncpg.InvalidCatalogNameError) as exc:
            last_error = exc
            continue
        finally:
            if conn is not None:
                await conn.close()
    if last_error is not None:
        raise last_error


if os.getenv("DATABASE_URL", "").strip():
    try:
        asyncio.run(_load_runtime_secrets_from_db())
    except Exception as exc:
        print(f"AlphaPulse runtime secret bootstrap failed: {type(exc).__name__}")

production_host = os.getenv("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
if production_host:
    production_base_url = production_host if production_host.startswith(("http://", "https://")) else f"https://{production_host}"
else:
    production_base_url = "https://lumetrix55active-neonorbit.vercel.app"
os.environ["BACKEND_URL"] = production_base_url.rstrip("/")
os.environ["MINI_APP_URL"] = production_base_url.rstrip("/")

from backend.services import pocketoption_otc as _po_service
from backend.services.pocket_telemetry import TelemetryPocketOptionClient


def _make_direct_market_client(self):
    return TelemetryPocketOptionClient(self.ssid, is_demo=self.demo)


_po_service.PocketOptionOTCService._make_client = _make_direct_market_client

from backend.services import auto_trade as _auto_trade
from backend.services.pocket_demo_trading import DirectDemoTradingClient


def _make_direct_demo_trading_client():
    return DirectDemoTradingClient(_po_service.market_data.ssid)


_auto_trade._build_trading_client = _make_direct_demo_trading_client

import backend.main as _backend_main

app = _backend_main.app


async def _idempotent_repair_telegram_webhook() -> dict:
    bot = _backend_main.bot
    backend_url = _backend_main.BACKEND_URL
    if bot is None:
        await _backend_main._write_diag(
            "__telegram_webhook_diag__",
            {"ok": False, "error": "telegram_not_configured", "backend_url": backend_url or None},
        )
        raise _backend_main.HTTPException(503, "Telegram is not configured in this deployment")
    if not backend_url.startswith("https://"):
        await _backend_main._write_diag(
            "__telegram_webhook_diag__",
            {"ok": False, "error": "backend_url_not_configured", "backend_url": backend_url or None},
        )
        raise _backend_main.HTTPException(503, "Production backend URL is not configured")

    target = f"{backend_url}/api/telegram/webhook"

    async def _result_from_info(info, **extra):
        actual = str(info.url or "")
        result = {
            "ok": actual == target,
            "target": target,
            "actual": actual,
            "pending_update_count": int(info.pending_update_count or 0),
            "last_error_message": str(info.last_error_message or "")[:300] or None,
            **extra,
        }
        await _backend_main._write_diag("__telegram_webhook_diag__", result)
        return result

    try:
        info = await bot.get_webhook_info()
        if str(info.url or "") == target:
            return await _result_from_info(info, changed=False)

        await bot.set_webhook(
            url=target,
            secret_token=_backend_main.webhook_secret(),
            drop_pending_updates=False,
        )
        return await _result_from_info(await bot.get_webhook_info(), changed=True)
    except Exception as exc:
        retry_after = int(getattr(exc, "retry_after", 0) or 0)
        if retry_after > 0:
            await asyncio.sleep(min(max(retry_after, 1), 3))
            try:
                info = await bot.get_webhook_info()
                if str(info.url or "") == target:
                    return await _result_from_info(info, changed=False, rate_limit_recovered=True)
            except Exception:
                pass
        await _backend_main._write_diag(
            "__telegram_webhook_diag__",
            {
                "ok": False,
                "target": target,
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
            },
        )
        raise


_backend_main.repair_telegram_webhook = _idempotent_repair_telegram_webhook

try:
    _bot_main = importlib.import_module("bot.main")
    _bot_main.MINI_APP_URL = production_base_url.rstrip("/")
except Exception:
    pass
