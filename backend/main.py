from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.github_oidc import verify as verify_oidc
from backend.routers import auth, market, settings, signals, stats, websocket
from backend.services.database import init_db
from backend.services.pocketoption_otc import market_data
from backend.services.scanner import scan_tick
from bot.main import bot, configure_webhook, feed_update, valid_secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alphapulse")


@asynccontextmanager
async def lifespan(app):
    await init_db()
    try:
        await configure_webhook()
    except Exception:
        logger.exception("Webhook setup failed; API remains available")
    yield
    try:
        await market_data.close()
    except Exception:
        pass


app = FastAPI(title="AlphaPulse API", version="2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(signals.router, prefix="/api/signals", tags=["signals"])
app.include_router(market.router, prefix="/api/market", tags=["market"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(websocket.router, prefix="/ws", tags=["websocket"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "alphapulsesbot", "market": await market_data.health()}


@app.post("/telegram/webhook")
async def telegram_webhook(
    payload: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not valid_secret(x_telegram_bot_api_secret_token):
        raise HTTPException(403, "Invalid Telegram webhook secret")
    await feed_update(payload)
    return {"ok": True}


@app.post("/api/internal/scan")
async def internal_scan(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer token required")
    await verify_oidc(authorization.split(" ", 1)[1])
    return await scan_tick(bot)


# Mount last so API/webhook routes keep priority. During the Vercel build the
# original second-archive Mini App is compiled to miniapp/dist and bundled with
# the Python function. StaticFiles(html=True) also provides SPA index fallback.
DIST_DIR = Path(__file__).resolve().parents[1] / "miniapp" / "dist"
if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="miniapp")
