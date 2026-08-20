from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta

from aiogram import Bot
from sqlalchemy import select

from backend.models.db_models import AsyncSessionLocal, User, UserSettings, utcnow
from backend.services.control import get_control, update_control
from backend.services.pocketoption_otc import MarketDataUnavailable, OTC_ASSETS, market_data
from backend.services.positions import reconcile_positions, sync_broker_positions
from backend.services.reconciler import reconcile_pending
from backend.services.session_driver import drive_session_tick
from backend.services.signal_engine import signal_engine
from backend.services.signal_store import save_signal
from backend.services.trade_runtime import update_trade_runtime

logger = logging.getLogger("alphapulse.scanner")
VIP_MIN_CONFIDENCE = 82.0
ADMIN_ID = os.environ.get("ADMIN_ID", "0")


def format_signal(signal: dict) -> str:
    from datetime import datetime, timezone

    entry = datetime.fromisoformat(signal["entry_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
    expiry = datetime.fromisoformat(signal["expiry_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
    arrow = "🟢 <b>CALL / ВВЕРХ</b>" if signal["direction"] == "BUY" else "🔴 <b>PUT / ВНИЗ</b>"
    confirmations = signal.get("confirmations") or []
    confirmations_text = "\n".join(f"• {item}" for item in confirmations[:5])
    return (
        "🔥 <b>VIP 5M OTC СИГНАЛ</b>\n\n"
        + f"Актив: <b>{signal['pair']}</b>\n"
        + f"Стратегия: <b>{signal.get('strategy_label', signal['strategy'])}</b>\n"
        + f"Направление: {arrow}\n"
        + "Таймфрейм: <b>5m</b>\n"
        + f"⏰ Вход: <b>{entry:%H:%M:%S UTC}</b>\n"
        + f"⌛ Экспирация: <b>{expiry:%H:%M:%S UTC}</b>\n"
        + f"Уверенность: <b>{float(signal['confidence']):.1f}%</b>\n\n"
        + signal["reason"]
        + (f"\n\n<b>Подтверждения:</b>\n{confirmations_text}" if confirmations_text else "")
    )


def should_notify(settings: UserSettings, signal: dict) -> bool:
    del signal
    return bool(settings.vip_enabled)


async def notify_signal(bot: Bot, signal: dict) -> dict:
    notified = 0
    errors = 0
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(User, UserSettings)
                .join(UserSettings, UserSettings.telegram_id == User.telegram_id)
                .where(User.status == "VERIFIED")
            )
        ).all()
    for user, settings in rows:
        if not should_notify(settings, signal):
            continue
        try:
            await bot.send_message(int(user.telegram_id), format_signal(signal))
            notified += 1
        except Exception as exc:
            errors += 1
            logger.warning(
                "VIP notification failed for user %s: %s",
                user.telegram_id,
                type(exc).__name__,
            )
    return {"notified": notified, "notification_errors": errors}


async def _vip_tick(bot: Bot, control, now) -> dict:
    if not control.vip_enabled:
        return {"status": "DISABLED"}
    if control.next_vip_at is not None and control.next_vip_at > now:
        return {"status": "WAITING", "next_vip_at": control.next_vip_at.isoformat() + "Z"}

    interval = max(60, min(86400, int(control.vip_interval_seconds or 300)))
    next_vip = now + timedelta(seconds=interval)
    try:
        candidate = await signal_engine.scan_vip(list(OTC_ASSETS.keys()))
    except (MarketDataUnavailable, RuntimeError, asyncio.TimeoutError) as exc:
        await update_control(
            last_vip_at=now,
            last_vip_status="MARKET_ERROR",
            next_vip_at=next_vip,
            last_scan_at=now,
        )
        return {
            "status": "MARKET_ERROR",
            "error": type(exc).__name__,
            "next_vip_at": next_vip.isoformat() + "Z",
        }

    if not candidate or float(candidate.get("confidence") or 0) < VIP_MIN_CONFIDENCE:
        await update_control(
            last_vip_at=now,
            last_vip_status="NO_CONFIRMED_SETUP",
            next_vip_at=next_vip,
            last_scan_at=now,
        )
        return {
            "status": "NO_CONFIRMED_SETUP",
            "next_vip_at": next_vip.isoformat() + "Z",
            "timeframe": "5m",
        }

    signal, duplicate = await save_signal(candidate, is_vip=True)
    notification = {"notified": 0, "notification_errors": 0}
    if not duplicate:
        notification = await notify_signal(bot, signal)
    await update_control(
        last_vip_at=now,
        last_vip_status="DUPLICATE" if duplicate else "ISSUED",
        next_vip_at=next_vip,
        last_scan_at=now,
    )
    return {
        "status": "DUPLICATE" if duplicate else "ISSUED",
        "signal": signal,
        "next_vip_at": next_vip.isoformat() + "Z",
        **notification,
    }


async def _safe_component(label: str, operation, fallback: dict) -> dict:
    try:
        return await operation()
    except Exception as exc:
        logger.warning("Scanner %s failed: %s", label, type(exc).__name__)
        return {**fallback, "error": type(exc).__name__}


async def scan_tick(bot: Bot) -> dict:
    health = await market_data.health()
    if not health.get("configured"):
        return {"ok": True, "scanner": "disabled", "reason": "market source is not configured"}

    try:
        admin_id = int(ADMIN_ID or 0)
    except Exception:
        admin_id = 0

    broker_sync = (
        await _safe_component(
            "broker-sync",
            lambda: sync_broker_positions(admin_id),
            {"supported": False},
        )
        if admin_id
        else {"supported": False}
    )
    signal_reconcile = await _safe_component(
        "signal-reconcile",
        reconcile_pending,
        {"status": "ERROR"},
    )
    position_reconcile = await _safe_component(
        "position-reconcile",
        reconcile_positions,
        {"status": "ERROR"},
    )

    try:
        auto = await drive_session_tick()
    except (MarketDataUnavailable, RuntimeError, asyncio.TimeoutError) as exc:
        message = "Pocket временно недоступен · сохраняю сессию и переподключусь автоматически"
        await update_trade_runtime(stage="WAIT_MARKET", message=message)
        logger.warning("AUTO waiting for Pocket reconnect: %s", type(exc).__name__)
        auto = {"status": "WAIT_MARKET", "error": type(exc).__name__, "message": message}
    except Exception as exc:
        logger.exception("AUTO session tick failed: %s", type(exc).__name__)
        auto = {"status": "ERROR", "error": type(exc).__name__}

    control = await get_control()
    if control is None:
        return {
            "ok": True,
            "scanner": "auto-only",
            "auto": auto,
            "broker_sync": broker_sync,
            "reconcile": signal_reconcile,
            "positions": position_reconcile,
        }

    try:
        vip = await _vip_tick(bot, control, utcnow())
    except Exception as exc:
        logger.warning("VIP tick recovered after error: %s", type(exc).__name__)
        vip = {"status": "ERROR", "error": type(exc).__name__}

    return {
        "ok": True,
        "scanner": "5s-session-window",
        "auto": auto,
        "vip": vip,
        "broker_sync": broker_sync,
        "reconcile": signal_reconcile,
        "positions": position_reconcile,
    }
