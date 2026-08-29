from __future__ import annotations
import json, logging, os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from backend.github_oidc import verify as verify_oidc
from backend.models.db_models import AsyncSessionLocal, MLState
from backend.routers import admin, admin_stats, auth, auto, home, live, market, preload, settings, signals, stats, websocket
from backend.services.database import init_db
from backend.services.pocketoption_otc import market_data
from backend.services import auto_recovery as _auto_recovery
from backend.services.scanner import scan_tick
from backend.services.cpu_guard import adaptive_drive_session_tick
from backend.services.vip_runtime_fix import run_due_vip
from backend.services.session_engine import ensure_schema
from backend.services.preload_next import ensure_preload_schema
from backend.services.auto_realtime import (
    driver_health,
    start_auto_realtime_driver,
    stop_auto_realtime_driver,
)


def _deployment_base_url() -> str:
    explicit = str(os.getenv('PUBLIC_BACKEND_URL') or os.getenv('BACKEND_URL') or '').strip().rstrip('/')
    if explicit:
        return explicit
    host = str(
        os.getenv('RAILWAY_PUBLIC_DOMAIN')
        or os.getenv('RENDER_EXTERNAL_HOSTNAME')
        or os.getenv('VERCEL_PROJECT_PRODUCTION_URL')
        or os.getenv('VERCEL_URL')
        or ''
    ).strip().rstrip('/')
    if not host:
        return ''
    if host.startswith('http://') or host.startswith('https://'):
        return host
    return f'https://{host}'


BACKEND_URL = _deployment_base_url()
if BACKEND_URL:
    os.environ['BACKEND_URL'] = BACKEND_URL
    os.environ['MINI_APP_URL'] = BACKEND_URL

logging.basicConfig(level=logging.INFO)
logger=logging.getLogger('alphapulse')
TELEGRAM_ENABLED=bool(os.getenv('TELEGRAM_BOT_TOKEN','').strip())
if TELEGRAM_ENABLED:
    from bot.main import bot, configure_webhook, feed_update, valid_secret, webhook_secret
else:
    bot=None
    async def configure_webhook():return None
    async def feed_update(payload):del payload;raise RuntimeError('Telegram is not configured in this deployment')
    def valid_secret(value):del value;return False
    def webhook_secret():return ''


async def _write_diag(key:str,payload:dict) -> None:
    if not os.getenv('DATABASE_URL','').strip():
        return
    try:
        body=json.dumps({'at':datetime.now(timezone.utc).isoformat(),**payload},ensure_ascii=False)
        async with AsyncSessionLocal() as db:
            state=await db.get(MLState,key)
            if state:
                state.payload=body
            else:
                db.add(MLState(strategy=key,payload=body,samples=0))
            await db.commit()
    except Exception:
        logger.exception('Cannot persist Telegram diagnostic %s',key)


async def repair_telegram_webhook() -> dict:
    if bot is None:
        await _write_diag('__telegram_webhook_diag__',{'ok':False,'error':'telegram_not_configured','backend_url':BACKEND_URL or None})
        raise HTTPException(503,'Telegram is not configured in this deployment')
    if not BACKEND_URL.startswith('https://'):
        await _write_diag('__telegram_webhook_diag__',{'ok':False,'error':'backend_url_not_configured','backend_url':BACKEND_URL or None})
        raise HTTPException(503,'Production backend URL is not configured')
    target=f'{BACKEND_URL}/api/telegram/webhook'
    try:
        await bot.set_webhook(
            url=target,
            secret_token=webhook_secret(),
            drop_pending_updates=False,
        )
        info=await bot.get_webhook_info()
        actual=str(info.url or '')
        result={
            'ok':actual==target,
            'target':target,
            'actual':actual,
            'pending_update_count':int(info.pending_update_count or 0),
            'last_error_message':str(info.last_error_message or '')[:300] or None,
        }
        await _write_diag('__telegram_webhook_diag__',result)
        logger.info('Telegram webhook repair target=%s actual=%s ok=%s',target,actual,result['ok'])
        return result
    except Exception as exc:
        await _write_diag('__telegram_webhook_diag__',{'ok':False,'target':target,'error_type':type(exc).__name__,'error':str(exc)[:300]})
        raise


@asynccontextmanager
async def lifespan(app):
    del app
    if os.getenv('DATABASE_URL','').strip():
        await init_db()
        try:await ensure_schema()
        except Exception:logger.exception('AUTO session schema bootstrap failed')
        try:await ensure_preload_schema()
        except Exception:logger.exception('AUTO preload schema bootstrap failed')
    else:logger.warning('DATABASE_URL is not configured; database routes are disabled in this deployment')
    try:
        if bot is not None and BACKEND_URL:
            await repair_telegram_webhook()
        else:
            await configure_webhook()
    except Exception:
        logger.exception('Webhook setup failed; API remains available')
    try:
        await start_auto_realtime_driver()
    except Exception:
        logger.exception('Persistent AUTO realtime driver failed to start')
    yield
    try:await stop_auto_realtime_driver()
    except Exception:pass
    try:await market_data.close()
    except Exception:pass


app=FastAPI(title='AlphaPulse API',version='3.4',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['*'],allow_headers=['*'])


@app.middleware('http')
async def miniapp_cache_headers(request,call_next):
    response=await call_next(request)
    if request.method=='GET' and request.url.path in {'/','/index.html'}:
        response.headers['Cache-Control']='no-store, max-age=0'
        response.headers['Pragma']='no-cache'
        response.headers['Expires']='0'
    return response


