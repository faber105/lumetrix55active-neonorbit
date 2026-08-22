from __future__ import annotations

import asyncio
import importlib
import os

production_host = str(
    os.getenv("PUBLIC_BACKEND_URL")
    or os.getenv("BACKEND_URL")
    or os.getenv("RENDER_EXTERNAL_HOSTNAME")
    or os.getenv("VERCEL_PROJECT_PRODUCTION_URL")
    or os.getenv("VERCEL_URL")
    or ""
).strip()
if production_host:
    production_base_url = production_host if production_host.startswith(("http://", "https://")) else f"https://{production_host}"
else:
    production_base_url = "https://lumetrix55active-neonorbit.vercel.app"
os.environ["BACKEND_URL"] = production_base_url.rstrip("/")
production_mini_app_url = f"{production_base_url.rstrip('/')}?v=20260821-2500"
os.environ["MINI_APP_URL"] = production_mini_app_url

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
    _bot_main.MINI_APP_URL = production_mini_app_url
except Exception:
    pass
