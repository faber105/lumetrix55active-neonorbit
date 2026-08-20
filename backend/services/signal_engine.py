from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Iterable, Optional

from backend.services.auto_scan_scope import get_auto_scan_scope
from backend.services.online_ml import get_model
from backend.services.pocketoption_otc import MarketDataUnavailable, OTC_ASSETS, TF_SECONDS, market_data
from backend.services.strategies import (
    MANUAL_STRATEGIES,
    SMART_STRATEGIES,
    STRATEGY_LABELS,
    StrategyCandidate,
    evaluate,
    evaluate_best,
)

logger = logging.getLogger("alphapulse.engine")
ENTRY_LEAD_SECONDS = max(2, min(12, int(os.getenv("ENTRY_LEAD_SECONDS", "4"))))
SMART_EXECUTION_STRATEGIES = tuple(SMART_STRATEGIES) + ("vip_confluence",)
SCAN_CONCURRENCY = max(1, min(8, int(os.getenv("SIGNAL_SCAN_CONCURRENCY", "4"))))
CANDLE_LOOKBACK = max(100, min(140, int(os.getenv("SIGNAL_CANDLE_LOOKBACK", "110"))))

TF_SECONDS.setdefault("15s", 15)
TF_SECONDS.setdefault("3m", 180)


def _fallback_bias(candles: list) -> StrategyCandidate:
    closes = [float(c["close"]) for c in candles]
    opens = [float(c["open"]) for c in candles]
    recent = closes[-12:]
    fast = sum(recent[-4:]) / 4
    slow = sum(recent) / len(recent)
    momentum = closes[-1] - closes[-4]
    body = closes[-1] - opens[-1]
    direction = "BUY" if (fast > slow or (fast == slow and (momentum > 0 or body >= 0))) else "SELL"
    denom = max(abs(slow), 1e-9)
    strength = min(5.5, abs(fast - slow) / denom * 100000.0)
    confidence = round(70.0 + strength, 1)
    return StrategyCandidate(
        strategy="market_bias",
        direction=direction,
        confidence=confidence,
        reason=(
            "Live market bias: strict strategy filters are not fully aligned, so the engine "
            "uses the short-term price slope and candle momentum for the current direction."
        ),
        features=[0.0] * 12,
        indicators={"fast_mean": fast, "slow_mean": slow, "momentum": momentum, "last_body": body},
        confirmations=["live OTC price slope", "recent candle momentum", "short-term mean direction"],
    )


