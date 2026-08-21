from __future__ import annotations

try:
    from api.main import app
except Exception as exc:
    import json
    import os
    from datetime import datetime, timezone
    from urllib.parse import unquote, urlsplit

    import asyncpg
    from fastapi import FastAPI, HTTPException

    _ERROR_TYPE = type(exc).__name__
    _ERROR_MESSAGE = str(exc)[:500]

    async def _persist_bootstrap_error() -> None:
        raw = str(os.getenv("DATABASE_URL") or "").strip()
        if not raw:
            return
        conn = None
        try:
            parsed = urlsplit(raw)
            database = parsed.path.lstrip("/") or "alphapulsesbot"
            conn = await asyncpg.connect(
                host=parsed.hostname,
                port=parsed.port or 5432,
                user=unquote(parsed.username or ""),
                password=unquote(parsed.password or ""),
                database=database,
                ssl="require" if parsed.hostname and "neon.tech" in parsed.hostname else None,
                statement_cache_size=0,
                timeout=10,
            )
            payload = json.dumps(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "error_type": _ERROR_TYPE,
                    "error": _ERROR_MESSAGE,
                    "telegram_env_present": bool(str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()),
                    "database_env_present": bool(raw),
                    "pocket_env_present": bool(str(os.getenv("POCKET_OPTION_SSID") or "").strip()),
                    "vercel_url_present": bool(str(os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or os.getenv("VERCEL_URL") or "").strip()),
                },
                ensure_ascii=False,
            )
            await conn.execute(
                """
                INSERT INTO ml_state(strategy, payload, samples, updated_at)
                VALUES('__vercel_bootstrap_diag__', $1, 0, NOW())
                ON CONFLICT(strategy) DO UPDATE SET payload=EXCLUDED.payload, updated_at=NOW()
                """,
                payload,
            )
        except Exception:
            pass
        finally:
            if conn is not None:
                await conn.close()

    app = FastAPI(title="AlphaPulse bootstrap recovery", version="recovery")

    @app.on_event("startup")
    async def _startup_diag() -> None:
        await _persist_bootstrap_error()

    @app.get("/api/health")
    async def recovery_health():
        await _persist_bootstrap_error()
        return {
            "status": "bootstrap_error",
            "error_type": _ERROR_TYPE,
            "error": _ERROR_MESSAGE,
        }

    @app.post("/api/internal/telegram-repair")
    async def recovery_repair():
        await _persist_bootstrap_error()
        raise HTTPException(503, f"Bootstrap failed: {_ERROR_TYPE}")
