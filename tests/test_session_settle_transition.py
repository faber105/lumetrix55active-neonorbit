from __future__ import annotations

from backend.services.session_transitions import settle_transition


def session(**changes):
    value = {
        "mode": "count",
        "target_wins": 5,
        "target_profit": None,
        "max_martingale": 3,
        "max_failed_series": 1,
        "wins": 0,
        "failed_series": 0,
        "current_level": 0,
        "current_series_loss": 0,
        "profit": 0,
    }
    value.update(changes)
    return value


def test_four_losses_reset_chain_and_count_session_continues():
    state = session()
    transition = None
    for level, amount in enumerate((1.0, 2.09, 4.36, 9.10)):
        state["current_level"] = level
        transition = settle_transition(state, result="LOSS", amount=amount, payout=92)
        if level < 3:
            assert transition["status"] == "ACTIVE"
            assert transition["level"] == level + 1
            state["current_series_loss"] = transition["series_loss"]
        else:
            assert transition["status"] == "ACTIVE"
            assert transition["reason"] is None
            assert transition["level"] == 0
            assert transition["series_loss"] == 0
            assert transition["failed"] == 1


def test_profit_mode_four_losses_stop_at_failed_series_limit():
    state = session(mode="profit", target_profit=10)
    transition = None
    for level, amount in enumerate((1.0, 2.09, 4.36, 9.10)):
        state["current_level"] = level
        transition = settle_transition(state, result="LOSS", amount=amount, payout=92)
        if level < 3:
            state["current_series_loss"] = transition["series_loss"]
        else:
            assert transition["status"] == "STOPPED"
            assert transition["reason"] == "MAX_FAILED_SERIES"


def test_draw_does_not_change_targets_or_level():
    transition = settle_transition(
        session(wins=2, current_level=2, current_series_loss=3.09, profit=-3.09),
        result="DRAW",
        amount=4.36,
        payout=92,
    )
    assert transition["wins"] == 2
    assert transition["failed"] == 0
    assert transition["level"] == 2
    assert transition["series_loss"] == 3.09
    assert transition["profit"] == -3.09


def test_win_target_completes_from_actual_result():
    transition = settle_transition(
        session(wins=4, current_level=1, current_series_loss=1),
        result="WIN",
        amount=2.09,
        payout=92,
    )
    assert transition["status"] == "COMPLETED"
    assert transition["reason"] == "TARGET_WINS"
    assert transition["wins"] == 5
    assert transition["level"] == 0


def test_unknown_result_is_rejected():
    try:
        settle_transition(session(), result="UNKNOWN", amount=1, payout=92)
    except ValueError as exc:
        assert "Unsupported broker result" in str(exc)
    else:
        raise AssertionError("UNKNOWN result must not mutate the session")