class SignalEngine:
    async def _candidate_dict(self, asset, timeframe, candidate, candles, *, is_vip=False):
        if candidate.strategy == "market_bias":
            ml_p = 0.5
            confidence = float(candidate.confidence)
            ml_samples = 0
            ml_influence = False
            strategy_label = "Live Market Bias"
        else:
            model = get_model(candidate.strategy)
            await model.hydrate()
            ml_p = model.probability_setup_wins(candidate.features)
            confidence = float(candidate.confidence)
            if model.influence_ready():
                confidence = 0.82 * confidence + 0.18 * (ml_p * 100.0)
            ml_samples = model.samples
            ml_influence = model.influence_ready()
            strategy_label = STRATEGY_LABELS[candidate.strategy]

        server_ts = await market_data.server_time()
        seconds = int(TF_SECONDS[timeframe])
        entry_ts = server_ts + ENTRY_LEAD_SECONDS
        expiry_ts = entry_ts + seconds
        return {
            "pair": OTC_ASSETS[asset],
            "asset": asset,
            "timeframe": timeframe,
            "strategy": candidate.strategy,
            "strategy_label": strategy_label,
            "direction": candidate.direction,
            "confidence": round(max(0.0, min(99.0, confidence)), 1),
            "model_probability": round(ml_p * 100.0, 1),
            "ml_samples": ml_samples,
            "ml_influence": ml_influence,
            "reason": candidate.reason,
            "confirmations": list(candidate.confirmations),
            "features": candidate.features,
            "indicators": candidate.indicators,
            "analysis_price": float(candles[-1]["close"]),
            "entry_time": datetime.fromtimestamp(entry_ts, tz=timezone.utc).isoformat(),
            "expiry_time": datetime.fromtimestamp(expiry_ts, tz=timezone.utc).isoformat(),
            "is_vip": bool(is_vip),
        }

    async def evaluate_asset(self, asset: str, timeframe: str, strategy: str) -> Optional[dict]:
        if timeframe not in TF_SECONDS:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        candles = await market_data.get_candles(asset, timeframe, CANDLE_LOOKBACK)
        candidate = evaluate(strategy, candles)
        return None if candidate is None else await self._candidate_dict(asset, timeframe, candidate, candles)

    async def _evaluate_asset_best(self, asset: str, timeframe: str, strategies: Iterable[str]) -> Optional[dict]:
        if timeframe not in TF_SECONDS:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        candles = await market_data.get_candles(asset, timeframe, CANDLE_LOOKBACK)
        candidate = evaluate_best(candles, strategies)
        return None if candidate is None else await self._candidate_dict(asset, timeframe, candidate, candles)

    async def evaluate_asset_composite(self, asset: str, timeframe: str) -> Optional[dict]:
        if timeframe not in TF_SECONDS:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        candles = await market_data.get_candles(asset, timeframe, CANDLE_LOOKBACK)
        candidate = evaluate_best(candles, MANUAL_STRATEGIES)
        if candidate is None:
            candidate = _fallback_bias(candles)
        return await self._candidate_dict(asset, timeframe, candidate, candles)

    async def evaluate_vip_asset(self, asset: str) -> Optional[dict]:
        candles = await market_data.get_candles(asset, "5m", CANDLE_LOOKBACK)
        candidate = evaluate("vip_confluence", candles)
        return None if candidate is None else await self._candidate_dict(asset, "5m", candidate, candles, is_vip=True)

    async def _gather_candidates(self, assets: Iterable[str], evaluator) -> list[dict]:
        requested = list(dict.fromkeys(str(asset) for asset in assets if asset))
        eligible_scope, discovered_count = get_auto_scan_scope()
        if eligible_scope and discovered_count and len(requested) >= discovered_count:
            requested_set = set(requested)
            requested = [asset for asset in eligible_scope if asset in requested_set]

        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def one(asset: str):
            if asset not in OTC_ASSETS:
                return None
            async with semaphore:
                try:
                    return await evaluator(asset)
                except MarketDataUnavailable as exc:
                    if "SSID" in str(exc) or "connection failed" in str(exc).lower():
                        raise
                    logger.warning("Market scan skipped %s: %s", asset, type(exc).__name__)
                except Exception as exc:
                    logger.warning("Market scan failed %s: %s", asset, type(exc).__name__)
                return None

        tasks = [asyncio.create_task(one(asset)) for asset in requested]
        if not tasks:
            return []
        results = await asyncio.gather(*tasks)
        return [item for item in results if item]

    async def scan_strategy_candidates(self, timeframe: str, assets: Iterable[str], strategy: str) -> list[dict]:
        results = await self._gather_candidates(assets, lambda asset: self.evaluate_asset(asset, timeframe, strategy))
        return sorted(results, key=lambda x: float(x.get("confidence") or 0), reverse=True)

    async def scan_strategy(self, timeframe: str, assets: Iterable[str], strategy: str) -> Optional[dict]:
        results = await self.scan_strategy_candidates(timeframe, assets, strategy)
        return results[0] if results else None

    async def scan_best_candidates(self, timeframe: str, assets: Iterable[str]) -> list[dict]:
        results = await self._gather_candidates(
            assets,
            lambda asset: self._evaluate_asset_best(asset, timeframe, SMART_EXECUTION_STRATEGIES),
        )
        return sorted(results, key=lambda x: float(x.get("confidence") or 0), reverse=True)

    async def scan_best(self, timeframe: str, assets: Iterable[str]) -> Optional[dict]:
        results = await self.scan_best_candidates(timeframe, assets)
        return results[0] if results else None

    async def scan_vip(self, assets: Iterable[str]) -> Optional[dict]:
        results = await self._gather_candidates(assets, self.evaluate_vip_asset)
        return max(results, key=lambda x: float(x.get("confidence") or 0)) if results else None


signal_engine = SignalEngine()
