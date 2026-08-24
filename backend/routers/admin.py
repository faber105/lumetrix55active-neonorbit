from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select, text

from backend.models.db_models import AsyncSessionLocal, PaperPosition, Signal, utcnow
from backend.routers.signals import out
from backend.services.auto_trade import (
    MIN_AUTO_PAYOUT,
    get_auto_trade_control,
    latest_execution,
    serialize_auto_trade,
    update_auto_trade_control,
)
from backend.services.control import (
    VALID_STRATEGIES,
    VALID_TIMEFRAMES,
    get_control,
    serialize_control,
    update_control,
)
from backend.services.trade_mode import (
    get_execution_mode,
    get_trade_account_mode,
    set_execution_mode,
    set_trade_account_mode,
)
from backend.services.trade_runtime import get_trade_runtime, reset_trade_runtime, update_trade_runtime
from backend.telegram_auth import TelegramMiniAppUser, admin_user
from worker.fast_snapshot import fast_realtime_snapshot

router = APIRouter()
VIP_CONFIDENCE = 82.0
REGULAR_CONFIDENCE = 72.0
HUNT_REGULAR = "HUNT_REGULAR"
HUNT_VIP = "HUNT_VIP"
HUNT_FOUND = "HUNT_FOUND"


class ControlPatch(BaseModel):
    selected_strategy: str | None = None
    selected_timeframe: str | None = None
    regular_enabled: bool | None = None
    vip_enabled: bool | None = None
    vip_interval_seconds: int | None = None
    auto_trade_enabled: bool | None = None
    auto_trade_regular: bool | None = None
    auto_trade_vip: bool | None = None
    trade_amount: float | None = None
    max_open_positions: int | None = None
    trade_account_mode: str | None = None
    execution_mode: str | None = None


async def _demo_account_id(user_id: int) -> int | None:
    async with AsyncSessionLocal() as db:
        value = (
            await db.execute(
                text("""SELECT id FROM broker_accounts
                    WHERE owner_telegram_id=:uid AND mode='DEMO' AND status='ACTIVE'
                    ORDER BY id DESC LIMIT 1"""),
                {"uid": int(user_id)},
            )
        ).scalar_one_or_none()
    return int(value) if value is not None else None


async def _worker_state(user_id: int) -> dict:
    try:
        account_id = await _demo_account_id(int(user_id))
        if account_id is None:
            return {"worker": {"status": "OFFLINE"}, "runtime": {}}
        return await fast_realtime_snapshot(account_id)
    except Exception:
        return {"worker": {"status": "OFFLINE"}, "runtime": {}}


async def _market_rows() -> tuple[Signal | None, int]:
    async with AsyncSessionLocal() as db:
        latest = (await db.execute(select(Signal).order_by(desc(Signal.created_at)).limit(1))).scalar_one_or_none()
        open_positions = int((await db.execute(
            select(func.count()).select_from(PaperPosition).where(PaperPosition.status == "OPEN")
        )).scalar_one() or 0)
    return latest, open_positions


async def _state_payload(user_id: int) -> dict:
    (
        control,
        auto_control,
        account_mode,
        execution_mode,
        runtime,
        worker_snapshot,
        execution,
        market_rows,
    ) = await asyncio.gather(
        get_control(),
        get_auto_trade_control(),
        get_trade_account_mode(),
        get_execution_mode(),
        get_trade_runtime(),
        _worker_state(user_id),
        latest_execution(),
        _market_rows(),
    )

    worker = worker_snapshot.get("worker") or {}
    worker_runtime = worker_snapshot.get("runtime") or {}
    if worker_runtime:
        runtime = {**runtime, **worker_runtime}
    latest, open_positions = market_rows

    payload = serialize_control(control)
    payload.update(serialize_auto_trade(auto_control))
    hunt_status = str(control.last_vip_status or "") if control else ""
    payout_map = runtime.get("eligible_payouts") or runtime.get("payouts") or {}
    balance = runtime.get("balance")
    worker_status = str(worker.get("status") or "OFFLINE")

    payload.update({
        "market": {
            "configured": worker_status in {"ONLINE", "DEGRADED"},
            "connected": worker_status == "ONLINE",
            "demo": True,
            "provider": "OCI worker / Pocket Option DEMO",
            "worker_status": worker_status,
            "heartbeat_age_seconds": worker.get("heartbeat_age_seconds"),
        },
        "worker": worker,
        "open_positions": open_positions,
        "latest_signal": out(latest) if latest else None,
        "latest_execution": execution,
        "trade_account": "demo",
        "trade_account_mode": account_mode,
        "account_matches_mode": account_mode == "demo",
        "execution_mode": execution_mode,
        "regular_confidence": REGULAR_CONFIDENCE,
        "vip_confidence": VIP_CONFIDENCE,
        "min_auto_payout": MIN_AUTO_PAYOUT,
        "pocket_balance": balance,
        "pocket_balance_is_demo": runtime.get("balance_is_demo", True),
        "payouts": payout_map,
        "auto_runtime": runtime,
        "hunt": {
            "active": hunt_status in {HUNT_REGULAR, HUNT_VIP},
            "kind": "vip" if hunt_status == HUNT_VIP else ("regular" if hunt_status == HUNT_REGULAR else None),
            "status": hunt_status or None,
            "last_scan_at": control.last_scan_at.isoformat() + "Z" if control and control.last_scan_at else None,
        },
    })
    payload["vip_seconds_remaining"] = (
        max(0, int((control.next_vip_at - utcnow()).total_seconds())) if control and control.next_vip_at else None
    )
    return payload


@router.get("/state")
async def state(user: TelegramMiniAppUser = Depends(admin_user)):
    return await _state_payload(int(user.id))


