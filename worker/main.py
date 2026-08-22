from __future__ import annotations

import asyncio
import logging
import os
import signal

from dotenv import load_dotenv


logger = logging.getLogger("alphapulse.worker")


async def _maintenance_loop(stop_event: asyncio.Event) -> None:
    from backend.services.reconciler import reconcile_pending
    from backend.services.vip_runtime_fix import run_due_vip

    telegram_bot = None
    if str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip():
        try:
            from bot.main import bot as telegram_bot
        except Exception:
            logger.exception("Telegram bot could not initialize in worker maintenance")
    while not stop_event.is_set():
        try:
            await reconcile_pending()
        except Exception as exc:
            logger.warning("Manual signal reconciliation recovered after %s", type(exc).__name__)
        try:
            await run_due_vip(telegram_bot)
        except Exception as exc:
            logger.warning("VIP maintenance recovered after %s", type(exc).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


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
    from backend.services.worker_protocol import (
        acquire_lease,
        ensure_demo_account,
        ensure_worker_schema,
        register_heartbeat,
        release_lease,
        worker_supervisor,
    )

    await init_db()
    await ensure_schema()
    await ensure_preload_schema()
    await ensure_worker_schema()
    account_id = await ensure_demo_account()
    os.environ["WORKER_ACCOUNT_ID"] = str(account_id)
    if await acquire_lease(account_id) is None:
        raise RuntimeError("Another worker currently owns the DEMO account lease")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop(*_args: object) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, request_stop)

    supervisor = asyncio.create_task(
        worker_supervisor(stop_event, account_id),
        name="alphapulse-worker-supervisor",
    )
    maintenance = asyncio.create_task(
        _maintenance_loop(stop_event),
        name="alphapulse-worker-maintenance",
    )
    realtime_server = None
    realtime_task = None
    if str(os.getenv("REALTIME_TRANSPORT") or "polling").strip().lower() == "wss":
        import uvicorn

        realtime_server = uvicorn.Server(
            uvicorn.Config(
                "worker.realtime_server:app",
                host="127.0.0.1",
                port=int(os.getenv("WORKER_HTTP_PORT") or 8765),
                log_level=os.getenv("LOG_LEVEL", "info").lower(),
                access_log=False,
            )
        )
        realtime_task = asyncio.create_task(
            realtime_server.serve(),
            name="alphapulse-worker-realtime",
        )
    if not await start_auto_realtime_driver():
        supervisor.cancel()
        raise RuntimeError("Persistent AUTO driver did not start")

    logger.info("AlphaPulse Windows worker started in DEMO-only mode")
    try:
        await stop_event.wait()
    finally:
        logger.info("AlphaPulse Windows worker is stopping")
        await stop_auto_realtime_driver()
        stop_event.set()
        if realtime_server is not None:
            realtime_server.should_exit = True
        if realtime_task is not None:
            await realtime_task
        try:
            await supervisor
        except asyncio.CancelledError:
            pass
        try:
            await maintenance
        except asyncio.CancelledError:
            pass
        await release_lease(account_id)
        await register_heartbeat(status="OFFLINE")
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
