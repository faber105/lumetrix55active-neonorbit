from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.db_models import Signal,SignalDirection,SignalResult,StrategyPerformance
from backend.services.database import get_db
from backend.services.online_ml import get_model
from backend.services.pocketoption_otc import DISPLAY_TO_ASSET,MarketDataUnavailable,OTC_ASSETS,TF_SECONDS,market_data
from backend.services.signal_engine import signal_engine
from backend.services.strategies import STRATEGY_LABELS

router=APIRouter()
def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
def parse(v):
    d=datetime.fromisoformat(v.replace('Z','+00:00')); return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
def out(s):
    return {'id':s.id,'pair':s.pair,'asset':s.asset,'timeframe':s.timeframe,'strategy':s.strategy,'strategy_label':STRATEGY_LABELS.get(s.strategy,s.strategy),'direction':s.direction.value,'confidence':s.confidence,'model_probability':s.model_probability,'is_vip':s.is_vip,'reason':s.reason,'indicators':{'RSI':s.rsi,'EMA':s.ema_signal,'MACD':s.macd_signal},'analysis_price':s.analysis_price,'entry_price':s.entry_price,'close_price':s.close_price,'entry_time':s.entry_time.isoformat()+'Z','expiry_time':s.expiry_time.isoformat()+'Z','result':s.result.value,'created_at':s.created_at.isoformat()+'Z','closed_at':s.closed_at.isoformat()+'Z' if s.closed_at else None}
async def save_candidate(db,c):
    et=parse(c['entry_time']); xt=parse(c['expiry_time'])
    ex=(await db.execute(select(Signal).where(Signal.asset==c['asset'],Signal.timeframe==c['timeframe'],Signal.strategy==c['strategy'],Signal.entry_time==et))).scalar_one_or_none()
    if ex: return ex,True
    ind=c.get('indicators',{}); s=Signal(pair=c['pair'],asset=c['asset'],timeframe=c['timeframe'],strategy=c['strategy'],direction=SignalDirection(c['direction']),confidence=c['confidence'],model_probability=c.get('model_probability'),is_vip=c['confidence']>=80,rsi=ind.get('rsi'),ema_signal='Bull' if c['direction']=='BUY' else 'Bear',macd_signal='Positive' if c['direction']=='BUY' else 'Negative',trend_strength=ind.get('atr_expansion') or ind.get('ema_gap_atr'),reason=c['reason'],features_json=json.dumps(c['features']),analysis_price=c.get('analysis_price'),entry_time=et,expiry_time=xt,result=SignalResult.PENDING)
    db.add(s); await db.commit(); await db.refresh(s); return s,False
class AnalyzeRequest(BaseModel): pair:str; timeframe:str='5m'; user_id:Optional[int]=None
@router.post('/analyze')
async def analyze(req:AnalyzeRequest,db:AsyncSession=Depends(get_db)):
    asset=DISPLAY_TO_ASSET.get(req.pair.replace(' OTC','').strip())
    if not asset: raise HTTPException(400,'Unsupported OTC pair')
    if req.timeframe not in TF_SECONDS: raise HTTPException(400,'Unsupported timeframe')
    try: c=await signal_engine.evaluate_asset_best(asset,req.timeframe)
    except MarketDataUnavailable as e: raise HTTPException(503,str(e)) from e
    if not c: return {'error':'No confirmed strategy setup right now. Try another pair or timeframe.'}
    s,_=await save_candidate(db,c); data=out(s); data['indicators']=c.get('indicators',{}); return data
class ScanRequest(BaseModel): timeframe:str='1m'; assets:list[str]=Field(default_factory=lambda:list(OTC_ASSETS.keys())); min_confidence:float=72.0
@router.post('/scan-best')
async def scan_best(req:ScanRequest,db:AsyncSession=Depends(get_db)):
    assets=[a for a in req.assets if a in OTC_ASSETS]
    try: c=await signal_engine.scan_best(req.timeframe,assets)
    except MarketDataUnavailable as e: raise HTTPException(503,str(e)) from e
    if not c or c['confidence']<req.min_confidence: return {'status':'NO_SIGNAL','signal':None}
    s,dup=await save_candidate(db,c); return {'status':'SIGNAL','signal':out(s),'duplicate':dup}
@router.post('/reconcile')
async def reconcile(db:AsyncSession=Depends(get_db)):
    pending=(await db.execute(select(Signal).where(Signal.result==SignalResult.PENDING).order_by(Signal.entry_time).limit(100))).scalars().all(); closed=[]; entered=trained=0
    t=now()
    for s in pending:
        try:
            if s.entry_price is None and s.entry_time<=t: s.entry_price=await market_data.latest_price(s.asset); entered+=1
            if s.entry_price is not None and s.expiry_time<=t:
                s.close_price=await market_data.latest_price(s.asset); d=s.close_price-s.entry_price; eps=max(abs(s.entry_price)*1e-10,1e-10)
                if abs(d)<=eps: s.result=SignalResult.DRAW
                elif s.direction==SignalDirection.BUY: s.result=SignalResult.WIN if d>0 else SignalResult.LOSS
                else: s.result=SignalResult.WIN if d<0 else SignalResult.LOSS
                s.closed_at=t; closed.append(out(s))
                if s.result in {SignalResult.WIN,SignalResult.LOSS} and s.trained_at is None:
                    await get_model(s.strategy).learn(json.loads(s.features_json),s.result==SignalResult.WIN); s.trained_at=t; trained+=1
                    perf=(await db.execute(select(StrategyPerformance).where(StrategyPerformance.strategy==s.strategy))).scalar_one_or_none()
                    if perf is None: perf=StrategyPerformance(strategy=s.strategy); db.add(perf)
                    perf.samples+=1; perf.wins+=1 if s.result==SignalResult.WIN else 0; perf.losses+=1 if s.result==SignalResult.LOSS else 0
        except MarketDataUnavailable: continue
    await db.commit(); return {'entered':entered,'closed':len(closed),'trained':trained,'closed_signals':closed}
@router.get('/history')
async def history(limit:int=Query(30,ge=1,le=200),db:AsyncSession=Depends(get_db)):
    rows=(await db.execute(select(Signal).order_by(desc(Signal.created_at)).limit(limit))).scalars().all(); return [out(s) for s in rows]
@router.get('/vip')
async def vip(limit:int=Query(20,ge=1,le=100),db:AsyncSession=Depends(get_db)):
    rows=(await db.execute(select(Signal).where(Signal.is_vip==True).order_by(desc(Signal.created_at)).limit(limit))).scalars().all(); return [out(s) for s in rows]
@router.get('/ml')
async def ml():
    result={}
    for k in STRATEGY_LABELS:
        m=get_model(k); await m.hydrate(); result[k]=m.stats()
    return result
