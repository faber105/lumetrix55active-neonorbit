from __future__ import annotations

import asyncio
import hashlib
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from backend.services.realtime_tokens import verify_realtime_token
from backend.services.worker_protocol import realtime_snapshot, worker_id, worker_version


app = FastAPI(
    title="AlphaPulse worker realtime",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def _digest(payload: dict) -> str:
    body = json.dumps(jsonable_encoder(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.blake2s(body.encode("utf-8"), digest_size=12).hexdigest()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "alphapulse-worker",
        "worker_id": worker_id(),
        "version": worker_version(),
    }


@app.websocket("/ws/live")
async def live(websocket: WebSocket):
    await websocket.accept()
    try:
        auth = await asyncio.wait_for(websocket.receive_json(), timeout=8)
        if not isinstance(auth, dict) or auth.get("type") != "auth":
            raise ValueError("Authentication required")
        claims = verify_realtime_token(str(auth.get("token") or ""))
        account_id = int(claims["account_id"])
        after_sequence = max(0, int(auth.get("last_sequence") or 0))
        snapshot = await realtime_snapshot(account_id, after_sequence=after_sequence)
        if int(snapshot["account"]["owner_telegram_id"]) != int(claims["sub"]):
            await websocket.close(code=4403, reason="Account access denied")
            return
    except WebSocketDisconnect:
        return
    except Exception:
        await websocket.close(code=4401, reason="Invalid realtime authentication")
        return

    await websocket.send_json({"type": "ready", "transport": "worker-wss"})
    last_digest = ""
    try:
        while True:
            snapshot = await realtime_snapshot(account_id, after_sequence=after_sequence)
            digest = _digest(snapshot)
            if digest != last_digest:
                await websocket.send_json({"type": "auto_state", "data": jsonable_encoder(snapshot)})
                last_digest = digest
                after_sequence = int(snapshot.get("sequence") or after_sequence)
            await asyncio.sleep(0.2)
    except (WebSocketDisconnect, RuntimeError):
        return
    except asyncio.CancelledError:
        raise
