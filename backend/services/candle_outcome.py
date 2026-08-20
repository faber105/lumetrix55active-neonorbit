from __future__ import annotations

from datetime import datetime, timezone

from backend.services.pocketoption_otc import TF_SECONDS, MarketDataUnavailable, market_data


def _timestamp(value: datetime | int | float) -> int:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return int(float(value))


def _match_bucket(candles: list[dict], bucket: int, period: int) -> tuple[float, float] | None:
    for candle in reversed(candles):
        ts = int(candle['time'])
        # Pocket feeds are not fully consistent about whether a candle timestamp
        # denotes the interval start or interval end. Accept either convention.
        for candidate_ts in (ts, ts - period):
            candle_bucket = candidate_ts - (candidate_ts % period)
            if candle_bucket == bucket:
                return float(candle['open']), float(candle['close'])
    return None


async def exact_signal_candle_prices(asset: str, timeframe: str, entry_time: datetime | int | float) -> tuple[float, float]:
    if timeframe not in TF_SECONDS:
        raise MarketDataUnavailable(f'Unsupported timeframe: {timeframe}')

    period = int(TF_SECONDS[timeframe])
    target = _timestamp(entry_time)
    bucket = target - (target % period)

    candles = await market_data.get_candles(asset, timeframe, 240)
    matched = _match_bucket(candles, bucket, period)
    if matched is not None:
        return matched

    # Fallback for the fast-entry scheduler: signals can now be opened a few
    # seconds after analysis instead of exactly on a timeframe boundary. If the
    # broker omits that exact bucket, compare the nearest available 1m boundary
    # prices around the actual entry/expiry. This keeps stale PENDING signals
    # from blocking every scanner tick while preserving live Pocket pricing.
    try:
        entry_price = await market_data.boundary_price(asset, target)
        expiry_price = await market_data.boundary_price(asset, target + period)
        return float(entry_price), float(expiry_price)
    except Exception as exc:
        raise MarketDataUnavailable(
            f'Exact {timeframe} signal candle not found for {asset}; boundary fallback failed'
        ) from exc
