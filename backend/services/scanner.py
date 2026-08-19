from __future__ import annotations

import os
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy import select

from backend.models.db_models import AsyncSessionLocal, User, UserSettings
from backend.routers.signals import ScanRequest, reconcile, scan_best
from backend.services.pocketoption_otc import OTC_ASSETS, market_data

MIN_CONF = float(os.getenv("SIGNAL_MIN_CONFIDENCE", "72"))


def format_signal(signal: dict) -> str:
    entry = datetime.fromisoformat(signal["entry_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
    expiry = datetime.fromisoformat(signal["expiry_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
    arrow = "🟢 <b>CALL / ВВЕРХ</b>" if signal["direction"] == "BUY" else "🔴 <b>PUT / ВНИЗ</b>"
    title = "🔥 <b>VIP OTC СИГНАЛ</b>" if signal.get("is_vip") else "🚨 <b>OTC СИГНАЛ</b>"
    return title + "\n\n" + f"Актив: <b>{signal['pair']}</b>\n" + f"Стратегия: <b>{signal['strategy_label']}</b>\n" + f"Направление: {arrow}\n" + f"Таймфрейм: <b>{signal['timeframe']}</b>\n" + f"⏰ Вход: <b>{entry:%H:%M:%S UTC}</b>\n" + f"⌛ Экспирация: <b>{expiry:%H:%M:%S UTC}</b>\n" + f"Уверенность: <b>{signal['confidence']:.1f}%</b>\n\n" + f"{signal['reason']}"


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


async def scan_tick(bot: Bot) -> dict:
    market_health = await market_data.health()
    if not market_health.get("configured"):
        return {"ok": True, "scanner": "disabled", "reason": "market source is not configured"}

    async with AsyncSessionLocal() as db:
        reconciliation = await reconcile(db=db)

    minute = datetime.now(timezone.utc).minute
    timeframes = ["1m"] + (["5m"] if minute % 5 == 0 else [])
    published: list[dict] = []

    for timeframe in timeframes:
        async with AsyncSessionLocal() as db:
            result = await scan_best(ScanRequest(timeframe=timeframe, assets=list(OTC_ASSETS.keys()), min_confidence=MIN_CONF), db=db)

        if result.get("status") != "SIGNAL" or result.get("duplicate"):
            continue

        signal = result["signal"]
        published.append(signal)

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(User, UserSettings).join(UserSettings, UserSettings.telegram_id == User.telegram_id).where(User.status == "VERIFIED"))).all()

        for user, settings in rows:
            if not should_notify(settings, signal):
                continue
            try:
                await bot.send_message(int(user.telegram_id), format_signal(signal))
            except Exception:
                pass

    return {"ok": True, "reconcile": reconciliation, "published": len(published), "signals": published}
