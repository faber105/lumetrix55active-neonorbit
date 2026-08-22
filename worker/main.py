from __future__ import annotations

import asyncio
import logging
import os
import signal

from dotenv import load_dotenv


logger = logging.getLogger("alphapulse.worker")


def _require_demo_runtime() -> None:
    if str(os.getenv("VERCEL") or "").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("The persistent worker cannot run inside Vercel")
    if str(os.getenv("POCKET_OPTION_DEMO") or "true").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError("REAL AUTO execution is disabled; POCKET_OPTION_DEMO must be true")
    missing = [
        name
        for name in ("DATABASE_URL", "POCKET_OPTION_SSID")
        if not str(os.getenv(name) or "").strip()
    ]
    if missing:
        raise RuntimeError(f"Missing worker configuration: {', '.join(missing)}")


async def run_worker() -> None:
    load_dotenv(override=False)
    os.environ["APP_RUNTIME_ROLE"] = "worker"
    os.environ["AUTO_REALTIME_DRIVER"] = "true"
    _require_demo_runtime()

    # Imports happen only after the runtime guard so the public web runtime can
    # never acquire worker-side resources as an import side effect.
    from backend.models.db_models import engine
    from backend.services.auto_realtime import (
        start_auto_realtime_driver,
        stop_auto_realtime_driver,
    )
    from backend.services.database import init_db
    from backend.services.pocketoption_otc import market_data
    from backend.services.preload_next import ensure_preload_schema
    from backend.services.session_engine import ensure_schema

    await init_db()
    await ensure_schema()
    await ensure_preload_schema()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop(*_args: object) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, request_stop)

    if not await start_auto_realtime_driver():
        raise RuntimeError("Persistent AUTO driver did not start")

    logger.info("AlphaPulse Windows worker started in DEMO-only mode")
    try:
        await stop_event.wait()
    finally:
        logger.info("AlphaPulse Windows worker is stopping")
        await stop_auto_realtime_driver()
        await market_data.close()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
