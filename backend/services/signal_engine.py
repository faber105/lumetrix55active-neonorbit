from __future__ import annotations

import logging, os
from datetime import datetime, timezone
from typing import Optional

from backend.services.online_ml import get_model
from backend.services.pocketoption_otc import MarketDataUnavailable, OTC_ASSETS, TF_SECONDS, market_data
from backend.services.strategies import STRATEGY_LABELS, STRATEGIES, evaluate

logger=logging.getLogger('alphapulse.engine')
ENTRY_LEAD_SECONDS=int(os.getenv('ENTRY_LEAD_SECONDS','6'))


class SignalEngine:
    async def _candidate_dict(self, asset, timeframe, candidate, candles):
        model=get_model(candidate.strategy); await model.hydrate()
        ml_p=model.probability_setup_wins(candidate.features)
        confidence=candidate.confidence
        if model.influence_ready(): confidence=0.78*confidence+0.22*(ml_p*100.0)
        server_ts=await market_data.server_time(); seconds=TF_SECONDS[timeframe]
        target=server_ts+ENTRY_LEAD_SECONDS; entry_ts=((target//seconds)+1)*seconds; expiry_ts=entry_ts+seconds
        return {
            'pair':OTC_ASSETS[asset], 'asset':asset, 'timeframe':timeframe,
            'strategy':candidate.strategy, 'strategy_label':STRATEGY_LABELS[candidate.strategy],
            'direction':candidate.direction, 'confidence':round(max(0.0,min(99.0,confidence)),1),
            'model_probability':round(ml_p*100.0,1),'ml_samples':model.samples,'ml_influence':model.influence_ready(),
            'reason':candidate.reason,'features':candidate.features,'indicators':candidate.indicators,
            'analysis_price':float(candles[-1]['close']),
            'entry_time':datetime.fromtimestamp(entry_ts,tz=timezone.utc).isoformat(),
            'expiry_time':datetime.fromtimestamp(expiry_ts,tz=timezone.utc).isoformat(),
        }

    async def evaluate_asset(self, asset: str, timeframe: str, strategy: str) -> Optional[dict]:
        candles=await market_data.get_candles(asset,timeframe,240); candidate=evaluate(strategy,candles)
        return None if candidate is None else await self._candidate_dict(asset,timeframe,candidate,candles)

    async def evaluate_asset_best(self, asset: str, timeframe: str) -> Optional[dict]:
        candles=await market_data.get_candles(asset,timeframe,240)
        confirmed=[]
        for strategy in STRATEGIES:
            try:
                c=evaluate(strategy,candles)
                if c is not None: confirmed.append(await self._candidate_dict(asset,timeframe,c,candles))
            except Exception as exc:
                logger.warning('Strategy %s failed for %s/%s: %s',strategy,asset,timeframe,exc)
        if not confirmed: return None
        confirmed.sort(key=lambda x:x['confidence'],reverse=True); return confirmed[0]

    async def scan_strategy(self, timeframe: str, assets: list[str], strategy: str) -> Optional[dict]:
        if timeframe not in TF_SECONDS: raise ValueError(f'Unsupported timeframe: {timeframe}')
        if strategy not in STRATEGIES: raise ValueError(f'Unknown strategy: {strategy}')
        results=[]; unavailable=None
        for asset in assets:
            if asset not in OTC_ASSETS: continue
            try:
                candidate=await self.evaluate_asset(asset,timeframe,strategy)
                if candidate: results.append(candidate)
            except MarketDataUnavailable as exc:
                unavailable=exc
                if 'SSID' in str(exc) or 'connection failed' in str(exc).lower(): raise
            except Exception as exc:
                logger.warning('Strategy scan failed %s/%s/%s: %s',strategy,asset,timeframe,exc)
        if not results:
            if unavailable and not results:
                logger.debug('No strategy setup; last market error: %s', unavailable)
            return None
        results.sort(key=lambda x:x['confidence'],reverse=True)
        return results[0]

    async def scan_best(self, timeframe: str, assets: list[str]) -> Optional[dict]:
        if timeframe not in TF_SECONDS: raise ValueError(f'Unsupported timeframe: {timeframe}')
        results=[]; unavailable=None
        for asset in assets:
            if asset not in OTC_ASSETS: continue
            try:
                c=await self.evaluate_asset_best(asset,timeframe)
                if c: results.append(c)
            except MarketDataUnavailable as exc:
                unavailable=exc
                if 'SSID' in str(exc) or 'connection failed' in str(exc).lower(): raise
            except Exception as exc: logger.warning('Scan failed %s/%s: %s',asset,timeframe,exc)
        if not results: return None
        results.sort(key=lambda x:x['confidence'],reverse=True); return results[0]

signal_engine=SignalEngine()
