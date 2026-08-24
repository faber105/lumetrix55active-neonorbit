from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, Signal, utcnow
from backend.services.auto_trade import MIN_AUTO_PAYOUT, update_auto_trade_control
from backend.services.control import VALID_STRATEGIES, VALID_TIMEFRAMES, get_control, update_control
from backend.services.trade_mode import get_trade_account_mode, set_execution_mode, set_trade_account_mode
from backend.services.trade_runtime import reset_trade_runtime, update_trade_runtime
from backend.telegram_auth import TelegramMiniAppUser, admin_user

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


def _dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        return dict(value)
    except Exception:
        return {}


def _runtime(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except Exception:
        return {}


async def _state_payload(user_id: int) -> dict:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text("""
                    WITH account AS (
                        SELECT id,owner_telegram_id,mode,status
                          FROM broker_accounts
                         WHERE owner_telegram_id=:uid AND mode='DEMO' AND status='ACTIVE'
                         ORDER BY id DESC LIMIT 1
                    ), lease AS (
                        SELECT l.worker_id,l.lease_until,l.generation,w.hostname,w.version,
                               w.heartbeat_at,w.status AS reported_status,
                               EXTRACT(EPOCH FROM (NOW() - w.heartbeat_at)) AS heartbeat_age
                          FROM worker_leases l
                          LEFT JOIN workers w ON w.id=l.worker_id
                         WHERE l.account_id=(SELECT id FROM account)
                    )
                    SELECT
                        (SELECT row_to_json(c) FROM admin_control c WHERE c.telegram_id=:uid) AS control,
                        (SELECT row_to_json(a) FROM auto_trade_control a WHERE a.telegram_id=:uid) AS auto_control,
                        (SELECT payload FROM ml_state WHERE strategy='__trade_account_mode__') AS account_mode,
                        (SELECT payload FROM ml_state WHERE strategy='__trade_execution_mode__') AS execution_mode,
                        (SELECT payload FROM ml_state WHERE strategy='__auto_trade_runtime__') AS runtime,
                        (SELECT row_to_json(s) FROM signals s ORDER BY s.created_at DESC LIMIT 1) AS latest_signal,
                        (SELECT COUNT(*) FROM paper_positions p WHERE p.status='OPEN') AS open_positions,
                        (SELECT row_to_json(e) FROM trade_executions e
                          WHERE e.telegram_id=:uid ORDER BY e.created_at DESC LIMIT 1) AS latest_execution,
                        (SELECT row_to_json(l) FROM lease l) AS lease
                """),
                {"uid": int(user_id)},
            )
        ).mappings().one()

    control = _dict(row.get("control"))
    auto_control = _dict(row.get("auto_control"))
    runtime = _runtime(row.get("runtime"))
    worker = _dict(row.get("lease"))
    latest_signal = _dict(row.get("latest_signal")) or None
    latest_execution = _dict(row.get("latest_execution")) or None

    age = worker.pop("heartbeat_age", None)
    try:
        age = max(0.0, float(age)) if age is not None else None
    except Exception:
        age = None
    worker_status = "ONLINE" if age is not None and age <= 10 else ("DEGRADED" if age is not None and age <= 20 else "OFFLINE")
    worker["status"] = worker_status
    worker["heartbeat_age_seconds"] = round(age, 3) if age is not None else None

    account_mode = str(row.get("account_mode") or "demo").strip().lower()
    if account_mode not in {"demo", "real"}:
        account_mode = "demo"
    execution_mode = str(row.get("execution_mode") or "confirm").strip().lower()
    if execution_mode not in {"auto", "confirm"}:
        execution_mode = "confirm"

    hunt_status = str(control.get("last_vip_status") or "")
    payout_map = runtime.get("eligible_payouts") or runtime.get("payouts") or {}
    balance = runtime.get("balance")
    next_vip_at = control.get("next_vip_at")
    vip_remaining = None
    if next_vip_at is not None:
        try:
            vip_remaining = max(0, int((next_vip_at - utcnow()).total_seconds()))
        except Exception:
            vip_remaining = None

    return {
        "configured": bool(control),
        "telegram_id": int(user_id),
        "selected_strategy": control.get("selected_strategy") or "ema_trend",
        "selected_timeframe": control.get("selected_timeframe") or "1m",
        "regular_enabled": bool(control.get("regular_enabled", True)),
        "vip_enabled": bool(control.get("vip_enabled", True)),
        "vip_interval_seconds": int(control.get("vip_interval_seconds") or 300),
        "next_vip_at": next_vip_at,
        "last_vip_at": control.get("last_vip_at"),
        "last_vip_status": control.get("last_vip_status"),
        "last_scan_at": control.get("last_scan_at"),
        "auto_trade_enabled": bool(auto_control.get("enabled", False)),
        "auto_trade_regular": bool(auto_control.get("regular_enabled", True)),
        "auto_trade_vip": bool(auto_control.get("vip_enabled", True)),
        "trade_amount": float(auto_control.get("amount") or 1.0),
        "max_open_positions": int(auto_control.get("max_open_positions") or 1),
        "market": {
            "configured": worker_status in {"ONLINE", "DEGRADED"},
            "connected": worker_status == "ONLINE",
            "demo": True,
            "provider": "OCI worker / Pocket Option DEMO",
            "worker_status": worker_status,
            "heartbeat_age_seconds": worker.get("heartbeat_age_seconds"),
        },
        "worker": worker,
        "open_positions": int(row.get("open_positions") or 0),
        "latest_signal": latest_signal,
        "latest_execution": latest_execution,
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
            "last_scan_at": control.get("last_scan_at"),
        },
        "vip_seconds_remaining": vip_remaining,
    }


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
