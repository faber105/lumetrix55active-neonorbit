from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

DIAG_KEY = "__vercel_import_diag__"
TOKEN_KEY = "__runtime_telegram_bot__"


def _redact(text: str, secrets: list[str]) -> str:
    out = str(text or "")
    for value in secrets:
        if value:
            out = out.replace(value, "<redacted>")
    return out[-1500:]


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
    env_token = str(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    if env_token:
        return env_token
    conn = await _db_connect()
    if conn is None:
        return ""
    try:
        value = await conn.fetchval("SELECT payload FROM ml_state WHERE strategy=$1", TOKEN_KEY)
        return str(value or "").strip()
    except Exception:
        return ""
    finally:
        await conn.close()


async def _persist(payload: dict) -> None:
    try:
        conn = await _db_connect()
        if conn is None:
            return
        try:
            body = json.dumps({"at": datetime.now(timezone.utc).isoformat(), **payload}, ensure_ascii=False)
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
        print(f"Cannot persist import diagnostic: {type(exc).__name__}: {exc}", file=sys.stderr)


def _probe(label: str, code: str, env: dict[str, str], secrets: list[str]) -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=70,
        )
        return {
            "label": label,
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": _redact(proc.stdout, secrets),
            "stderr": _redact(proc.stderr, secrets),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "label": label,
            "ok": False,
            "returncode": None,
            "error": "timeout",
            "stdout": _redact(exc.stdout or "", secrets),
            "stderr": _redact(exc.stderr or "", secrets),
        }
    except Exception as exc:
        return {
            "label": label,
            "ok": False,
            "returncode": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def main() -> int:
    token = await _load_token()
    env = os.environ.copy()
    if token:
        env["TELEGRAM_BOT_TOKEN"] = token
        env["BOT_TOKEN"] = token
    env.setdefault("BACKEND_URL", "https://lumetrix55active-neonorbit.vercel.app")
    env.setdefault("MINI_APP_URL", env["BACKEND_URL"])

    secrets = [token, str(os.getenv("DATABASE_URL") or "")]
    probes = [
        ("core", "import asyncpg, fastapi, sqlalchemy; print('core-ok')"),
        ("db_models", "import backend.models.db_models; print('db-models-ok')"),
        ("pocket_service", "import backend.services.pocketoption_otc; print('pocket-service-ok')"),
        ("bot_main", "import bot.main; print('bot-main-ok')"),
        ("backend_main", "import backend.main; print('backend-main-ok')"),
        ("api_main", "import api.main; print('api-main-ok')"),
        ("api_index", "import api.index; print('api-index-ok')"),
    ]

    results: list[dict] = []
    for label, code in probes:
        result = _probe(label, code, env, secrets)
        results.append(result)
        if not result.get("ok"):
            break

    payload = {
        "python": sys.version.split()[0],
        "token_present": bool(token),
        "database_present": bool(str(os.getenv("DATABASE_URL") or "").strip()),
        "probes": results,
        "success": bool(results) and all(bool(item.get("ok")) for item in results),
    }
    await _persist(payload)
    print("Runtime import diagnostic:", json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
