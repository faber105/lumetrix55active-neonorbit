"""Read-only Pocket Option OTC market-data adapter.

Pocket Option has no documented public developer candle API. This adapter uses an
unofficial read-only WebSocket client and NEVER calls order/trade methods.

Two browser auth formats are supported:
- legacy: 42[\"auth\",{\"session\":\"...\", ...}]
- current web chart: 42[\"auth\",{\"sessionToken\":\"...\", ...}]

For production the captured auth frame can be supplied through POCKET_OPTION_SSID
or a private DB row keyed as ``__runtime_pocket__`` in ``ml_state``. The DB value
has priority so credentials never need to be committed to GitHub.
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
DISPLAY_TO_ASSET = {v.replace(' OTC', ''): k for k, v in OTC_ASSETS.items()}
TF_SECONDS = {'1m': 60, '5m': 300, '15m': 900, '1h': 3600}
PRIVATE_SSID_KEY = '__runtime_pocket__'


class MarketDataUnavailable(RuntimeError):
    pass


def _parse_wire_auth(value: str) -> dict | None:
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
        self.ssid = ''
        self.demo = os.getenv('POCKET_OPTION_DEMO', 'true').lower() in {'1', 'true', 'yes', 'on'}
        self._client = None
        self._lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._last_error = None
        self._auth_kind = 'none'
        self._private_secret_loaded = False
        self._apply_ssid(os.getenv('POCKET_OPTION_SSID', '').strip())

    def _apply_ssid(self, value: str) -> None:
        self.ssid = (value or '').strip()
        self._auth_kind = 'none'
        payload = _parse_wire_auth(self.ssid)
        if payload:
            if payload.get('session'):
                self._auth_kind = 'legacy-session'
                if 'isDemo' in payload:
                    try:
                        self.demo = bool(int(payload.get('isDemo') or 0))
                    except Exception:
                        pass
            elif payload.get('sessionToken'):
                self._auth_kind = 'sessionToken-wire'
                current_url = str(payload.get('currentUrl') or '').lower()
                if 'demo' in current_url:
                    self.demo = True
        elif self.ssid:
            self._auth_kind = 'raw-session'

    async def _refresh_private_ssid(self) -> None:
        """Load the broker auth frame from private Postgres storage once per cold start."""
        if self._private_secret_loaded:
            return
        try:
            from backend.models.db_models import AsyncSessionLocal, MLState
            async with AsyncSessionLocal() as db:
                state = await db.get(MLState, PRIVATE_SSID_KEY)
                if state and state.payload and state.payload.strip():
                    private_ssid = state.payload.strip()
                    if private_ssid != self.ssid:
                        old, self._client = self._client, None
                        if old is not None:
                            try:
                                await old.disconnect()
                            except Exception:
                                pass
                        self._apply_ssid(private_ssid)
                    logger.info('Pocket Option credential loaded from private DB storage (%s)', self._auth_kind)
            self._private_secret_loaded = True
        except Exception as exc:
            logger.warning('Cannot load private Pocket Option credential: %s', type(exc).__name__)

    @property
    def configured(self):
        return bool(self.ssid)

    def _make_client(self):
        from pocketoptionapi_async.client import AsyncPocketOptionClient

        payload = _parse_wire_auth(self.ssid)
        if payload and payload.get('sessionToken') and not payload.get('session'):
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

        # Legacy full auth frames are parsed natively, but the library rebuilds
        # the outgoing message and drops browser flags it does not know about
        # (for example isOptimized). Preserve the captured frame byte-for-byte.
        client = AsyncPocketOptionClient(
            ssid=self.ssid,
            is_demo=self.demo,
            persistent_connection=False,
            auto_reconnect=True,
            enable_logging=False,
        )
        if payload and payload.get('session'):
            exact_wire_frame = self.ssid
            client._format_session_message = lambda: exact_wire_frame
        return client

    async def connect(self):
        await self._refresh_private_ssid()
        if self._client is not None and getattr(self._client, 'is_connected', True):
            return self._client
        async with self._lock:
            await self._refresh_private_ssid()
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
        await self._refresh_private_ssid()
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

    async def get_candles(self, asset: str, timeframe: str = '1m', count: int = 240) -> List[dict]:
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
            old, self._client = self._client, None
            if old is not None:
                try:
                    await old.disconnect()
                except Exception:
                    pass
            raise MarketDataUnavailable(f'Cannot fetch {asset} {timeframe} candles: {exc}') from exc

        out = []
        for item in raw or []:
            if hasattr(item, 'model_dump'):
                item = item.model_dump()
            elif not isinstance(item, dict):
                try:
                    item = vars(item)
                except Exception:
                    continue
            try:
                ts = item.get('timestamp') or item.get('time') or item.get('from')
                close = float(item.get('close', item.get('price')))
                op = float(item.get('open', close))
                hi = float(item.get('high', close))
                lo = float(item.get('low', close))
                out.append({
                    'time': int(float(ts)) if ts is not None else int(time.time()),
                    'open': op,
                    'high': max(hi, op, close),
                    'low': min(lo, op, close),
                    'close': close,
                })
            except Exception:
                continue
        out.sort(key=lambda candle: candle['time'])
        if len(out) < min(80, count):
            raise MarketDataUnavailable(f'Broker returned only {len(out)} usable candles for {asset}/{timeframe}')
        return out[-count:]

    async def latest_price(self, asset: str) -> float:
        candles = await self.get_candles(asset, '1m', 100)
        return float(candles[-1]['close'])


market_data = PocketOptionOTCService()
