from __future__ import annotations

import asyncio
import logging
import os
import signal

from dotenv import load_dotenv


logger = logging.getLogger("alphapulse.worker")
HUNT_REGULAR = "HUNT_REGULAR"
HUNT_FOUND = "HUNT_FOUND"
REGULAR_CONFIDENCE = 72.0


async def _regular_hunt_tick() -> None:
    """Advance an admin-requested regular signal hunt on the worker only."""
    from backend.models.db_models import utcnow
    from backend.services.control import get_control, update_control
    from backend.services.pocketoption_otc import OTC_ASSETS
    from backend.services.signal_engine import signal_engine
    from backend.services.signal_store import save_signal

    control = await get_control()
    if control is None or str(control.last_vip_status or "") != HUNT_REGULAR:
        return
    candidate = await signal_engine.scan_strategy(
        control.selected_timeframe,
        list(OTC_ASSETS.keys()),
        control.selected_strategy,
    )
    now = utcnow()
    if not candidate or float(candidate.get("confidence") or 0) < REGULAR_CONFIDENCE:
        await update_control(last_scan_at=now, last_vip_status=HUNT_REGULAR)
        return
    _signal, duplicate = await save_signal(candidate, is_vip=False)
    await update_control(
        last_scan_at=now,
        last_vip_status=HUNT_REGULAR if duplicate else HUNT_FOUND,
    )


async def _maintenance_loop(stop_event: asyncio.Event) -> None:
    from backend.services.execution_recovery import reconcile_uncertain_executions
    from backend.services.reconciler import reconcile_pending
    from backend.services.vip_runtime_fix import run_due_vip

    telegram_bot = None
    if str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip():
        try:
            from bot.main import bot as telegram_bot
        except Exception:
            logger.exception("Telegram bot could not initialize in worker maintenance")
    while not stop_event.is_set():
        # Resolve ambiguous broker sends before any maintenance that could make
        # a new trading decision. This path never resends an order.
        try:
            recovery = await reconcile_uncertain_executions()
            if recovery.get("recovered"):
                logger.warning("Recovered %s uncertain Pocket execution(s)", recovery["recovered"])
        except Exception as exc:
            logger.warning("Uncertain execution recovery recovered after %s", type(exc).__name__)
        try:
            await reconcile_pending()
        except Exception as exc:
            logger.warning("Manual signal reconciliation recovered after %s", type(exc).__name__)
        try:
            await _regular_hunt_tick()
        except Exception as exc:
            logger.warning("Regular hunt recovered after %s", type(exc).__name__)
        try:
            await run_due_vip(telegram_bot)
        except Exception as exc:
            logger.warning("VIP maintenance recovered after %s", type(exc).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass


def _require_demo_runtime() -> None:
    if str(os.getenv("VERCEL") or "").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("The persistent worker cannot run inside Vercel")
    if str(os.getenv("POCKET_OPTION_DEMO") or "true").strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        raise RuntimeError("REAL AUTO execution is disabled; POCKET_OPTION_DEMO must be true")
    missing = [
        name
        for name in ("DATABASE_URL", "ADMIN_ID", "POCKET_OPTION_SSID", "WORKER_SHARED_SECRET")
        if not str(os.getenv(name) or "").strip()
    ]
    if missing:
        raise RuntimeError(f"Missing worker configuration: {', '.join(missing)}")
    try:
        if int(os.getenv("ADMIN_ID") or 0) <= 0:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("ADMIN_ID must be a positive Telegram user id") from exc
    if len(str(os.getenv("WORKER_SHARED_SECRET") or "").strip()) < 32:
        raise RuntimeError("WORKER_SHARED_SECRET must contain at least 32 characters")
    if len(str(os.getenv("POCKET_OPTION_SSID") or "").strip()) < 10:
        raise RuntimeError("POCKET_OPTION_SSID looks incomplete")


async def run_worker() -> None:
    load_dotenv(override=False)
    os.environ["APP_RUNTIME_ROLE"] = "worker"
    os.environ["AUTO_REALTIME_DRIVER"] = "true"
    _require_demo_runtime()

    from backend.models.db_models import engine
    from backend.services.auto_realtime import start_auto_realtime_driver, stop_auto_realtime_driver
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

    supervisor = asyncio.create_task(worker_supervisor(stop_event, account_id), name="alphapulse-worker-supervisor")
    maintenance = asyncio.create_task(_maintenance_loop(stop_event), name="alphapulse-worker-maintenance")
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
        realtime_task = asyncio.create_task(realtime_server.serve(), name="alphapulse-worker-realtime")
    if not await start_auto_realtime_driver():
        supervisor.cancel()
        maintenance.cancel()
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
        for task in (supervisor, maintenance):
            try:
                await task
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
