from __future__ import annotations

import os
from datetime import timedelta

from sqlalchemy import select

from backend.models.db_models import AdminControl, AsyncSessionLocal, utcnow

VALID_STRATEGIES = {"ema_trend", "bollinger_reversal", "atr_breakout"}
VALID_TIMEFRAMES = {"1m", "5m", "15m", "1h"}


def admin_id() -> int:
    return int(os.getenv("ADMIN_ID", "0") or 0)


async def get_control() -> AdminControl | None:
    tid = admin_id()
    if tid <= 0:
        return None
    async with AsyncSessionLocal() as db:
        control = (
            await db.execute(select(AdminControl).where(AdminControl.telegram_id == tid))
        ).scalar_one_or_none()
        if control is None:
            control = AdminControl(
                telegram_id=tid,
                selected_strategy="ema_trend",
                selected_timeframe="1m",
                regular_enabled=True,
                vip_enabled=True,
                vip_interval_seconds=300,
                next_vip_at=utcnow() + timedelta(seconds=300),
            )
            db.add(control)
            await db.commit()
            await db.refresh(control)
        return control


async def update_control(**changes) -> AdminControl:
    tid = admin_id()
    if tid <= 0:
        raise RuntimeError("ADMIN_ID is not configured")
    async with AsyncSessionLocal() as db:
        control = (
            await db.execute(select(AdminControl).where(AdminControl.telegram_id == tid))
        ).scalar_one_or_none()
        if control is None:
            control = AdminControl(telegram_id=tid)
            db.add(control)
            await db.flush()

        old_interval = int(control.vip_interval_seconds or 300)
        old_vip_enabled = bool(control.vip_enabled)
        if "selected_strategy" in changes and changes["selected_strategy"] in VALID_STRATEGIES:
            control.selected_strategy = changes["selected_strategy"]
        if "selected_timeframe" in changes and changes["selected_timeframe"] in VALID_TIMEFRAMES:
            control.selected_timeframe = changes["selected_timeframe"]
        if "regular_enabled" in changes and changes["regular_enabled"] is not None:
            control.regular_enabled = bool(changes["regular_enabled"])
        if "vip_enabled" in changes and changes["vip_enabled"] is not None:
            control.vip_enabled = bool(changes["vip_enabled"])
        if "vip_interval_seconds" in changes and changes["vip_interval_seconds"] is not None:
            control.vip_interval_seconds = max(60, min(86400, int(changes["vip_interval_seconds"])))
        if "last_vip_status" in changes:
            control.last_vip_status = changes["last_vip_status"]
        if "last_vip_at" in changes:
            control.last_vip_at = changes["last_vip_at"]
        if "last_scan_at" in changes:
            control.last_scan_at = changes["last_scan_at"]
        if "next_vip_at" in changes:
            control.next_vip_at = changes["next_vip_at"]
        elif bool(control.vip_enabled) and not old_vip_enabled:
            # Enabling VIP from the admin panel should not wait for an old stale
            # timestamp. The next scanner tick starts the VIP check immediately.
            control.next_vip_at = utcnow()
        elif int(control.vip_interval_seconds or 300) != old_interval:
            # A frequency change takes effect from now, not after the previous
            # schedule finishes.
            control.next_vip_at = utcnow() + timedelta(seconds=int(control.vip_interval_seconds or 300))

        await db.commit()
        await db.refresh(control)
        return control


def serialize_control(control: AdminControl | None) -> dict:
    if control is None:
        return {
            "configured": False,
            "selected_strategy": "ema_trend",
            "selected_timeframe": "1m",
            "regular_enabled": False,
            "vip_enabled": False,
            "vip_interval_seconds": 300,
            "next_vip_at": None,
            "last_vip_at": None,
            "last_vip_status": None,
            "last_scan_at": None,
        }
    return {
        "configured": True,
        "telegram_id": int(control.telegram_id),
        "selected_strategy": control.selected_strategy,
        "selected_timeframe": control.selected_timeframe,
        "regular_enabled": bool(control.regular_enabled),
        "vip_enabled": bool(control.vip_enabled),
        "vip_interval_seconds": int(control.vip_interval_seconds or 300),
        "next_vip_at": control.next_vip_at.isoformat() + "Z" if control.next_vip_at else None,
        "last_vip_at": control.last_vip_at.isoformat() + "Z" if control.last_vip_at else None,
        "last_vip_status": control.last_vip_status,
        "last_scan_at": control.last_scan_at.isoformat() + "Z" if control.last_scan_at else None,
    }
