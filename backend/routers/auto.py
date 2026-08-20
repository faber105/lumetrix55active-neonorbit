from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.services.session_driver import drive_session_tick
from backend.services.session_engine import session_history, session_state, start_session, stop_session
from backend.telegram_auth import TelegramMiniAppUser, admin_user

router = APIRouter()


class StartSessionRequest(BaseModel):
    mode: str = Field(pattern="^(count|profit)$")
    strategy: str
    timeframe: str | None = None
    target_wins: int | None = None
    target_profit: float | None = None
    amount: float = 1.0
    max_martingale: int = 3
    max_failed_series: int = 1


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
            # The UI must keep receiving the persisted session even when Pocket
            # is temporarily reconnecting. The next poll retries automatically.
            tick_result = {"status": "ERROR", "error": type(exc).__name__}
    payload = await session_state(refresh_balance=refresh)
    payload["driver"] = tick_result
    return payload


@router.post("/tick")
async def tick(_: TelegramMiniAppUser = Depends(admin_user)):
    """Drive one throttled AUTO iteration while the Mini App is open."""
    return await drive_session_tick()


@router.post("/start")
async def start(data: StartSessionRequest, _: TelegramMiniAppUser = Depends(admin_user)):
    try:
        return await start_session(data.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/stop")
async def stop(_: TelegramMiniAppUser = Depends(admin_user)):
    return await stop_session("USER_STOP")


@router.get("/history")
async def history(
    limit: int = Query(30, ge=1, le=100),
    _: TelegramMiniAppUser = Depends(admin_user),
):
    return await session_history(limit)
