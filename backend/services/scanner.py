from __future__ import annotations

import logging
import os
from datetime import timedelta

from aiogram import Bot
from sqlalchemy import select

from backend.models.db_models import AsyncSessionLocal, User, UserSettings, utcnow
from backend.routers.signals import save_candidate
from backend.services.control import get_control, update_control
from backend.services.pocketoption_otc import OTC_ASSETS, market_data
from backend.services.positions import reconcile_positions, sync_broker_positions
from backend.services.reconciler import reconcile_pending
from backend.services.signal_engine import signal_engine

logger = logging.getLogger("alphapulse.scanner")
MIN_CONF = float(os.getenv("SIGNAL_MIN_CONFIDENCE", "72"))
VIP_CONF = float(os.getenv("VIP_SIGNAL_MIN_CONFIDENCE", "80"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)


def format_signal(signal: dict) -> str:
    from datetime import datetime, timezone

    entry = datetime.fromisoformat(signal["entry_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
    expiry = datetime.fromisoformat(signal["expiry_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
    arrow = "🟢 <b>CALL / ВВЕРХ</b>" if signal["direction"] == "BUY" else "🔴 <b>PUT / ВНИЗ</b>"
    title = "🔥 <b>VIP OTC СИГНАЛ</b>" if signal.get("is_vip") else "🚨 <b>OTC СИГНАЛ</b>"
    return (
        title + "\n\n"
        + f"Актив: <b>{signal['pair']}</b>\n"
        + f"Стратегия: <b>{signal['strategy_label']}</b>\n"
        + f"Направление: {arrow}\n"
        + f"Таймфрейм: <b>{signal['timeframe']}</b>\n"
        + f"⏰ Вход: <b>{entry:%H:%M:%S UTC}</b>\n"
        + f"⌛ Экспирация: <b>{expiry:%H:%M:%S UTC}</b>\n"
        + f"Уверенность: <b>{signal['confidence']:.1f}%</b>\n\n"
        + f"{signal['reason']}"
    )


def should_notify(settings: UserSettings, signal: dict) -> bool:
    is_vip = bool(signal.get("is_vip"))
    mode = (settings.signal_mode or "vip").lower()
    frequency = (settings.notification_frequency or "standard").lower()
    if is_vip and not settings.vip_enabled:
        return False
    if mode == "vip" and not is_vip:
        return False
    if frequency == "rarely" and not is_vip:
        return False
    if mode == "mixed" and not is_vip and float(signal.get("confidence") or 0) < 76.0:
        return False
    return True


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
                "Telegram signal notification failed for user %s: %s",
                user.telegram_id,
                type(exc).__name__,
            )
    return {"notified": notified, "notification_errors": errors}


async def _save(candidate: dict) -> tuple[dict, bool]:
    async with AsyncSessionLocal() as db:
        row, duplicate = await save_candidate(db, candidate)
        from backend.routers.signals import out

        return out(row), duplicate


async def scan_tick(bot: Bot) -> dict:
    market_health = await market_data.health()
    if not market_health.get("configured"):
        return {"ok": True, "scanner": "disabled", "reason": "market source is not configured"}

    # Read-only fallback: even when Mini App is not open, periodically capture a
    # manually opened Pocket deal and attach it to its matching AlphaPulse signal.
    broker_sync = await sync_broker_positions(ADMIN_ID) if ADMIN_ID else {"supported": False}
    reconciliation = await reconcile_pending()
    position_reconciliation = await reconcile_positions()
    control = await get_control()
    if control is None:
        return {
            "ok": True,
            "scanner": "disabled",
            "reason": "ADMIN_ID is not configured",
            "broker_sync": broker_sync,
            "reconcile": reconciliation,
            "positions": position_reconciliation,
        }

    now = utcnow()
    vip_due = bool(control.vip_enabled) and (
        control.next_vip_at is None or control.next_vip_at <= now
    )
    should_scan = bool(control.regular_enabled) or vip_due
    candidate = None
    if should_scan:
        candidate = await signal_engine.scan_strategy(
            control.selected_timeframe,
            list(OTC_ASSETS.keys()),
            control.selected_strategy,
        )

    published: list[dict] = []
    notified = 0
    notification_errors = 0
    vip_status = control.last_vip_status

    if vip_due:
        interval = max(60, int(control.vip_interval_seconds or 300))
        next_vip = now + timedelta(seconds=interval)
        if candidate and float(candidate.get("confidence") or 0) >= VIP_CONF:
            signal, duplicate = await _save(candidate)
            if not duplicate:
                published.append(signal)
                info = await notify_signal(bot, signal)
                notified += info["notified"]
                notification_errors += info["notification_errors"]
                vip_status = "ISSUED"
            else:
                vip_status = "DUPLICATE"
        else:
            vip_status = "NO_CONFIRMED_SETUP"
        await update_control(
            next_vip_at=next_vip,
            last_vip_at=now,
            last_vip_status=vip_status,
            last_scan_at=now,
        )
    else:
        if (
            control.regular_enabled
            and candidate
            and MIN_CONF <= float(candidate.get("confidence") or 0) < VIP_CONF
        ):
            signal, duplicate = await _save(candidate)
            if not duplicate:
                published.append(signal)
                info = await notify_signal(bot, signal)
                notified += info["notified"]
                notification_errors += info["notification_errors"]
        await update_control(last_scan_at=now)

    return {
        "ok": True,
        "strategy": control.selected_strategy,
        "timeframe": control.selected_timeframe,
        "vip_due": vip_due,
        "vip_status": vip_status,
        "broker_sync": broker_sync,
        "reconcile": reconciliation,
        "positions": position_reconciliation,
        "published": len(published),
        "notified": notified,
        "notification_errors": notification_errors,
        "signals": published,
    }