app.include_router(auth.router,prefix='/api/auth',tags=['auth'])
app.include_router(home.router,prefix='/api/home',tags=['home'])
app.include_router(signals.router,prefix='/api/signals',tags=['signals'])
app.include_router(market.router,prefix='/api/market',tags=['market'])
app.include_router(stats.router,prefix='/api/stats',tags=['stats'])
app.include_router(settings.router,prefix='/api/settings',tags=['settings'])
app.include_router(live.router,prefix='/api/live',tags=['live'])
app.include_router(auto.router,prefix='/api/auto',tags=['auto'])
app.include_router(preload.router,prefix='/api/auto-preload',tags=['auto-preload'])
app.include_router(admin.router,prefix='/api/admin',tags=['admin'])
app.include_router(admin_stats.router,prefix='/api/admin-stats',tags=['admin-stats'])
app.include_router(websocket.router,prefix='/ws',tags=['websocket'])


async def _health_payload():
    return {
        'status':'ok','service':'alphapulsesbot','version':'3.4',
        'scanner':'adaptive-timeframe-driver','telegram_configured':TELEGRAM_ENABLED,
        'database_configured':bool(os.getenv('DATABASE_URL','').strip()),
        'backend_url':BACKEND_URL or None,
        'auto_realtime':driver_health(),
        'source':{
            'provider':os.getenv('VERCEL_GIT_PROVIDER','manual'),
            'repository':'/'.join(p for p in (os.getenv('VERCEL_GIT_REPO_OWNER',''),os.getenv('VERCEL_GIT_REPO_SLUG','')) if p) or 'unknown',
            'ref':os.getenv('VERCEL_GIT_COMMIT_REF','unknown'),
            'sha':os.getenv('VERCEL_GIT_COMMIT_SHA','unknown'),
        },
        'market':await market_data.health(),
    }


@app.get('/health')
async def health():
    return await _health_payload()


@app.get('/api/health')
async def api_health():
    return await _health_payload()


@app.post('/api/internal/telegram-repair')
async def internal_telegram_repair():
    return await repair_telegram_webhook()


async def _telegram_webhook_handler(payload:dict,x_telegram_bot_api_secret_token:str|None):
    if not TELEGRAM_ENABLED:
        raise HTTPException(503,'Telegram is not configured in this deployment')
    secret_ok=valid_secret(x_telegram_bot_api_secret_token)
    message=payload.get('message') if isinstance(payload,dict) else None
    text=str((message or {}).get('text') or '') if isinstance(message,dict) else ''
    is_start=text.startswith('/start')
    if is_start:
        await _write_diag('__telegram_last_start_diag__',{'received':True,'secret_ok':secret_ok,'update_id':payload.get('update_id')})
    if not secret_ok:
        raise HTTPException(403,'Invalid Telegram webhook secret')
    try:
        await feed_update(payload)
        if is_start:
            await _write_diag('__telegram_last_start_diag__',{'received':True,'secret_ok':True,'handled':True,'update_id':payload.get('update_id')})
        return {'ok':True}
    except Exception as exc:
        if is_start:
            await _write_diag('__telegram_last_start_diag__',{'received':True,'secret_ok':True,'handled':False,'update_id':payload.get('update_id'),'error_type':type(exc).__name__,'error':str(exc)[:300]})
        raise


@app.post('/api/telegram/webhook')
async def api_telegram_webhook(payload:dict,x_telegram_bot_api_secret_token:str|None=Header(default=None)):
    return await _telegram_webhook_handler(payload,x_telegram_bot_api_secret_token)


@app.post('/telegram/webhook')
async def telegram_webhook(payload:dict,x_telegram_bot_api_secret_token:str|None=Header(default=None)):
    return await _telegram_webhook_handler(payload,x_telegram_bot_api_secret_token)


async def _verify_scanner(authorization: str | None) -> None:
    if bot is None:raise HTTPException(503,'Telegram is not configured in this deployment')
    if not authorization or not authorization.lower().startswith('bearer '):raise HTTPException(401,'Bearer token required')
    await verify_oidc(authorization.split(' ',1)[1])


@app.post('/api/internal/auto-tick')
async def internal_auto_tick(authorization:str|None=Header(default=None)):
    await _verify_scanner(authorization)
    return await adaptive_drive_session_tick()


@app.post('/api/internal/scan')
async def internal_scan(authorization:str|None=Header(default=None)):
    await _verify_scanner(authorization)
    return await scan_tick(bot)


@app.post('/api/internal/vip-scan')
async def internal_vip_scan(authorization:str|None=Header(default=None)):
    await _verify_scanner(authorization)
    return await run_due_vip(bot)


DIST_DIR=Path(__file__).resolve().parents[1]/'miniapp'/'dist'
ASSETS_DIR=DIST_DIR/'assets'


@app.get('/assets/{asset_path:path}',include_in_schema=False)
async def miniapp_asset(asset_path:str):
    if not ASSETS_DIR.exists():raise HTTPException(404,'Mini App assets are not built')
    requested=ASSETS_DIR/Path(asset_path).name
    if requested.is_file():return FileResponse(requested)
    suffix=requested.suffix.lower()
    if suffix not in {'.js','.css'}:raise HTTPException(404,'Asset not found')
    candidates=sorted((p for p in ASSETS_DIR.glob(f'index-*{suffix}') if p.is_file()),key=lambda p:p.stat().st_mtime,reverse=True)
    if not candidates:raise HTTPException(404,'Asset not found')
    return FileResponse(candidates[0],headers={'Cache-Control':'no-store, max-age=0','X-AlphaPulse-Asset-Recovery':'1'})


if DIST_DIR.exists():
    app.mount('/',StaticFiles(directory=str(DIST_DIR),html=True),name='miniapp')