@router.patch("/state")
async def patch_state(data: ControlPatch, user: TelegramMiniAppUser = Depends(admin_user)):
    changes = data.model_dump(exclude_none=True)
    if "selected_strategy" in changes and changes["selected_strategy"] not in VALID_STRATEGIES:
        raise HTTPException(400, "Unknown strategy")
    if "selected_timeframe" in changes and changes["selected_timeframe"] not in VALID_TIMEFRAMES:
        raise HTTPException(400, "Unknown timeframe")
    if "vip_interval_seconds" in changes:
        changes["vip_interval_seconds"] = max(60, min(86400, int(changes["vip_interval_seconds"])))
    if "trade_amount" in changes and not (1.0 <= float(changes["trade_amount"]) <= 50000.0):
        raise HTTPException(400, "Trade amount must be between 1 and 50000")

    requested_auto = changes.get("auto_trade_enabled")
    trade_account_mode = changes.pop("trade_account_mode", None)
    if trade_account_mode is not None:
        try:
            await set_trade_account_mode(trade_account_mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    selected_account = await get_trade_account_mode()
    execution_mode = changes.pop("execution_mode", None)
    if requested_auto is True:
        if selected_account != "demo":
            raise HTTPException(400, "AUTO broker execution is available only for DEMO account")
        execution_mode = "auto"
        changes["auto_trade_regular"] = True
        changes["auto_trade_vip"] = True
        changes["regular_enabled"] = True
    elif requested_auto is False and execution_mode is None:
        execution_mode = "confirm"

    if execution_mode is not None:
        try:
            await set_execution_mode(execution_mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    auto_map = {
        "auto_trade_enabled": "enabled",
        "auto_trade_regular": "regular_enabled",
        "auto_trade_vip": "vip_enabled",
        "trade_amount": "amount",
        "max_open_positions": "max_open_positions",
    }
    auto_changes = {auto_map[key]: changes.pop(key) for key in list(changes) if key in auto_map}
    if changes:
        await update_control(**changes)
    if auto_changes:
        try:
            await update_auto_trade_control(**auto_changes)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    if requested_auto is True:
        await update_trade_runtime(
            stage="SCANNING", pending_signal_id=None, min_payout=MIN_AUTO_PAYOUT,
            message=f"AUTO включён · OCI worker сканирует пары с выплатой ≥{MIN_AUTO_PAYOUT:g}%",
        )
    elif requested_auto is False:
        await update_control(last_vip_status="HUNT_STOPPED", last_scan_at=utcnow())
        await reset_trade_runtime("IDLE", "Автоторговля выключена")

    return await _state_payload(int(user.id))


@router.post("/scan-now")
async def scan_now(_: TelegramMiniAppUser = Depends(admin_user)):
    control = await get_control()
    if control is None:
        raise HTTPException(503, "Admin control is not configured")
    now = utcnow()
    await update_control(regular_enabled=True, last_vip_status=HUNT_REGULAR, last_scan_at=now)
    return {
        "status": "SEARCHING", "vip": False,
        "strategy": control.selected_strategy, "timeframe": control.selected_timeframe,
        "threshold": REGULAR_CONFIDENCE,
        "worker_driven": True,
        "hunt": {"active": True, "kind": "regular", "status": HUNT_REGULAR},
    }


@router.post("/vip-now")
async def vip_now(_: TelegramMiniAppUser = Depends(admin_user)):
    control = await get_control()
    if control is None:
        raise HTTPException(503, "Admin control is not configured")
    now = utcnow()
    await update_control(vip_enabled=True, next_vip_at=now, last_vip_status=HUNT_VIP, last_scan_at=now)
    return {
        "status": "SEARCHING", "vip": True,
        "strategy": "VIP 5M Confluence", "timeframe": "5m",
        "threshold": VIP_CONFIDENCE, "worker_driven": True,
        "hunt": {"active": True, "kind": "vip", "status": HUNT_VIP},
    }


@router.post("/hunt-stop")
async def hunt_stop(_: TelegramMiniAppUser = Depends(admin_user)):
    await update_auto_trade_control(enabled=False)
    await set_execution_mode("confirm")
    await update_control(last_vip_status="HUNT_STOPPED", last_scan_at=utcnow())
    await reset_trade_runtime("IDLE", "Поиск остановлен")
    return {"status": "STOPPED"}


@router.post("/execute/{signal_id}")
async def execute_signal(signal_id: int, _: TelegramMiniAppUser = Depends(admin_user)):
    async with AsyncSessionLocal() as db:
        signal = await db.get(Signal, signal_id)
        if signal is None:
            raise HTTPException(404, "Signal not found")
    raise HTTPException(409, "Direct broker execution is worker-only; start an AUTO DEMO session")


@router.get("/diagnostics")
async def diagnostics(user: TelegramMiniAppUser = Depends(admin_user)):
    state = await _state_payload(int(user.id))
    return {
        "market": state.get("market"),
        "worker": state.get("worker"),
        "scanner": {
            "last_scan_at": state.get("last_scan_at"), "hunt": state.get("hunt"),
            "regular_enabled": state.get("regular_enabled"), "vip_enabled": state.get("vip_enabled"),
        },
        "trading": {
            "enabled": state.get("auto_trade_enabled"), "execution_mode": state.get("execution_mode"),
            "selected_account": state.get("trade_account_mode"), "connected_account": state.get("trade_account"),
            "account_matches_mode": state.get("account_matches_mode"), "balance": state.get("pocket_balance"),
            "min_auto_payout": state.get("min_auto_payout"), "runtime": state.get("auto_runtime"),
            "latest_execution": state.get("latest_execution"),
        },
    }
