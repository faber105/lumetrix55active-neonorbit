from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.services.auto_trade import MIN_AUTO_PAYOUT
from backend.services.session_driver import drive_session_tick
from backend.services.session_engine import session_history, session_state, start_session, stop_session
from backend.services.trade_mode import set_execution_mode
from backend.telegram_auth import TelegramMiniAppUser, admin_user

router = APIRouter()
MAX_SESSION_AMOUNT = 50000.0


class StartSessionRequest(BaseModel):
    mode: str = Field(pattern="^(count|profit)$")
    strategy: str
    timeframe: str | None = None
    target_wins: int | None = None
    target_profit: float | None = None
    amount: float = 1.0
    max_martingale: int = 3
    max_failed_series: int = 1


def _next_bet_amount(session: dict, legs: list[dict], runtime: dict) -> float:
    base = float(session.get("base_amount") or 1.0)
    level = int(session.get("current_level") or 0)
    if level <= 0:
        return round(base, 2)

    payout = runtime.get("payout_percent")
    if payout is None and legs:
        payout = legs[0].get("payout")
    try:
        payout = float(payout or MIN_AUTO_PAYOUT)
    except (TypeError, ValueError):
        payout = float(MIN_AUTO_PAYOUT)

    ratio = max(payout / 100.0, 0.01)
    recovery = float(session.get("current_series_loss") or 0)
    target = base * ratio
    amount = math.ceil(((recovery + target) / ratio) * 100) / 100
    return round(min(MAX_SESSION_AMOUNT, max(base, amount)), 2)


def _decorate_live_state(payload: dict) -> dict:
    session = payload.get("session") or {}
    if not session:
        return payload

    legs = payload.get("legs") or []
    events = payload.get("events") or []
    runtime = payload.get("runtime") or {}
    active_position_id = session.get("active_position_id")

    current_bet = None
    if active_position_id:
        for leg in legs:
            if int(leg.get("position_id") or 0) == int(active_position_id):
                current_bet = float(leg.get("amount") or 0)
                break
    if current_bet is None and str(session.get("stage") or "") in {"SIGNAL_FOUND", "WAIT_ENTRY", "SCHEDULED", "OPENING"}:
        try:
            current_bet = float(runtime.get("amount") or 0) or None
        except (TypeError, ValueError):
            current_bet = None

    next_bet = _next_bet_amount(session, legs, runtime)
    if current_bet is None and int(session.get("current_level") or 0) <= 0:
        current_bet = float(session.get("base_amount") or next_bet)

    session["current_bet_amount"] = round(float(current_bet), 2) if current_bet is not None else None
    session["next_bet_amount"] = round(float(next_bet), 2)

    stage = str(session.get("stage") or "SCANNING")
    base_message = str(session.get("last_message") or runtime.get("message") or "AUTO сессия активна")
    if stage == "OPEN" and current_bet is not None:
        amount_line = f"Текущая ставка: {current_bet:.2f}"
    elif int(session.get("current_level") or 0) > 0:
        amount_line = f"Следующая ставка перекрытия: {next_bet:.2f}"
    else:
        amount_line = f"Следующая ставка: {next_bet:.2f}"
    if amount_line not in base_message:
        session["last_message"] = f"{base_message} · {amount_line}"

    notifications = []
    if runtime.get("message"):
        notifications.append({"stage": runtime.get("stage") or stage, "message": runtime.get("message"), "created_at": runtime.get("updated_at")})
    for event in events[:8]:
        notifications.append({
            "stage": event.get("stage"),
            "message": event.get("message"),
            "created_at": event.get("created_at"),
        })
    payload["screen_notifications"] = notifications[:8]
    payload["session"] = session
    return payload


@router.get("/state")
async def state(
    refresh: bool = Query(False),
    drive: bool = Query(True),
    _: TelegramMiniAppUser = Depends(admin_user),
):
    tick_result = None
    if drive:
        try:
            tick_result = await drive_session_tick()
        except Exception as exc:
            tick_result = {"status": "ERROR", "error": type(exc).__name__}
    payload = await session_state(refresh_balance=refresh)
    payload["driver"] = tick_result
    return _decorate_live_state(payload)


@router.post("/tick")
async def tick(_: TelegramMiniAppUser = Depends(admin_user)):
    """Drive one throttled AUTO iteration while the Mini App is open."""
    return await drive_session_tick()


@router.post("/start")
async def start(data: StartSessionRequest, _: TelegramMiniAppUser = Depends(admin_user)):
    try:
        return _decorate_live_state(await start_session(data.model_dump()))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/stop")
async def stop(_: TelegramMiniAppUser = Depends(admin_user)):
    await set_execution_mode("confirm")
    return _decorate_live_state(await stop_session("USER_STOP"))


@router.get("/history")
async def history(
    limit: int = Query(30, ge=1, le=100),
    _: TelegramMiniAppUser = Depends(admin_user),
):
    return await session_history(limit)
