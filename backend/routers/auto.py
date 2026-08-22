from __future__ import annotations

import json
import hashlib
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal
from backend.services.auto_trade import MIN_AUTO_PAYOUT
from backend.services.session_engine import session_history, session_state
from backend.services.worker_protocol import ensure_demo_account, enqueue_command
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _command_key(user_id: int, command_type: str, payload: dict, provided: str | None) -> str:
    if provided and provided.strip():
        return provided.strip()[:128]
    # Browser retries of the same action within a short window resolve to the
    # same command even when an older client does not yet send an explicit key.
    bucket = int(datetime.now(timezone.utc).timestamp() // 10)
    raw = json.dumps(
        {"user": int(user_id), "type": command_type, "payload": payload, "bucket": bucket},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        text_value = value.isoformat()
        return text_value if "+" in text_value[-6:] or text_value.endswith("Z") else text_value + "Z"
    return value


def _row(row):
    if row is None:
        return None
    data = dict(row)
    for key, value in list(data.items()):
        data[key] = _iso(value)
    return data


def _session_metrics(session: dict, legs: list[dict]) -> dict:
    wins = sum(1 for leg in legs if str(leg.get("result") or "").upper() == "WIN")
    losses = sum(1 for leg in legs if str(leg.get("result") or "").upper() == "LOSS")
    draws = sum(1 for leg in legs if str(leg.get("result") or "").upper() == "DRAW")
    pending = sum(1 for leg in legs if str(leg.get("result") or "").upper() == "PENDING")
    covered = sum(1 for leg in legs if int(leg.get("martingale_level") or 0) > 0)
    total_staked = round(sum(float(leg.get("amount") or 0) for leg in legs), 2)
    gross_wins = round(sum(float(leg.get("pnl") or 0) for leg in legs if float(leg.get("pnl") or 0) > 0), 2)
    gross_losses = round(abs(sum(float(leg.get("pnl") or 0) for leg in legs if float(leg.get("pnl") or 0) < 0)), 2)
    start_balance = session.get("start_balance")
    end_balance = session.get("current_balance")
    balance_change = None
    try:
        if start_balance is not None and end_balance is not None:
            balance_change = round(float(end_balance) - float(start_balance), 2)
    except (TypeError, ValueError):
        pass
    return {
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "pending": pending,
        "closed": wins + losses + draws,
        "covered_trades": covered,
        "total_staked": total_staked,
        "gross_wins": gross_wins,
        "gross_losses": gross_losses,
        "net_profit": round(float(session.get("profit") or 0), 2),
        "start_balance": start_balance,
        "end_balance": end_balance,
        "balance_change": balance_change,
        "winrate": round((wins / (wins + losses) * 100), 1) if wins + losses else None,
    }


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
    session["metrics"] = _session_metrics(session, legs)
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
    live_events = []
    now = _now_iso()
    runtime_message = str(runtime.get("message") or "").strip()
    if runtime_message:
        live_events.append({"id": f"runtime-{stage}", "stage": runtime.get("stage") or stage, "message": runtime_message, "created_at": runtime.get("updated_at") or now, "payload": {"live": True}})
    live_events.append({"id": f"bet-{stage}-{session.get('current_level', 0)}", "stage": "BET", "message": amount_line, "created_at": now, "payload": {"current_bet": session.get("current_bet_amount"), "next_bet": session.get("next_bet_amount"), "level": session.get("current_level")}})
    if stage in {"SCANNING", "MARTINGALE"}:
        live_events.append({"id": f"scan-{stage}", "stage": "ANALYSIS", "message": "Анализ рынка активен · следующий сетап ищется сразу после закрытия предыдущей сделки", "created_at": now, "payload": {"live": True}})
    merged = live_events + events
    seen = set()
    output = []
    for event in merged:
        key = (str(event.get("stage")), str(event.get("message")))
        if key in seen:
            continue
        seen.add(key)
        output.append(event)
        if len(output) >= 40:
            break
    payload["events"] = output
    payload["screen_notifications"] = output
    payload["session"] = session
    return payload


@router.get("/state")
async def state(refresh: bool = Query(False), drive: bool = Query(False), _: TelegramMiniAppUser = Depends(admin_user)):
    payload = await session_state(refresh_balance=refresh)
    payload["driver"] = {"status": "WORKER_DRIVEN", "legacy_drive_ignored": bool(drive)}
    return _decorate_live_state(payload)


@router.post("/tick")
async def tick(_: TelegramMiniAppUser = Depends(admin_user)):
    return {"status": "WORKER_DRIVEN"}


@router.post("/start")
async def start(
    data: StartSessionRequest,
    user: TelegramMiniAppUser = Depends(admin_user),
    x_idempotency_key: str | None = Header(default=None),
):
    body = data.model_dump()
    account_id = await ensure_demo_account(int(user.id))
    command = await enqueue_command(
        account_id=account_id,
        command_type="START_SESSION",
        payload=body,
        idempotency_key=_command_key(int(user.id), "START_SESSION", body, x_idempotency_key),
    )
    payload = _decorate_live_state(await session_state())
    payload["command"] = command
    payload["driver"] = {"status": "QUEUED"}
    return payload


@router.post("/stop")
async def stop(
    user: TelegramMiniAppUser = Depends(admin_user),
    x_idempotency_key: str | None = Header(default=None),
):
    body = {"reason": "USER_STOP"}
    account_id = await ensure_demo_account(int(user.id))
    command = await enqueue_command(
        account_id=account_id,
        command_type="STOP_SESSION",
        payload=body,
        idempotency_key=_command_key(int(user.id), "STOP_SESSION", body, x_idempotency_key),
    )
    payload = _decorate_live_state(await session_state())
    payload["command"] = command
    payload["driver"] = {"status": "QUEUED"}
    return payload


@router.get("/history")
async def history(limit: int = Query(30, ge=1, le=100), _: TelegramMiniAppUser = Depends(admin_user)):
    return await session_history(limit)


@router.get("/history/{session_id}")
async def history_detail(session_id: int, user: TelegramMiniAppUser = Depends(admin_user)):
    async with AsyncSessionLocal() as db:
        session = (await db.execute(text("SELECT * FROM auto_trade_sessions WHERE id=:sid AND telegram_id=:tid LIMIT 1"), {"sid": session_id, "tid": int(user.id)})).mappings().first()
        if not session:
            raise HTTPException(404, "Session not found")
        legs = (await db.execute(text("SELECT * FROM auto_trade_legs WHERE session_id=:sid ORDER BY id ASC"), {"sid": session_id})).mappings().all()
        events = (await db.execute(text("SELECT id,stage,message,payload,created_at FROM auto_trade_events WHERE session_id=:sid ORDER BY id ASC"), {"sid": session_id})).mappings().all()
    session_data = _row(session)
    leg_rows = [_row(item) for item in legs]
    event_rows = [_row(item) for item in events]
    for event in event_rows:
        raw = event.get("payload")
        if isinstance(raw, str):
            try:
                event["payload"] = json.loads(raw or "{}")
            except Exception:
                event["payload"] = {}
    return {"session": session_data, "metrics": _session_metrics(session_data, leg_rows), "legs": leg_rows, "events": event_rows}
