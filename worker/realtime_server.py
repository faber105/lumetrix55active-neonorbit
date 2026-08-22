from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.routers import admin, admin_stats, auth, auto, home, live, market, preload, settings, signals, stats, websocket
from backend.services.realtime_tokens import verify_realtime_token
from backend.services.worker_protocol import realtime_snapshot, worker_version


app = FastAPI(
    title="AlphaPulse Windows gateway",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# The persistent Windows worker is now also the control-plane gateway for the
# Telegram Mini App. This keeps browser/API traffic off the trading loop while
# removing the dependency on a blocked Vercel deployment. Router-level Telegram
# init-data checks remain authoritative for protected endpoints.
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(home.router, prefix="/api/home", tags=["home"])
app.include_router(signals.router, prefix="/api/signals", tags=["signals"])
app.include_router(market.router, prefix="/api/market", tags=["market"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(live.router, prefix="/api/live", tags=["live"])
app.include_router(auto.router, prefix="/api/auto", tags=["auto"])
app.include_router(preload.router, prefix="/api/auto-preload", tags=["auto-preload"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(admin_stats.router, prefix="/api/admin-stats", tags=["admin-stats"])
app.include_router(websocket.router, prefix="/ws", tags=["websocket"])

DIST_DIR = Path(__file__).resolve().parents[1] / "miniapp" / "dist"
ASSETS_DIR = DIST_DIR / "assets"


def _digest(payload: dict) -> str:
    body = json.dumps(jsonable_encoder(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.blake2s(body.encode("utf-8"), digest_size=12).hexdigest()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "alphapulse-worker-gateway",
        "version": worker_version(),
        "miniapp_built": DIST_DIR.exists(),
    }


@app.get("/assets/{asset_path:path}", include_in_schema=False)
async def miniapp_asset(asset_path: str):
    if not ASSETS_DIR.exists():
        raise HTTPException(404, "Mini App assets are not built")
    requested = ASSETS_DIR / Path(asset_path).name
    if requested.is_file():
        return FileResponse(requested)
    suffix = requested.suffix.lower()
    if suffix not in {".js", ".css"}:
        raise HTTPException(404, "Asset not found")
    candidates = sorted(
        (p for p in ASSETS_DIR.glob(f"index-*{suffix}") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise HTTPException(404, "Asset not found")
    return FileResponse(candidates[0], headers={"Cache-Control": "no-store, max-age=0"})


@app.websocket("/ws/live")
async def live_worker(websocket: WebSocket):
    await websocket.accept()
    try:
        auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=8)
        if not isinstance(auth_message, dict) or auth_message.get("type") != "auth":
            raise ValueError("Authentication required")
        claims = verify_realtime_token(str(auth_message.get("token") or ""))
        account_id = int(claims["account_id"])
        after_sequence = max(0, int(auth_message.get("last_sequence") or 0))
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


if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="miniapp")
