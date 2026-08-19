from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.github_oidc import verify_github_actions_token
from api.models.database import init_db
from api.routers import admin, auth, sessions, signals, subscriptions, users, verification
from config import get_settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(title=settings.project_name, version='2.1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(auth.router, prefix='/api')
app.include_router(signals.router, prefix='/api')
app.include_router(sessions.router, prefix='/api')
app.include_router(subscriptions.router, prefix='/api')
app.include_router(users.router, prefix='/api')
app.include_router(admin.router, prefix='/api')
app.include_router(verification.router)


@app.get('/health')
async def health() -> dict[str, str]:
    return {'status': 'ok', 'project': settings.project_name, 'mode': 'vercel-webhook-otc'}


@app.post('/telegram/webhook')
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    from bot.runtime import feed_update, valid_webhook_secret

    if not valid_webhook_secret(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail='Invalid Telegram webhook secret')
    await feed_update(await request.json())
    return {'ok': True}


@app.post('/api/internal/scan')
async def scheduled_scan(authorization: str | None = Header(default=None)) -> dict[str, int | str]:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing GitHub Actions OIDC token')
    await verify_github_actions_token(authorization[7:])

    from bot.runtime import bot
    from signal_engine.ml_store import load_online_model_from_db, save_online_model_to_db
    from signal_engine.otc_scanner import OTCScanner

    await load_online_model_from_db()
    scanner = OTCScanner(bot)
    try:
        resolved = await scanner.resolve_expired()
        if resolved:
            await save_online_model_to_db()
        published = await scanner.scan_once()
        return {'status': 'ok', 'resolved': resolved, 'published': published}
    finally:
        await scanner.stop()


@app.on_event('startup')
async def startup() -> None:
    try:
        await init_db()
        logger.info('Database is ready')
    except Exception:
        logger.exception('Database startup failed; API health/webhook boot will remain available')
    try:
        from bot.runtime import configure_webhook
        await configure_webhook()
    except Exception:
        logger.exception('Telegram webhook setup failed; API remains available')


DIST = Path(__file__).resolve().parents[1] / 'mini_app' / 'dist'
ASSETS = DIST / 'assets'
if ASSETS.is_dir():
    app.mount('/assets', StaticFiles(directory=ASSETS), name='assets')


@app.get('/')
async def mini_app_index():
    index = DIST / 'index.html'
    if index.exists():
        return FileResponse(index)
    return {'status': 'ok', 'message': 'Mini App build is not present'}


@app.get('/{path:path}')
async def mini_app_spa(path: str):
    candidate = DIST / path
    if candidate.is_file() and DIST in candidate.resolve().parents:
        return FileResponse(candidate)
    index = DIST / 'index.html'
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail='Not found')
