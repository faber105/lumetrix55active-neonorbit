from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from backend.services.auto_realtime import (
    current_revision,
    driver_health,
    wait_for_auto_change,
)
from backend.services.session_engine import session_state
from backend.telegram_auth import _verify_init_data, is_admin_id

router = APIRouter()


def _digest(payload: dict) -> str:
    raw = json.dumps(
        jsonable_encoder(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.blake2s(raw, digest_size=12).hexdigest()


async def _auto_state_payload() -> dict:
    # Import at call time to avoid a router import cycle during FastAPI startup.
    from backend.routers.auto import _decorate_live_state

    return _decorate_live_state(await session_state())


@router.get("/status")
async def status():
    return {
        "enabled": True,
        "transport": "websocket-push",
        "path": "/ws/auto",
        "driver": driver_health(),
    }


@router.websocket("/auto")
async def auto_stream(websocket: WebSocket):
    """Push AUTO state changes to the authenticated Telegram Mini App.

    Telegram initData is sent as the first WebSocket message instead of a query
    parameter so it is not exposed in access-log URLs.
    """
    await websocket.accept()
    try:
        auth = await asyncio.wait_for(websocket.receive_json(), timeout=8)
        if not isinstance(auth, dict) or auth.get("type") != "auth":
            await websocket.close(code=4401, reason="Authentication required")
            return
        user = _verify_init_data(str(auth.get("init_data") or ""))
        if not is_admin_id(user.id):
            await websocket.close(code=4403, reason="Admin access required")
            return
    except WebSocketDisconnect:
        return
    except Exception:
        await websocket.close(code=4401, reason="Invalid Telegram authentication")
        return

    health = driver_health()
    await websocket.send_json(
        {
            "type": "ready",
            "realtime_driver": bool(health.get("running")),
            "server_time": datetime.now(timezone.utc).isoformat(),
        }
    )

    last_digest = ""
    last_sent = 0.0
    revision = current_revision()
    try:
        while True:
            payload = await _auto_state_payload()
            digest = _digest(payload)
            now = time.monotonic()
            heartbeat_due = now - last_sent >= 10.0
            if digest != last_digest or heartbeat_due:
                await websocket.send_json(
                    {
                        "type": "auto_state",
                        "data": jsonable_encoder(payload),
                        "revision": revision,
                        "server_time": datetime.now(timezone.utc).isoformat(),
                        "heartbeat": digest == last_digest,
                    }
                )
                last_digest = digest
                last_sent = now

            revision = await wait_for_auto_change(revision, timeout=0.25)
    except (WebSocketDisconnect, RuntimeError):
        return
    except asyncio.CancelledError:
        raise
