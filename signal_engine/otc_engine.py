from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

import pandas as pd

from config import get_settings
from signal_engine.online_ml import online_model
from signal_engine.otc_provider import TIMEFRAME_SECONDS, next_entry_time, resample_from_1m
from signal_engine.strategies import choose_strategy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OTCAnalysis:
    asset: str
    timeframe: str
    status: str
    direction: str | None
    confidence: float
    strategy: str
    regime: str
    reason: str
    entry_time: datetime | None
    expires_at: datetime | None
    entry_price_reference: float | None
    strategy_score: float
    ml_direction: str | None
    ml_confidence: float
    features: dict[str, Any]


class OTCSignalEngine:
    def __init__(self) -> None:
        self.settings = get_settings()

    def analyze_frame(self, asset: str, timeframe: str, frame: pd.DataFrame) -> OTCAnalysis:
        seconds = TIMEFRAME_SECONDS[timeframe]
        decision = choose_strategy(frame, seconds)
        if not decision.direction or decision.score < self.settings.strategy_threshold:
            return OTCAnalysis(asset, timeframe, 'NO_SIGNAL', None, decision.score, decision.strategy, decision.regime, decision.reason or 'No strategy has a valid setup', None, None, None, decision.score, None, 0.50, decision.features)

        ml_direction, ml_confidence = online_model.predict(decision.features)
        strategy_weight = 1.0 - self.settings.ml_weight
        if ml_direction is None:
            final_conf = decision.score
        elif ml_direction == decision.direction:
            final_conf = decision.score * strategy_weight + ml_confidence * self.settings.ml_weight
        else:
            final_conf = decision.score * strategy_weight + (1.0 - ml_confidence) * self.settings.ml_weight

        if final_conf < self.settings.confidence_threshold:
            return OTCAnalysis(asset, timeframe, 'NO_SIGNAL', None, final_conf, decision.strategy, decision.regime, f'{decision.reason}; ML filter did not confirm enough', None, None, None, decision.score, ml_direction, ml_confidence, decision.features)

        entry_time = next_entry_time(timeframe)
        expires_at = entry_time + timedelta(seconds=seconds)
        reference = float(frame['close'].iloc[-1]) if not frame.empty else None
        return OTCAnalysis(asset=asset,timeframe=timeframe,status='SIGNAL',direction=decision.direction,confidence=max(0.0, min(0.99, final_conf)),strategy=decision.strategy,regime=decision.regime,reason=decision.reason,entry_time=entry_time,expires_at=expires_at,entry_price_reference=reference,strategy_score=decision.score,ml_direction=ml_direction,ml_confidence=ml_confidence,features=decision.features)

    def analyze_all_timeframes(self, asset: str, base_1m: pd.DataFrame) -> list[OTCAnalysis]:
        results: list[OTCAnalysis] = []
        for timeframe in self.settings.parsed_otc_timeframes:
            frame = resample_from_1m(base_1m, timeframe).tail(250).reset_index(drop=True)
            results.append(self.analyze_frame(asset, timeframe, frame))
        return results
