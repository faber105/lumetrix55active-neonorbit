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
DEFAULT_PUBLIC_APP = "https://lumetrix55active-neonorbit.onerfaber.workers.dev"


async def _regular_hunt_tick() -> None:
    """Advance an admin-requested regular signal hunt on the persistent worker only."""
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


async def _install_worker_bot_db_fallback(bot_main) -> None:
    """Keep /start and verification independent of an external API deployment."""
    from sqlalchemy import text
    from backend.models.db_models import AsyncSessionLocal, utcnow

    def serialize_row(row):
        if row is None:
            return None
        item = dict(row)
        for key in ("click_time", "pending_time", "verified_time", "created_at"):
            value = item.get(key)
            if value is not None and hasattr(value, "isoformat"):
                item[key] = value.isoformat()
        item["attempts_count"] = int(item.get("attempts_count") or 0)
        return item

    async def local_get_user(telegram_id: int):
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    text("""SELECT id,telegram_id,username,full_name,status,click_time,
                        pending_time,verified_time,attempts_count,created_at
                        FROM users WHERE telegram_id=:telegram_id LIMIT 1"""),
                    {"telegram_id": int(telegram_id)},
                )
            ).mappings().first()
        return serialize_row(row)

    async def local_create_user(telegram_id: int, username: str, full_name: str):
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    text("""INSERT INTO users
                        (telegram_id,username,full_name,status,attempts_count,created_at)
                        VALUES (:telegram_id,:username,:full_name,'NEW',0,:created_at)
                        ON CONFLICT (telegram_id) DO UPDATE SET
                            username=COALESCE(NULLIF(EXCLUDED.username,''),users.username),
                            full_name=COALESCE(NULLIF(EXCLUDED.full_name,''),users.full_name)
                        RETURNING id,telegram_id,username,full_name,status,click_time,
                            pending_time,verified_time,attempts_count,created_at"""),
                    {
                        "telegram_id": int(telegram_id),
                        "username": username or None,
                        "full_name": full_name or None,
                        "created_at": utcnow(),
                    },
                )
            ).mappings().one()
            await db.commit()
        return serialize_row(row)

    async def local_set_status(telegram_id: int, status: str) -> bool:
        value = str(status).upper()
        now = utcnow()
        async with AsyncSessionLocal() as db:
            row_id = (
                await db.execute(
                    text("""UPDATE users SET
                        status=:status,
                        verified_time=CASE WHEN :status='VERIFIED' THEN :now ELSE verified_time END,
                        pending_time=CASE WHEN :status='PENDING' THEN :now ELSE pending_time END
                        WHERE telegram_id=:telegram_id
                        RETURNING id"""),
                    {"status": value, "now": now, "telegram_id": int(telegram_id)},
                )
            ).scalar_one_or_none()
            await db.commit()
        return row_id is not None

    bot_main.get_user = local_get_user
    bot_main.create_user = local_create_user
    bot_main.set_status = local_set_status
    logger.info("Telegram worker auth fallback is using Neon directly")


