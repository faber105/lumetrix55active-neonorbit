from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from backend.services.pocketoption_otc import DISPLAY_TO_ASSET, MarketDataUnavailable, OTC_ASSETS, TF_SECONDS
from backend.services.signal_engine import signal_engine
from backend.services.signal_store import save_signal
from backend.telegram_auth import TelegramMiniAppUser, telegram_user
router=APIRouter(); MANUAL_TIMEFRAMES={'15s','1m','3m','5m','15m'}; MIN_MANUAL_CONFIDENCE=70.0
class AnalyzeRequest(BaseModel):pair:str; timeframe:str='1m'
@router.post('/analyze')
async def analyze(req:AnalyzeRequest,_:TelegramMiniAppUser=Depends(telegram_user)):
    asset=DISPLAY_TO_ASSET.get(req.pair.replace(' OTC','').strip())
    if not asset:raise HTTPException(400,'Unsupported OTC pair')
    if req.timeframe not in MANUAL_TIMEFRAMES or req.timeframe not in TF_SECONDS:raise HTTPException(400,'Unsupported timeframe')
    try:candidate=await signal_engine.evaluate_asset_composite(asset,req.timeframe)
    except MarketDataUnavailable as exc:raise HTTPException(503,str(exc)) from exc
    if not candidate or float(candidate.get('confidence') or 0)<MIN_MANUAL_CONFIDENCE:return {'status':'NO_SIGNAL','pair':OTC_ASSETS[asset],'timeframe':req.timeframe,'signal':None,'reason':'Сейчас нет подтверждённой точки входа. Trend, momentum и volatility-фильтры не дали достаточного совпадения.'}
    signal,duplicate=await save_signal(candidate,is_vip=False); return {'status':'SIGNAL','signal':signal,'duplicate':duplicate,'analysis':{'engine':'Composite Analysis','strategy':candidate.get('strategy_label'),'confirmations':candidate.get('confirmations',[]),'indicators':candidate.get('indicators',{})}}
@router.get('/vip-status')
async def vip_status(_:TelegramMiniAppUser=Depends(telegram_user)):
    from backend.services.control import get_control
    from backend.models.db_models import utcnow
    control=await get_control()
    if control is None:return {'enabled':False,'interval_seconds':300,'next_vip_at':None,'seconds_remaining':None,'last_status':None}
    remaining=max(0,int((control.next_vip_at-utcnow()).total_seconds())) if control.next_vip_at else 0
    return {'enabled':bool(control.vip_enabled),'interval_seconds':int(control.vip_interval_seconds or 300),'next_vip_at':control.next_vip_at.isoformat()+'Z' if control.next_vip_at else None,'seconds_remaining':remaining,'last_status':control.last_vip_status,'timeframe':'5m','strategy':'VIP 5M Confluence'}
