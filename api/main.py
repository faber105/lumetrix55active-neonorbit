from __future__ import annotations

import asyncio
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

# Normalize values copied through the Vercel UI before any third-party library
# validates them. An accidental trailing newline in a bot token used to crash the
# whole Python function during import, making even /health unavailable.
for _key in (
    "TELEGRAM_BOT_TOKEN",
    "BOT_TOKEN",
    "ADMIN_ID",
    "ADMIN_TELEGRAM_ID",
    "BACKEND_URL",
    "PUBLIC_BACKEND_URL",
    "POCKET_OPTION_SSID",
):
    if _key in os.environ:
        os.environ[_key] = str(os.environ.get(_key) or "").strip()


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
                os.environ.setdefault("TELEGRAM_BOT_TOKEN", bot_token)
                os.environ.setdefault("BOT_TOKEN", bot_token)
            if admin_secret:
                os.environ.setdefault("ADMIN_SECRET", admin_secret)
            if admin_id:
                os.environ.setdefault("ADMIN_ID", admin_id)
                os.environ.setdefault("ADMIN_TELEGRAM_ID", admin_id)
            if pocket:
                os.environ.setdefault("POCKET_OPTION_SSID", pocket)
            return
        except (asyncpg.UndefinedTableError, asyncpg.InvalidCatalogNameError) as exc:
            last_error = exc
            continue
        finally:
            if conn is not None:
                await conn.close()
    if last_error is not None:
        raise last_error


if os.getenv("DATABASE_URL", "").strip() and not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
    try:
        asyncio.run(_load_runtime_secrets_from_db())
    except Exception as exc:
        print(f"AlphaPulse runtime secret bootstrap failed: {type(exc).__name__}")

production_host = (
    os.getenv("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
    or os.getenv("BACKEND_URL", "").strip()
    or os.getenv("PUBLIC_BACKEND_URL", "").strip()
)
if production_host:
    production_base_url = production_host if production_host.startswith(("http://", "https://")) else f"https://{production_host}"
else:
    production_base_url = "https://lumetrix55active-neonorbit.vercel.app"
os.environ["BACKEND_URL"] = production_base_url.rstrip("/")
os.environ["MINI_APP_URL"] = production_base_url.rstrip("/")

_IMPORT_ERROR: Exception | None = None

try:
    # Market data stays read-only. The telemetry subclass additionally records the
    # authenticated Pocket balance and live `updateAssets` payout table; it never has
    # an order method.
    from backend.services import pocketoption_otc as _po_service
    from backend.services.pocket_telemetry import TelemetryPocketOptionClient

    def _make_direct_market_client(self):
        return TelemetryPocketOptionClient(self.ssid, is_demo=self.demo)

    _po_service.PocketOptionOTCService._make_client = _make_direct_market_client

    # Broker execution is still hard-limited to DEMO.
    from backend.services import auto_trade as _auto_trade
    from backend.services.pocket_demo_trading import DirectDemoTradingClient

    def _make_direct_demo_trading_client():
        return DirectDemoTradingClient(_po_service.market_data.ssid)

    _auto_trade._build_trading_client = _make_direct_demo_trading_client

    from backend.main import app
except Exception as exc:
    # Never let Vercel collapse into opaque FUNCTION_INVOCATION_FAILED again.
    # Keep a tiny diagnostic app alive so /health reveals the failing component
    # without exposing credentials or environment values.
    _IMPORT_ERROR = exc
    import traceback
    traceback.print_exc()
    from fastapi import FastAPI

    app = FastAPI(title="AlphaPulse bootstrap diagnostics", version="3.1-recovery")

    @app.get("/health")
    async def recovery_health():
        return {
            "status": "bootstrap_error",
            "service": "alphapulsesbot",
            "error_type": type(_IMPORT_ERROR).__name__ if _IMPORT_ERROR else None,
            "error": str(_IMPORT_ERROR)[:500] if _IMPORT_ERROR else None,
            "telegram_env_present": bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip()),
            "database_env_present": bool(os.getenv("DATABASE_URL", "").strip()),
            "pocket_env_present": bool(os.getenv("POCKET_OPTION_SSID", "").strip()),
            "backend_url": os.getenv("BACKEND_URL") or None,
        }

    @app.get("/")
    async def recovery_root():
        return await recovery_health()
