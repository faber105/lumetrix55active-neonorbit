from __future__ import annotations

from datetime import datetime, timezone

from backend.services.pocketoption_otc import TF_SECONDS, MarketDataUnavailable, market_data


def _timestamp(value: datetime | int | float) -> int:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return int(float(value))


async def exact_signal_candle_prices(asset: str, timeframe: str, entry_time: datetime | int | float) -> tuple[float, float]:
    if timeframe not in TF_SECONDS:
        raise MarketDataUnavailable(f'Unsupported timeframe: {timeframe}')
    period = TF_SECONDS[timeframe]
    target = _timestamp(entry_time)
    bucket = target - (target % period)
    candles = await market_data.get_candles(asset, timeframe, 240)
    for candle in reversed(candles):
        candle_bucket = int(candle['time']) - (int(candle['time']) % period)
        if candle_bucket == bucket:
            return float(candle['open']), float(candle['close'])
    raise MarketDataUnavailable(f'Exact {timeframe} signal candle not found for {asset}')
