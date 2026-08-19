"""Read-only Pocket Option OTC market data adapter.

Pocket Option does not document a public candle API. This adapter intentionally calls
only candle/history methods of an optional third-party client that connects to the
broker web platform's own WebSocket stream using a user-supplied SSID. It never
imports or calls order placement methods from the application code.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from config import get_settings

logger = logging.getLogger(__name__)

TIMEFRAME_SECONDS = {'1m': 60, '3m': 180, '5m': 300}

DISPLAY_TO_PO = {
    'EUR/USD OTC': 'EURUSD_otc','GBP/USD OTC': 'GBPUSD_otc','USD/JPY OTC': 'USDJPY_otc','AUD/USD OTC': 'AUDUSD_otc','USD/CAD OTC': 'USDCAD_otc','NZD/USD OTC': 'NZDUSD_otc','EUR/GBP OTC': 'EURGBP_otc','EUR/JPY OTC': 'EURJPY_otc','AUD/CAD OTC': 'AUDCAD_otc','AUD/CHF OTC': 'AUDCHF_otc',
}


def display_asset(symbol: str) -> str:
    if symbol.endswith('_otc') and len(symbol) >= 9:
        raw = symbol[:-4]
        if len(raw) == 6 and raw.isalpha():
            return f'{raw[:3]}/{raw[3:]} OTC'
    return symbol


def _utc_naive(value: Any) -> pd.Timestamp:
    ts = pd.to_datetime(value, utc=True)
    return ts.tz_localize(None) if getattr(ts, 'tzinfo', None) is not None else ts


def normalize_candles(candles: list[Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candle in candles:
        if hasattr(candle, 'model_dump'):
            raw = candle.model_dump()
        elif isinstance(candle, dict):
            raw = candle
        else:
            raw = vars(candle)
        timestamp = raw.get('timestamp') or raw.get('time') or raw.get('from')
        if timestamp is None:
            continue
        rows.append({'timestamp': _utc_naive(timestamp),'open': float(raw['open']),'high': float(raw['high']),'low': float(raw['low']),'close': float(raw['close']),'volume': float(raw.get('volume') or 0.0)})
    if not rows:
        return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    frame = pd.DataFrame(rows).sort_values('timestamp').drop_duplicates('timestamp', keep='last')
    return frame.reset_index(drop=True)


def resample_from_1m(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    seconds = TIMEFRAME_SECONDS[timeframe]
    if seconds == 60:
        return frame.copy()
    if frame.empty:
        return frame.copy()
    rule = f'{seconds}s'
    df = frame.copy().set_index(pd.to_datetime(frame['timestamp']))
    result = df.resample(rule, origin='epoch', label='left', closed='left').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna(subset=['open','high','low','close'])
    result['timestamp'] = result.index
    return result.reset_index(drop=True)[['timestamp','open','high','low','close','volume']]


class PocketOptionOTCProvider:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: Any | None = None
        self._connect_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.settings.pocket_option_ssid.strip():
            raise RuntimeError('POCKET_OPTION_SSID is not configured; OTC data is disabled')
        async with self._connect_lock:
            if self._client is not None:
                return self._client
            try:
                from pocketoptionapi_async.client import AsyncPocketOptionClient
            except ImportError as exc:
                raise RuntimeError('PocketOption read-only adapter is not installed. Install requirements.txt.') from exc
            client = AsyncPocketOptionClient(ssid=self.settings.pocket_option_ssid,is_demo=self.settings.pocket_option_demo,persistent_connection=True,auto_reconnect=True,enable_logging=False)
            connected = await client.connect(persistent=True)
            if not connected:
                raise RuntimeError('Could not authenticate to Pocket Option market stream; refresh POCKET_OPTION_SSID')
            self._client = client
            logger.info('Pocket Option OTC read-only market-data connection is ready')
            return client

    async def fetch_1m(self, asset: str, count: int = 320) -> pd.DataFrame:
        client = await self._ensure_client()
        async with self._request_lock:
            candles = await client.get_candles(asset=asset, timeframe=60, count=count)
        frame = normalize_candles(candles)
        if len(frame) < 60:
            raise RuntimeError(f'Not enough OTC candles for {asset}: received {len(frame)}')
        return frame.tail(count).reset_index(drop=True)

    async def fetch(self, asset: str, timeframe: str, count: int = 250) -> pd.DataFrame:
        base = await self.fetch_1m(asset, max(320, count * TIMEFRAME_SECONDS[timeframe] // 60))
        return resample_from_1m(base, timeframe).tail(count).reset_index(drop=True)

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                logger.exception('Failed to close Pocket Option client cleanly')
            self._client = None


def next_entry_time(timeframe: str, now: datetime | None = None, lead_seconds: int | None = None) -> datetime:
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    period = TIMEFRAME_SECONDS[timeframe]
    lead = settings.entry_lead_seconds if lead_seconds is None else lead_seconds
    epoch = int(now.timestamp())
    boundary = ((epoch // period) + 1) * period
    if boundary - epoch < lead:
        boundary += period
    return datetime.fromtimestamp(boundary, tz=timezone.utc).replace(tzinfo=None)


def price_at_entry(frame_1m: pd.DataFrame, moment: datetime) -> float | None:
    if frame_1m.empty:
        return None
    target = pd.Timestamp(moment)
    timestamps = pd.to_datetime(frame_1m['timestamp'])
    exact = frame_1m.loc[timestamps == target]
    if not exact.empty:
        return float(exact['open'].iloc[0])
    after = frame_1m.loc[timestamps >= target]
    if not after.empty and (pd.to_datetime(after['timestamp'].iloc[0]) - target).total_seconds() <= 60:
        return float(after['open'].iloc[0])
    return None


def price_at_or_before(frame_1m: pd.DataFrame, moment: datetime) -> float | None:
    if frame_1m.empty:
        return None
    target = pd.Timestamp(moment)
    close_times = pd.to_datetime(frame_1m['timestamp']) + pd.to_timedelta(60, unit='s')
    eligible = frame_1m.loc[close_times <= target]
    if eligible.empty:
        return None
    return float(eligible['close'].iloc[-1])
