from fastapi import APIRouter, HTTPException, Query
from backend.services.pocketoption_otc import DISPLAY_TO_ASSET, MarketDataUnavailable, OTC_ASSETS, TF_SECONDS, market_data
from backend.services.strategies import indicator_snapshot
router=APIRouter()
@router.get('/assets')
async def assets(): return [{'symbol':k,'name':v} for k,v in OTC_ASSETS.items()]
@router.get('/health')
async def health(): return await market_data.health()
@router.get('/analysis')
async def analysis(pair:str=Query(...)):
    asset=DISPLAY_TO_ASSET.get(pair.replace(' OTC','').strip())
    if not asset: raise HTTPException(400,'Unsupported OTC pair')
    timeframes={}; primary=None
    try:
        for tf in ['1m','5m','15m','1h']:
            snap=indicator_snapshot(await market_data.get_candles(asset,tf,240)); timeframes[tf]={'direction':snap['direction'],'confidence':snap['confidence']}
            if tf=='5m': primary=snap
    except MarketDataUnavailable as e: raise HTTPException(503,str(e)) from e
    return {'pair':pair,'asset':asset,'timeframes':timeframes,'indicators':(primary or {}).get('indicators',{})}
@router.get('/price/{asset}')
async def price(asset:str):
    if asset not in OTC_ASSETS: raise HTTPException(404,'Unknown OTC asset')
    try: p=await market_data.latest_price(asset)
    except MarketDataUnavailable as e: raise HTTPException(503,str(e)) from e
    return {'asset':asset,'pair':OTC_ASSETS[asset],'price':p}
