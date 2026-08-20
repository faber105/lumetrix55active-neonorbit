from __future__ import annotations
import logging, os
from datetime import datetime, timezone
from typing import Iterable, Optional
from backend.services.online_ml import get_model
from backend.services.pocketoption_otc import MarketDataUnavailable, OTC_ASSETS, TF_SECONDS, market_data
from backend.services.strategies import AUTO_STRATEGIES, STRATEGY_LABELS, evaluate, evaluate_best
logger=logging.getLogger('alphapulse.engine'); ENTRY_LEAD_SECONDS=int(os.getenv('ENTRY_LEAD_SECONDS','6')); TF_SECONDS.setdefault('15s',15); TF_SECONDS.setdefault('3m',180)
class SignalEngine:
    async def _candidate_dict(self,asset,timeframe,candidate,candles,*,is_vip=False):
        model=get_model(candidate.strategy); await model.hydrate(); ml_p=model.probability_setup_wins(candidate.features); confidence=float(candidate.confidence)
        if model.influence_ready():confidence=.82*confidence+.18*(ml_p*100)
        server_ts=await market_data.server_time(); seconds=int(TF_SECONDS[timeframe]); target=server_ts+ENTRY_LEAD_SECONDS; entry_ts=((target//seconds)+1)*seconds; expiry_ts=entry_ts+seconds
        return {'pair':OTC_ASSETS[asset],'asset':asset,'timeframe':timeframe,'strategy':candidate.strategy,'strategy_label':STRATEGY_LABELS[candidate.strategy],'direction':candidate.direction,'confidence':round(max(0,min(99,confidence)),1),'model_probability':round(ml_p*100,1),'ml_samples':model.samples,'ml_influence':model.influence_ready(),'reason':candidate.reason,'confirmations':list(candidate.confirmations),'features':candidate.features,'indicators':candidate.indicators,'analysis_price':float(candles[-1]['close']),'entry_time':datetime.fromtimestamp(entry_ts,tz=timezone.utc).isoformat(),'expiry_time':datetime.fromtimestamp(expiry_ts,tz=timezone.utc).isoformat(),'is_vip':bool(is_vip)}
    async def evaluate_asset(self,asset,timeframe,strategy):
        if timeframe not in TF_SECONDS:raise ValueError(f'Unsupported timeframe: {timeframe}')
        candles=await market_data.get_candles(asset,timeframe,240); c=evaluate(strategy,candles); return None if c is None else await self._candidate_dict(asset,timeframe,c,candles)
    async def evaluate_asset_composite(self,asset,timeframe):
        if timeframe not in TF_SECONDS:raise ValueError(f'Unsupported timeframe: {timeframe}')
        candles=await market_data.get_candles(asset,timeframe,240); c=evaluate_best(candles,AUTO_STRATEGIES); return None if c is None else await self._candidate_dict(asset,timeframe,c,candles)
    async def evaluate_vip_asset(self,asset):
        candles=await market_data.get_candles(asset,'5m',240); c=evaluate('vip_confluence',candles); return None if c is None else await self._candidate_dict(asset,'5m',c,candles,is_vip=True)
    async def scan_strategy(self,timeframe,assets:Iterable[str],strategy):
        results=[]; unavailable=None
        for asset in assets:
            if asset not in OTC_ASSETS:continue
            try:
                c=await self.evaluate_asset(asset,timeframe,strategy)
                if c:results.append(c)
            except MarketDataUnavailable as exc:
                unavailable=exc
                if 'SSID' in str(exc) or 'connection failed' in str(exc).lower():raise
            except Exception as exc:logger.warning('Strategy scan failed %s/%s/%s: %s',strategy,asset,timeframe,type(exc).__name__)
        if not results:return None
        return max(results,key=lambda x:float(x.get('confidence') or 0))
    async def scan_best(self,timeframe,assets:Iterable[str]):
        results=[]
        for asset in assets:
            if asset not in OTC_ASSETS:continue
            try:
                c=await self.evaluate_asset_composite(asset,timeframe)
                if c:results.append(c)
            except MarketDataUnavailable:raise
            except Exception as exc:logger.warning('Composite scan failed %s/%s: %s',asset,timeframe,type(exc).__name__)
        return max(results,key=lambda x:float(x.get('confidence') or 0)) if results else None
    async def scan_vip(self,assets:Iterable[str]):
        results=[]
        for asset in assets:
            if asset not in OTC_ASSETS:continue
            try:
                c=await self.evaluate_vip_asset(asset)
                if c:results.append(c)
            except MarketDataUnavailable:raise
            except Exception as exc:logger.warning('VIP scan failed %s: %s',asset,type(exc).__name__)
        return max(results,key=lambda x:float(x.get('confidence') or 0)) if results else None
signal_engine=SignalEngine()
