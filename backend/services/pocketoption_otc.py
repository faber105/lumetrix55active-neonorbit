"""Read-only Pocket Option OTC market-data adapter.

Pocket Option has no documented public developer candle API. This adapter uses an
unofficial read-only WebSocket client and NEVER calls order/trade methods.

Two browser auth formats are supported:
- legacy: 42["auth",{"session":"...", ...}]
- current web chart: 42["auth",{"sessionToken":"...", ...}]

For the current format we preserve and send the captured auth frame verbatim. The
third-party library is used only as WebSocket/market-data transport. If auth or
market data fails, the service fails closed: no synthetic/random candles.
"""
from __future__ import annotations

import asyncio
import json
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


def _parse_wire_auth(value: str) -> dict | None:
    """Parse a Socket.IO `42["auth", {...}]` frame without exposing secrets."""
    value = (value or '').strip()
    if not value.startswith('42'):
        return None
    try:
        event = json.loads(value[2:])
        if isinstance(event, list) and len(event) >= 2 and event[0] == 'auth' and isinstance(event[1], dict):
            return event[1]
    except Exception:
        return None
    return None


class PocketOptionOTCService:
    def __init__(self):
        self.ssid = os.getenv('POCKET_OPTION_SSID', '').strip()
        self.demo = os.getenv('POCKET_OPTION_DEMO', 'true').lower() in {'1','true','yes','on'}
        self._client = None
        self._lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._last_error = None
        self._auth_kind = 'none'
        payload = _parse_wire_auth(self.ssid)
        if payload:
            if payload.get('session'):
                self._auth_kind = 'legacy-session'
                if 'isDemo' in payload:
                    self.demo = bool(int(payload.get('isDemo') or 0))
            elif payload.get('sessionToken'):
                self._auth_kind = 'sessionToken-wire'
                # Current browser packet does not include isDemo. The captured
                # currentUrl is a reliable local hint only; the server still
                # decides whether authentication succeeds.
                current_url = str(payload.get('currentUrl') or '').lower()
                if 'demo' in current_url:
                    self.demo = True
        elif self.ssid:
            self._auth_kind = 'raw-session'

    @property
    def configured(self):
        return bool(self.ssid)

    def _make_client(self):
        from pocketoptionapi_async.client import AsyncPocketOptionClient

        payload = _parse_wire_auth(self.ssid)
        if payload and payload.get('sessionToken') and not payload.get('session'):
            # pocketoptionapi-async 2.0.1 only parses the legacy `session`
            # property. Initialize it with the token/uid so its internals are
            # valid, then override only the outgoing auth frame with the exact
            # browser-captured packet. No trade methods are used.
            token = str(payload.get('sessionToken') or '')
            uid_raw = payload.get('uid') or 0
            try:
                uid = int(uid_raw)
            except Exception:
                uid = 0
            if len(token) < 10 or uid <= 0:
                raise MarketDataUnavailable('Pocket Option sessionToken auth packet is incomplete')
            client = AsyncPocketOptionClient(
                ssid=token,
                is_demo=self.demo,
                uid=uid,
                platform=1,
                persistent_connection=False,
                auto_reconnect=True,
                enable_logging=False,
            )
            exact_wire_frame = self.ssid
            client._format_session_message = lambda: exact_wire_frame
            return client

        return AsyncPocketOptionClient(
            ssid=self.ssid,
            is_demo=self.demo,
            persistent_connection=False,
            auto_reconnect=True,
            enable_logging=False,
        )

    async def connect(self):
        if self._client is not None and getattr(self._client, 'is_connected', True):
            return self._client
        async with self._lock:
            if self._client is not None and getattr(self._client, 'is_connected', True):
                return self._client
            if not self.ssid:
                raise MarketDataUnavailable('POCKET_OPTION_SSID is not configured. OTC scanner is disabled.')
            try:
                client = self._make_client()
                ok = await asyncio.wait_for(client.connect(persistent=False), timeout=35)
                if not ok:
                    raise RuntimeError('Pocket Option session rejected or authentication timed out')
                self._client = client
                self._last_error = None
                logger.info('Pocket Option OTC read-only connection ready (%s)', self._auth_kind)
            except Exception as exc:
                self._client = None
                self._last_error = f'{type(exc).__name__}: {exc}'
                logger.warning('Pocket Option connection failed (%s): %s', self._auth_kind, exc)
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
            'connected': self._client is not None and bool(getattr(self._client, 'is_connected', True)),
            'auth_format': self._auth_kind,
            'demo': self.demo,
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
                    timeout=30,
                )
        except Exception as exc:
            self._last_error = f'{type(exc).__name__}: {exc}'
            # Force the next request through a fresh authentication attempt.
            old, self._client = self._client, None
            if old is not None:
                try:
                    await old.disconnect()
                except Exception:
                    pass
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
