from __future__ import annotations

import asyncio
from collections import defaultdict

from backend.services.pocket_direct import DirectPocketOptionClient
from backend.services.pocketoption_otc import TF_SECONDS, market_data

_lock = asyncio.Lock()
_client: DirectPocketOptionClient | None = None
_client_ssid: str | None = None


async def _client_for_market() -> DirectPocketOptionClient:
    global _client, _client_ssid
    await market_data._refresh_private_ssid()
    ssid = str(market_data.ssid or "").strip()
    if not ssid:
        raise RuntimeError("Pocket market session is not configured")
    if _client is None or _client_ssid != ssid:
        if _client is not None:
            try:
                await _client.disconnect()
            except Exception:
                pass
        _client = DirectPocketOptionClient(ssid, is_demo=True)
        _client_ssid = ssid
    return _client


def _normalize(candles: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in candles or []:
        try:
            ts = int(float(item.get("time") or item.get("timestamp") or 0))
            if ts > 10_000_000_000:
                ts //= 1000
            close = float(item.get("close", item.get("price")))
            op = float(item.get("open", close))
            high = float(item.get("high", close))
            low = float(item.get("low", close))
            out.append({
                "time": ts,
                "open": op,
                "high": max(high, op, close),
                "low": min(low, op, close),
                "close": close,
            })
        except Exception:
            continue
    unique = {int(candle["time"]): candle for candle in out if int(candle.get("time") or 0) > 0}
    return [unique[key] for key in sorted(unique)]


def _aggregate(candles: list[dict], period: int) -> list[dict]:
    buckets: dict[int, list[dict]] = defaultdict(list)
    for item in _normalize(candles):
        ts = int(item["time"])
        buckets[ts - (ts % period)].append(item)
    out: list[dict] = []
    for ts in sorted(buckets):
        rows = sorted(buckets[ts], key=lambda x: int(x["time"]))
        op = float(rows[0]["open"])
        close = float(rows[-1]["close"])
        high = max(float(row["high"]) for row in rows)
        low = min(float(row["low"]) for row in rows)
        out.append({"time": ts, "open": op, "high": max(high, op, close), "low": min(low, op, close), "close": close})
    return out


def _patch_current_candle(base: list[dict], ticks_1s: list[dict], period: int) -> tuple[list[dict], float]:
    ticks = _normalize(ticks_1s)
    if not ticks:
        if not base:
            raise RuntimeError("Pocket direct chart returned no candles")
        return base, float(base[-1]["close"])

    current_price = float(ticks[-1]["close"])
    current_bucket = int(ticks[-1]["time"]) - (int(ticks[-1]["time"]) % period)
    current_ticks = [row for row in ticks if int(row["time"]) - (int(row["time"]) % period) == current_bucket]
    if not current_ticks:
        return base, current_price

    rows = [dict(row) for row in base]
    existing = next((row for row in reversed(rows) if int(row.get("time") or 0) == current_bucket), None)
    op = float(existing["open"]) if existing else float(current_ticks[0]["open"])
    high_values = [float(row["high"]) for row in current_ticks]
    low_values = [float(row["low"]) for row in current_ticks]
    if existing:
        high_values.append(float(existing["high"]))
        low_values.append(float(existing["low"]))
    patched = {
        "time": current_bucket,
        "open": op,
        "high": max(high_values + [op, current_price]),
        "low": min(low_values + [op, current_price]),
        "close": current_price,
    }
    if existing:
        rows = [patched if int(row.get("time") or 0) == current_bucket else row for row in rows]
    else:
        rows.append(patched)
    rows.sort(key=lambda row: int(row.get("time") or 0))
    return rows, current_price


async def broker_live_chart(asset: str, timeframe: str, count: int = 60) -> tuple[list[dict], float, str]:
    """Return Pocket candles on the exact trade timeframe, patched with fresh 1s broker data.

    The completed/history candles use the same timeframe as the position. A short
    1-second broker request is then merged into the currently forming candle so
    the Mini App does not wait for Pocket to close the candle before moving it.
    """
    client = await _client_for_market()
    wanted = max(40, min(120, int(count)))
    tf = str(timeframe or "15s")
    period = int(TF_SECONDS.get(tf, 15))

    async with _lock:
        history: list[dict] = []
        history_error: Exception | None = None
        try:
            history = _normalize(await client.get_candles(asset, period, count=wanted))
        except Exception as exc:
            history_error = exc

        ticks: list[dict] = []
        try:
            # We only need enough one-second rows to reconstruct the live candle.
            # Cap the request so a 5m/15m chart remains light on Vercel.
            tick_count = max(45, min(360, period + 30))
            ticks = await client.get_candles(asset, 1, count=tick_count)
        except Exception:
            ticks = []

        if not history and ticks:
            history = _aggregate(ticks, period)
        if not history:
            if history_error:
                raise history_error
            raise RuntimeError("Pocket direct chart returned no candles")

        patched, current_price = _patch_current_candle(history, ticks, period)
        source = f"broker-direct-{tf}+1s-live" if ticks else f"broker-direct-{tf}"
        return patched[-wanted:], float(current_price), source
