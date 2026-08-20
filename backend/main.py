from __future__ import annotations
import logging, os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from backend.github_oidc import verify as verify_oidc
from backend.routers import admin, auth, auto, home, live, market, preload, settings, signals, stats, websocket
from backend.services.database import init_db
from backend.services.pocketoption_otc import market_data
from backend.services.scanner import scan_tick
from backend.services.session_engine import ensure_schema
from backend.services.preload_next import ensure_preload_schema
# Force the bot to call the same live production backend instead of any stale env URL.
os.environ['BACKEND_URL']='https://alphapulse-runtime-staging.vercel.app'
logging.basicConfig(level=logging.INFO);logger=logging.getLogger('alphapulse');TELEGRAM_ENABLED=bool(os.getenv('TELEGRAM_BOT_TOKEN','').strip())
if TELEGRAM_ENABLED:
    from bot.main import bot, configure_webhook, feed_update, valid_secret
else:
    bot=None
    async def configure_webhook():return None
    async def feed_update(payload):del payload;raise RuntimeError('Telegram is not configured in this deployment')
    def valid_secret(value):del value;return False
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
    try:await configure_webhook()
    except Exception:logger.exception('Webhook setup failed; API remains available')
    yield
    try:await market_data.close()
    except Exception:pass
app=FastAPI(title='AlphaPulse API',version='3.0',lifespan=lifespan);app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['*'],allow_headers=['*'])
@app.middleware('http')
async def miniapp_cache_headers(request,call_next):
    response=await call_next(request)
    if request.method=='GET' and request.url.path in {'/','/index.html'}:response.headers['Cache-Control']='no-store, max-age=0';response.headers['Pragma']='no-cache';response.headers['Expires']='0'
    return response
app.include_router(auth.router,prefix='/api/auth',tags=['auth']);app.include_router(home.router,prefix='/api/home',tags=['home']);app.include_router(signals.router,prefix='/api/signals',tags=['signals']);app.include_router(market.router,prefix='/api/market',tags=['market']);app.include_router(stats.router,prefix='/api/stats',tags=['stats']);app.include_router(settings.router,prefix='/api/settings',tags=['settings']);app.include_router(live.router,prefix='/api/live',tags=['live']);app.include_router(auto.router,prefix='/api/auto',tags=['auto']);app.include_router(preload.router,prefix='/api/auto-preload',tags=['auto-preload']);app.include_router(admin.router,prefix='/api/admin',tags=['admin']);app.include_router(websocket.router,prefix='/ws',tags=['websocket'])
@app.get('/health')
async def health():
    return {'status':'ok','service':'alphapulsesbot','version':'3.0','scanner':'github-actions-5s-session-window','telegram_configured':TELEGRAM_ENABLED,'database_configured':bool(os.getenv('DATABASE_URL','').strip()),'source':{'provider':os.getenv('VERCEL_GIT_PROVIDER','manual'),'repository':'/'.join(p for p in (os.getenv('VERCEL_GIT_REPO_OWNER',''),os.getenv('VERCEL_GIT_REPO_SLUG','')) if p) or 'unknown','ref':os.getenv('VERCEL_GIT_COMMIT_REF','unknown'),'sha':os.getenv('VERCEL_GIT_COMMIT_SHA','unknown')},'market':await market_data.health()}
@app.post('/telegram/webhook')
async def telegram_webhook(payload:dict,x_telegram_bot_api_secret_token:str|None=Header(default=None)):
    if not TELEGRAM_ENABLED:raise HTTPException(503,'Telegram is not configured in this deployment')
    if not valid_secret(x_telegram_bot_api_secret_token):raise HTTPException(403,'Invalid Telegram webhook secret')
    await feed_update(payload);return {'ok':True}
@app.post('/api/internal/scan')
async def internal_scan(authorization:str|None=Header(default=None)):
    if bot is None:raise HTTPException(503,'Telegram is not configured in this deployment')
    if not authorization or not authorization.lower().startswith('bearer '):raise HTTPException(401,'Bearer token required')
    await verify_oidc(authorization.split(' ',1)[1]);return await scan_tick(bot)
DIST_DIR=Path(__file__).resolve().parents[1]/'miniapp'/'dist';ASSETS_DIR=DIST_DIR/'assets'
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
if DIST_DIR.exists():app.mount('/',StaticFiles(directory=str(DIST_DIR),html=True),name='miniapp')
