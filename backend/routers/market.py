from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.services.pocketoption_otc import (
    DISPLAY_TO_ASSET,
    MarketDataUnavailable,
    OTC_ASSETS,
    TF_SECONDS,
    market_data,
)
from backend.services.strategies import indicator_snapshot
from backend.telegram_auth import TelegramMiniAppUser, telegram_user

router = APIRouter()


@router.get('/assets')
async def assets():
    return [{'symbol': key, 'name': value} for key, value in OTC_ASSETS.items()]


@router.get('/health')
async def health():
    return await market_data.health()


@router.get('/diagnostics')
async def diagnostics():
    state = await market_data.health()
    result = {
        'configured': state.get('configured', False),
        'connected': state.get('connected', False),
        'auth_format': state.get('auth_format'),
        'demo': state.get('demo'),
        'provider': state.get('provider'),
        'candle_test': False,
        'asset': 'EURUSD_otc',
        'timeframe': '1m',
        'candles': 0,
        'latest_candle_time': None,
    }
    if not result['configured']:
        return result
    try:
        candles = await market_data.get_candles('EURUSD_otc', '1m', 100)
        result['connected'] = True
        result['candle_test'] = len(candles) >= 80
        result['candles'] = len(candles)
        result['latest_candle_time'] = candles[-1]['time'] if candles else None
        return result
    except MarketDataUnavailable as exc:
        result['error'] = str(exc)
        return result


@router.get('/candles')
async def candles(
    pair: str = Query(...),
    timeframe: str = Query('1m'),
    count: int = Query(60, ge=20, le=120),
    _: TelegramMiniAppUser = Depends(telegram_user),
):
    asset = DISPLAY_TO_ASSET.get(pair.replace(' OTC', '').strip())
    if not asset:
        raise HTTPException(400, 'Unsupported OTC pair')
    if timeframe not in TF_SECONDS:
        raise HTTPException(400, 'Unsupported timeframe')
    try:
        rows = await market_data.get_candles(asset, timeframe, count)
        current_price = await market_data.latest_price(asset)
    except MarketDataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        'pair': OTC_ASSETS[asset],
        'asset': asset,
        'timeframe': timeframe,
        'current_price': float(current_price),
        'server_time': datetime.now(timezone.utc).isoformat(),
        'candles': rows,
    }


@router.get('/analysis')
async def analysis(pair: str = Query(...), _: TelegramMiniAppUser = Depends(telegram_user)):
    asset = DISPLAY_TO_ASSET.get(pair.replace(' OTC', '').strip())
    if not asset:
        raise HTTPException(400, 'Unsupported OTC pair')
    timeframes = {}
    primary = None
    try:
        for timeframe in ['1m', '5m', '15m', '1h']:
            snapshot = indicator_snapshot(await market_data.get_candles(asset, timeframe, 240))
            timeframes[timeframe] = {
                'direction': snapshot['direction'],
                'confidence': snapshot['confidence'],
            }
            if timeframe == '5m':
                primary = snapshot
    except MarketDataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        'pair': pair,
        'asset': asset,
        'timeframes': timeframes,
        'indicators': (primary or {}).get('indicators', {}),
    }


@router.get('/price/{asset}')
async def price(asset: str, _: TelegramMiniAppUser = Depends(telegram_user)):
    if asset not in OTC_ASSETS:
        raise HTTPException(404, 'Unknown OTC asset')
    try:
        value = await market_data.latest_price(asset)
    except MarketDataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {'asset': asset, 'pair': OTC_ASSETS[asset], 'price': value}
