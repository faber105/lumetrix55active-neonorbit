from __future__ import annotations

import asyncio
from collections import defaultdict

from backend.services.pocket_direct import DirectPocketOptionClient
from backend.services.pocketoption_otc import market_data

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


def _aggregate(candles: list[dict], period: int = 15) -> list[dict]:
    buckets: dict[int, list[dict]] = defaultdict(list)
    for item in candles:
        try:
            ts = int(item.get("time") or 0)
            if ts <= 0:
                continue
            buckets[ts - (ts % period)].append(item)
        except Exception:
            continue
    out: list[dict] = []
    for ts in sorted(buckets):
        rows = sorted(buckets[ts], key=lambda x: int(x.get("time") or 0))
        try:
            op = float(rows[0].get("open", rows[0].get("close")))
            close = float(rows[-1].get("close"))
            high = max(float(row.get("high", row.get("close"))) for row in rows)
            low = min(float(row.get("low", row.get("close"))) for row in rows)
        except Exception:
            continue
        out.append({"time": ts, "open": op, "high": max(high, op, close), "low": min(low, op, close), "close": close})
    return out


async def broker_live_chart(asset: str, count: int = 60) -> tuple[list[dict], float]:
    """Return a 15-second chart built from broker-direct short-period data.

    The previous Mini App chart relied on historical 15s snapshots. Pocket may
    return those only after a candle is completed, which makes an open deal look
    frozen or shifted. We first request 1-second broker data and aggregate it into
    15-second candles ourselves. If the broker does not expose 1s history for the
    current session, we fall back to its direct 15s history.
    """
    client = await _client_for_market()
    wanted = max(40, min(120, int(count)))
    async with _lock:
        try:
            raw = await client.get_candles(asset, 1, count=max(180, wanted * 15))
            candles = _aggregate(raw, 15)
            if candles:
                return candles[-wanted:], float(raw[-1]["close"])
        except Exception:
            pass

        raw15 = await client.get_candles(asset, 15, count=wanted)
        if not raw15:
            raise RuntimeError("Pocket direct chart returned no candles")
        return raw15[-wanted:], float(raw15[-1]["close"])
