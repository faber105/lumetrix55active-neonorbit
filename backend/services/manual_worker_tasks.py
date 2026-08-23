from __future__ import annotations

from datetime import datetime, timezone

from backend.services.pocketoption_otc import DISPLAY_TO_ASSET, OTC_ASSETS, TF_SECONDS, market_data
from backend.services.signal_engine import signal_engine
from backend.services.signal_store import save_signal
from backend.services.strategies import STRATEGIES

MANUAL_TIMEFRAMES = {"15s", "1m", "3m", "5m", "15m"}
MIN_MANUAL_CONFIDENCE = 70.0


def _timeframe(payload: dict) -> str:
    timeframe = str(payload.get("timeframe") or "1m")
    if timeframe not in MANUAL_TIMEFRAMES or timeframe not in TF_SECONDS:
        raise ValueError("Unsupported timeframe")
    return timeframe


def _threshold(payload: dict, default: float) -> float:
    try:
        return max(0.0, min(99.0, float(payload.get("min_confidence", default))))
    except (TypeError, ValueError):
        return float(default)


async def analyze_market(payload: dict) -> dict:
    pair = str(payload.get("pair") or "").replace(" OTC", "").strip()
    timeframe = _timeframe(payload)
    asset = DISPLAY_TO_ASSET.get(pair)
    if not asset:
        raise ValueError("Unsupported OTC pair")
    strategy = str(payload.get("strategy") or "").strip()
    threshold = _threshold(payload, MIN_MANUAL_CONFIDENCE)
    if strategy:
        if strategy not in STRATEGIES:
            raise ValueError("Unknown strategy")
        candidate = await signal_engine.evaluate_asset(asset, timeframe, strategy)
    else:
        candidate = await signal_engine.evaluate_asset_composite(asset, timeframe)
    if not candidate or float(candidate.get("confidence") or 0) < threshold:
        return {
            "status": "NO_SIGNAL",
            "pair": OTC_ASSETS[asset],
            "timeframe": timeframe,
            "strategy": strategy or None,
            "signal": None,
            "reason": "Условия выбранной стратегии сейчас не подтверждены.",
        }
    signal, duplicate = await save_signal(candidate, is_vip=False)
    return {
        "status": "SIGNAL",
        "signal": signal,
        "duplicate": duplicate,
        "analysis": {
            "engine": "Windows Worker",
            "strategy": candidate.get("strategy_label") or candidate.get("strategy"),
            "confirmations": candidate.get("confirmations", []),
            "indicators": candidate.get("indicators", {}),
        },
    }


async def scan_strategy(payload: dict) -> dict:
    timeframe = _timeframe(payload)
    strategy = str(payload.get("strategy") or "").strip()
    if strategy not in STRATEGIES:
        raise ValueError("Unknown strategy")
    threshold = _threshold(payload, 72.0)
    candidate = await signal_engine.scan_strategy(
        timeframe,
        list(OTC_ASSETS.keys()),
        strategy,
    )
    if not candidate or float(candidate.get("confidence") or 0) < threshold:
        return {
            "status": "NO_SIGNAL",
            "signal": None,
            "strategy": strategy,
            "timeframe": timeframe,
            "threshold": threshold,
        }
    signal, duplicate = await save_signal(candidate, is_vip=False)
    return {"status": "SIGNAL", "signal": signal, "duplicate": duplicate}


async def scan_best(payload: dict) -> dict:
    timeframe = _timeframe(payload)
    requested = payload.get("assets") or list(OTC_ASSETS.keys())
    assets = [str(asset) for asset in requested if str(asset) in OTC_ASSETS]
    if not assets:
        raise ValueError("No supported OTC assets")
    threshold = _threshold(payload, 72.0)
    candidate = await signal_engine.scan_best(timeframe, assets)
    if not candidate or float(candidate.get("confidence") or 0) < threshold:
        return {"status": "NO_SIGNAL", "signal": None, "threshold": threshold}
    signal, duplicate = await save_signal(candidate, is_vip=False)
    return {"status": "SIGNAL", "signal": signal, "duplicate": duplicate}


async def reconcile_signals(_payload: dict | None = None) -> dict:
    from backend.services.reconciler import reconcile_pending

    return await reconcile_pending()


async def candles(payload: dict) -> dict:
    pair = str(payload.get("pair") or "").replace(" OTC", "").strip()
    timeframe = _timeframe(payload)
    count = max(20, min(120, int(payload.get("count") or 60)))
    asset = DISPLAY_TO_ASSET.get(pair)
    if not asset:
        raise ValueError("Unsupported OTC pair")
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
