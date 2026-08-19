"""Read-only Pocket Option OTC market-data adapter.

Pocket Option has no documented public developer candle API. This adapter uses an
optional third-party client with a user supplied Pocket Option web-session SSID and
calls only market-data/history methods. There is deliberately no trade execution.
If the broker session is unavailable, the service fails closed: no random candles.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Dict, List

logger = logging.getLogger('alphapulse.pocketoption')

OTC_ASSETS: Dict[str, str] = {
    'EURUSD_otc': 'EUR/USD OTC',
    'GBPUSD_otc': 'GBP/USD OTC',
    'USDJPY_otc': 'USD/JPY OTC',
    'USDCHF_otc': 'USD/CHF OTC',
    'AUDUSD_otc': 'AUD/USD OTC',
    'USDCAD_otc': 'USD/CAD OTC',
    'NZDUSD_otc': 'NZD/USD OTC',
    'EURGBP_otc': 'EUR/GBP OTC',
    'EURJPY_otc': 'EUR/JPY OTC',
    'GBPJPY_otc': 'GBP/JPY OTC',
}
DISPLAY_TO_ASSET = {v.replace(' OTC',''): k for k, v in OTC_ASSETS.items()}
TF_SECONDS = {'1m': 60, '5m': 300, '15m': 900, '1h': 3600}


class MarketDataUnavailable(RuntimeError):
    pass


class PocketOptionOTCService:
    def __init__(self):
        self.ssid = os.getenv('POCKET_OPTION_SSID', '').strip()
        self.demo = os.getenv('POCKET_OPTION_DEMO', 'true').lower() in {'1','true','yes','on'}
        self._client = None
        self._lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._last_error = None

    @property
    def configured(self):
        return bool(self.ssid)

    async def connect(self):
        if self._client is not None:
            return self._client
        if not self.ssid:
            raise MarketDataUnavailable('POCKET_OPTION_SSID is not configured. OTC scanner is disabled.')
        async with self._lock:
            if self._client is not None:
                return self._client
            try:
                from pocketoptionapi_async.client import AsyncPocketOptionClient
                client = AsyncPocketOptionClient(
                    ssid=self.ssid,
                    is_demo=self.demo,
                    persistent_connection=True,
                    auto_reconnect=True,
                    enable_logging=False,
                )
                ok = await client.connect(persistent=True)
                if not ok:
                    raise RuntimeError('Pocket Option session rejected')
                self._client = client
                self._last_error = None
                logger.info('Pocket Option OTC read-only connection ready')
            except Exception as exc:
                self._client = None
                self._last_error = str(exc)
                raise MarketDataUnavailable(f'Pocket Option connection failed: {exc}') from exc
        return self._client

    async def close(self):
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def health(self):
        return {
            'configured': self.configured,
            'connected': self._client is not None,
            'provider': 'Pocket Option web-session stream (read-only, unofficial client)',
            'last_error': self._last_error,
        }

    async def server_time(self) -> int:
        return int(time.time())

    async def get_candles(self, asset: str, timeframe: str='1m', count: int=240) -> List[dict]:
        if asset not in OTC_ASSETS:
            raise MarketDataUnavailable(f'Unsupported OTC asset: {asset}')
        if timeframe not in TF_SECONDS:
            raise MarketDataUnavailable(f'Unsupported timeframe: {timeframe}')
        client = await self.connect()
        try:
            async with self._request_lock:
                raw = await asyncio.wait_for(
                    client.get_candles(asset=asset, timeframe=TF_SECONDS[timeframe], count=count),
                    timeout=25,
                )
        except Exception as exc:
            self._last_error = str(exc)
            raise MarketDataUnavailable(f'Cannot fetch {asset} {timeframe} candles: {exc}') from exc
        out=[]
        for item in raw or []:
            if hasattr(item, 'model_dump'):
                item=item.model_dump()
            elif not isinstance(item, dict):
                try: item=vars(item)
                except Exception: continue
            try:
                ts=item.get('timestamp') or item.get('time') or item.get('from')
                close=float(item.get('close', item.get('price')))
                op=float(item.get('open', close)); hi=float(item.get('high', close)); lo=float(item.get('low', close))
                out.append({'time': int(float(ts)) if ts is not None else int(time.time()), 'open':op, 'high':max(hi,op,close), 'low':min(lo,op,close), 'close':close})
            except Exception:
                continue
        out.sort(key=lambda c:c['time'])
        if len(out) < min(80, count):
            raise MarketDataUnavailable(f'Broker returned only {len(out)} usable candles for {asset}/{timeframe}')
        return out[-count:]

    async def latest_price(self, asset: str) -> float:
        candles = await self.get_candles(asset, '1m', 100)
        return float(candles[-1]['close'])


market_data = PocketOptionOTCService()
