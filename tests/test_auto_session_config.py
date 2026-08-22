from __future__ import annotations

import pytest

from backend.routers.auto import _bet_plan
from backend.services.session_engine import AUTO_TIMEFRAMES, validate_session_config


@pytest.mark.parametrize("timeframe", sorted(AUTO_TIMEFRAMES))
def test_each_supported_timeframe_is_preserved(timeframe):
    values = validate_session_config(
        {
            "mode": "count",
            "strategy": "trend_pulse",
            "timeframe": timeframe,
            "target_wins": 1,
            "amount": 2.5,
            "max_martingale": 2,
        }
    )
    assert values["timeframe"] == timeframe
    assert values["strategy"] == "trend_pulse"


def test_manual_target_and_profit_strategy_are_not_overridden():
    values = validate_session_config(
        {
            "mode": "profit",
            "strategy": "range_reversal",
            "timeframe": "3m",
            "target_profit": 12.75,
            "amount": 3,
            "max_martingale": 3,
            "max_failed_series": 4,
        }
    )
    assert values["target_profit"] == 12.75
    assert values["strategy"] == "range_reversal"
    assert values["max_failed_series"] == 4


def test_bet_plan_uses_backend_recovery_formula():
    assert _bet_plan(1, 92, 3) == [1.0, 2.09, 4.36, 9.1]


def test_zero_win_target_is_rejected_instead_of_silently_replaced():
    with pytest.raises(ValueError, match="Target wins"):
        validate_session_config(
            {
                "mode": "count",
                "strategy": "trend_pulse",
                "timeframe": "1m",
                "target_wins": 0,
                "amount": 1,
                "max_martingale": 0,
            }
        )


def test_mixed_strategy_alias_is_rejected():
    with pytest.raises(ValueError, match="Unknown AUTO strategy"):
        validate_session_config(
            {
                "mode": "count",
                "strategy": "smart_confluence",
                "timeframe": "1m",
                "target_wins": 1,
                "amount": 1,
                "max_martingale": 0,
            }
        )
