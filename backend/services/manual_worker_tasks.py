from __future__ import annotations

from datetime import datetime, timezone

from backend.services.pocketoption_otc import DISPLAY_TO_ASSET, OTC_ASSETS, TF_SECONDS, market_data
from backend.services.signal_engine import signal_engine
from backend.services.signal_store import save_signal

MANUAL_TIMEFRAMES = {"15s", "1m", "3m", "5m", "15m"}
MIN_MANUAL_CONFIDENCE = 70.0


async def analyze_market(payload: dict) -> dict:
    pair = str(payload.get("pair") or "").replace(" OTC", "").strip()
    timeframe = str(payload.get("timeframe") or "1m")
    asset = DISPLAY_TO_ASSET.get(pair)
    if not asset:
        raise ValueError("Unsupported OTC pair")
    if timeframe not in MANUAL_TIMEFRAMES or timeframe not in TF_SECONDS:
        raise ValueError("Unsupported timeframe")
    candidate = await signal_engine.evaluate_asset_composite(asset, timeframe)
    if not candidate or float(candidate.get("confidence") or 0) < MIN_MANUAL_CONFIDENCE:
        return {
            "status": "NO_SIGNAL",
            "pair": OTC_ASSETS[asset],
            "timeframe": timeframe,
            "signal": None,
            "reason": "Сейчас нет подтверждённой точки входа. Trend, momentum и volatility-фильтры не дали достаточного совпадения.",
        }
    signal, duplicate = await save_signal(candidate, is_vip=False)
    return {
        "status": "SIGNAL",
        "signal": signal,
        "duplicate": duplicate,
        "analysis": {
            "engine": "Composite Analysis",
            "strategy": candidate.get("strategy_label"),
            "confirmations": candidate.get("confirmations", []),
            "indicators": candidate.get("indicators", {}),
        },
    }


async def candles(payload: dict) -> dict:
    pair = str(payload.get("pair") or "").replace(" OTC", "").strip()
    timeframe = str(payload.get("timeframe") or "1m")
    count = max(20, min(120, int(payload.get("count") or 60)))
    asset = DISPLAY_TO_ASSET.get(pair)
    if not asset:
        raise ValueError("Unsupported OTC pair")
    if timeframe not in TF_SECONDS:
        raise ValueError("Unsupported timeframe")
    rows = await market_data.get_candles(asset, timeframe, count)
    current_price = float(rows[-1]["close"]) if rows else await market_data.latest_price(asset)
    return {
        "pair": OTC_ASSETS[asset],
        "asset": asset,
        "timeframe": timeframe,
        "current_price": float(current_price),
        "server_time": datetime.now(timezone.utc).isoformat(),
        "candles": rows,
    }
