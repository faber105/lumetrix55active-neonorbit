from __future__ import annotations

from backend.services import session_engine as _engine
from backend.services.signal_engine import signal_engine
from backend.services.strategies import SMART_STRATEGIES, STRATEGY_LABELS

SMART_ID = "smart_confluence"

# Smart Confluence is a first-class AUTO strategy in both session modes.
_engine.COUNT_STRATEGIES.add(SMART_ID)
_engine.PROFIT_STRATEGIES.add(SMART_ID)
STRATEGY_LABELS[SMART_ID] = "Smart Confluence · 5 стратегий"

_original_scan_strategy_candidates = signal_engine.scan_strategy_candidates


async def _scan_strategy_candidates(timeframe: str, assets, strategy: str):
    if str(strategy) != SMART_ID:
        return await _original_scan_strategy_candidates(timeframe, assets, strategy)

    # For every payout-eligible pair, evaluate the full normal AUTO arsenal and
    # keep only that pair's strongest independently confirmed setup. The session
    # engine then ranks all pairs and selects one best trade candidate.
    results = await signal_engine._gather_candidates(
        assets,
        lambda asset: signal_engine._evaluate_asset_best(asset, timeframe, SMART_STRATEGIES),
    )
    return sorted(results, key=lambda item: float(item.get("confidence") or 0), reverse=True)


if getattr(signal_engine.scan_strategy_candidates, "__name__", "") != "_scan_strategy_candidates":
    signal_engine.scan_strategy_candidates = _scan_strategy_candidates
