from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.models.db_models import utcnow
from backend.services.pocketoption_otc import DISPLAY_TO_ASSET, OTC_ASSETS, TF_SECONDS
from backend.services.worker_protocol import (
    await_command,
    enqueue_command,
    ensure_demo_account,
    owns_lease,
    realtime_snapshot,
)
from backend.telegram_auth import TelegramMiniAppUser, telegram_user

router = APIRouter()


def _is_local_worker(account_id: int) -> bool:
    if str(os.getenv('APP_RUNTIME_ROLE') or '').strip().lower() != 'worker':
        return False
    try:
        return int(os.getenv('WORKER_ACCOUNT_ID') or 0) == int(account_id)
    except (TypeError, ValueError):
        return False


async def _run_local_worker_command(account_id: int, command_type: str, payload: dict) -> dict | None:
    """Execute read/analysis work in-process when API and worker are the same Windows runtime.

    Neon remains the fallback command bus for future remote/multi-worker deployments, but
    the local Mini App must not bounce Windows -> Neon -> Windows for every candle request.
    """
    if not _is_local_worker(account_id) or not await owns_lease(account_id):
        return None
    if command_type == 'MARKET_CANDLES':
        from backend.services.manual_worker_tasks import candles

        return await candles(dict(payload))
    if command_type == 'ANALYZE_SIGNAL':
        from backend.services.manual_worker_tasks import analyze_market

        return await analyze_market(dict(payload))
    return None


async def _worker_command(
    user: TelegramMiniAppUser,
    command_type: str,
    payload: dict,
    key: str,
    *,
    timeout: float = 25.0,
) -> dict:
    account_id = await ensure_demo_account(int(user.id))
    try:
        local = await _run_local_worker_command(account_id, command_type, payload)
        if local is not None:
            return local
    except Exception as exc:
        raise HTTPException(503, f'Windows worker could not process local market data: {type(exc).__name__}') from exc

    command = await enqueue_command(
        account_id=account_id,
        command_type=command_type,
        payload=payload,
        idempotency_key=key[:128],
    )
    try:
        return await await_command(int(command['id']), account_id, timeout_seconds=timeout)
    except TimeoutError as exc:
        raise HTTPException(503, 'Windows worker is not responding') from exc
    except RuntimeError as exc:
        raise HTTPException(503, 'Windows worker could not load Pocket market data') from exc


@router.get('/assets')
async def assets():
    return [{'symbol': key, 'name': value} for key, value in OTC_ASSETS.items()]


@router.get('/health')
async def health(user: TelegramMiniAppUser = Depends(telegram_user)):
    account_id = await ensure_demo_account(int(user.id))
    snapshot = await realtime_snapshot(account_id)
    worker = snapshot.get('worker') or {}
    return {
        'configured': worker.get('status') in {'ONLINE', 'DEGRADED'},
        'connected': worker.get('status') == 'ONLINE',
        'demo': True,
        'provider': 'Windows worker / Pocket Option DEMO',
        'worker_status': worker.get('status', 'OFFLINE'),
        'heartbeat_age_seconds': worker.get('heartbeat_age_seconds'),
    }


@router.get('/diagnostics')
async def diagnostics(user: TelegramMiniAppUser = Depends(telegram_user)):
    account_id = await ensure_demo_account(int(user.id))
    snapshot = await realtime_snapshot(account_id)
    worker = snapshot.get('worker') or {}
    return {
        'configured': worker.get('status') in {'ONLINE', 'DEGRADED'},
        'connected': worker.get('status') == 'ONLINE',
        'demo': True,
        'provider': 'Windows worker / Pocket Option DEMO',
        'worker_status': worker.get('status', 'OFFLINE'),
        'heartbeat_age_seconds': worker.get('heartbeat_age_seconds'),
        'active_session': bool(snapshot.get('active')),
        'sequence': snapshot.get('sequence', 0),
    }


@router.get('/candles')
async def candles(
    pair: str = Query(...),
    timeframe: str = Query('1m'),
    count: int = Query(60, ge=20, le=120),
    user: TelegramMiniAppUser = Depends(telegram_user),
):
    asset = DISPLAY_TO_ASSET.get(pair.replace(' OTC', '').strip())
    if not asset:
        raise HTTPException(400, 'Unsupported OTC pair')
    if timeframe not in TF_SECONDS:
        raise HTTPException(400, 'Unsupported timeframe')
    payload = {'pair': pair, 'timeframe': timeframe, 'count': count}
    bucket = int(utcnow().timestamp() // 2)
    return await _worker_command(
        user,
        'MARKET_CANDLES',
        payload,
        f'candles:{int(user.id)}:{asset}:{timeframe}:{count}:{bucket}',
    )


@router.get('/analysis')
async def analysis(
    pair: str = Query(...),
    timeframe: str = Query('1m'),
    user: TelegramMiniAppUser = Depends(telegram_user),
):
    asset = DISPLAY_TO_ASSET.get(pair.replace(' OTC', '').strip())
    if not asset:
        raise HTTPException(400, 'Unsupported OTC pair')
    if timeframe not in {'15s', '1m', '3m', '5m', '15m'}:
        raise HTTPException(400, 'Unsupported timeframe')
    bucket = int(utcnow().timestamp() // 2)
    return await _worker_command(
        user,
        'ANALYZE_SIGNAL',
        {'pair': pair, 'timeframe': timeframe},
        f'analysis:{int(user.id)}:{asset}:{timeframe}:{bucket}',
    )


@router.get('/price/{asset}')
async def price(asset: str, user: TelegramMiniAppUser = Depends(telegram_user)):
    if asset not in OTC_ASSETS:
        raise HTTPException(404, 'Unknown OTC asset')
    pair = OTC_ASSETS[asset]
    bucket = int(utcnow().timestamp())
    payload = await _worker_command(
        user,
        'MARKET_CANDLES',
        {'pair': pair, 'timeframe': '15s', 'count': 20},
        f'price:{int(user.id)}:{asset}:{bucket}',
        timeout=10.0,
    )
    return {'asset': asset, 'pair': pair, 'price': payload.get('current_price')}