async def _telegram_polling_loop(stop_event: asyncio.Event) -> None:
    """Keep the Telegram bot responsive from the persistent worker."""
    if str(os.getenv("WORKER_ENABLE_TELEGRAM_POLLING") or "true").strip().lower() not in {"1", "true", "yes", "on"}:
        logger.info("Telegram polling disabled by WORKER_ENABLE_TELEGRAM_POLLING")
        return
    token = str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        logger.warning("Telegram polling disabled: TELEGRAM_BOT_TOKEN is empty")
        return

    os.environ.setdefault("BACKEND_URL", DEFAULT_PUBLIC_APP)
    os.environ.setdefault("MINI_APP_URL", DEFAULT_PUBLIC_APP)

    while not stop_event.is_set():
        polling_task: asyncio.Task | None = None
        stop_task: asyncio.Task | None = None
        try:
            import bot.main as bot_main
            bot = bot_main.bot
            dp = bot_main.dp
            await _install_worker_bot_db_fallback(bot_main)

            await bot.delete_webhook(drop_pending_updates=False)
            logger.info("Telegram webhook removed; persistent worker polling is active")

            polling_task = asyncio.create_task(
                dp.start_polling(bot, handle_signals=False),
                name="alphapulse-telegram-polling",
            )
            stop_task = asyncio.create_task(stop_event.wait(), name="alphapulse-telegram-stop-wait")
            done, _pending = await asyncio.wait(
                {polling_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if stop_task in done and stop_event.is_set():
                if not polling_task.done():
                    await dp.stop_polling()
                try:
                    await polling_task
                except asyncio.CancelledError:
                    pass
                return

            await polling_task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram polling crashed; retrying in 5 seconds")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
        finally:
            if stop_task is not None and not stop_task.done():
                stop_task.cancel()
            if polling_task is not None and not polling_task.done() and stop_event.is_set():
                polling_task.cancel()


def _require_demo_runtime() -> None:
    if str(os.getenv("VERCEL") or "").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("The persistent worker cannot run inside Vercel")
    if str(os.getenv("POCKET_OPTION_DEMO") or "true").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("REAL AUTO execution is disabled; POCKET_OPTION_DEMO must be true")
    missing = [name for name in ("DATABASE_URL", "ADMIN_ID", "POCKET_OPTION_SSID", "WORKER_SHARED_SECRET") if not str(os.getenv(name) or "").strip()]
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
    os.environ.setdefault("WORKER_ID", "alphapulse-oracle-milan-1" if os.name != "nt" else "alphapulse-windows")
    _require_demo_runtime()

    from backend.models.db_models import engine
    from backend.services.auto_trade import close_demo_trading_client
    from backend.services.auto_realtime import start_auto_realtime_driver, stop_auto_realtime_driver
    from backend.services.database import init_db
    from backend.services.pocketoption_otc import market_data
    from backend.services.preload_next import ensure_preload_schema
    from backend.services.session_engine import ensure_schema
    from backend.services.worker_protocol import acquire_lease, ensure_demo_account, ensure_worker_schema, register_heartbeat, release_lease, worker_supervisor
    from worker.cloudflare_bridge import run_cloudflare_bridge

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
    telegram_polling = asyncio.create_task(_telegram_polling_loop(stop_event), name="alphapulse-telegram-runtime")
    cloudflare_bridge = asyncio.create_task(run_cloudflare_bridge(stop_event), name="alphapulse-cloudflare-bridge")

    import uvicorn
    gateway_server = uvicorn.Server(
        uvicorn.Config(
            "worker.realtime_server:app",
            host="127.0.0.1",
            port=int(os.getenv("WORKER_HTTP_PORT") or 8765),
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
            access_log=False,
        )
    )
    gateway_task = asyncio.create_task(gateway_server.serve(), name="alphapulse-worker-gateway")

    if not await start_auto_realtime_driver():
        for task in (supervisor, maintenance, telegram_polling, cloudflare_bridge):
            task.cancel()
        gateway_server.should_exit = True
        gateway_task.cancel()
        raise RuntimeError("Persistent AUTO driver did not start")

    logger.info("AlphaPulse persistent DEMO worker started")
    logger.info("Local Mini App gateway listening on 127.0.0.1:%s", os.getenv("WORKER_HTTP_PORT") or "8765")
    try:
        await stop_event.wait()
    finally:
        logger.info("AlphaPulse persistent worker is stopping")
        await stop_auto_realtime_driver()
        stop_event.set()
        gateway_server.should_exit = True
        try:
            await gateway_task
        except asyncio.CancelledError:
            pass
        for task in (supervisor, maintenance, telegram_polling, cloudflare_bridge):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await release_lease(account_id)
        await register_heartbeat(status="OFFLINE")
        await close_demo_trading_client()
        await market_data.close()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
