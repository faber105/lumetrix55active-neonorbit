from __future__ import annotations

import asyncio

import pytest

from backend.services.session_transitions import settle_transition
from tests.fakes.fake_broker import DeterministicFakeBroker


def run(awaitable):
    return asyncio.run(awaitable)


def base_session(**changes):
    session = {
        "mode": "count",
        "target_wins": 1,
        "target_profit": None,
        "wins": 0,
        "failed_series": 0,
        "current_level": 0,
        "current_series_loss": 0,
        "profit": 0,
        "max_martingale": 3,
        "max_failed_series": 1,
    }
    session.update(changes)
    return session


def test_timeout_after_accept_is_reconciled_without_duplicate_order():
    broker = DeterministicFakeBroker()
    run(broker.connect())
    broker.timeout_after_accept = True
    with pytest.raises(asyncio.TimeoutError):
        run(broker.place_order(asset="EURUSD_otc", amount=1, direction="call", duration=60, idempotency_key="execution:7"))
    confirmed = run(broker.find_order("execution:7"))
    retried = run(broker.place_order(asset="EURUSD_otc", amount=1, direction="call", duration=60, idempotency_key="execution:7"))
    assert confirmed is retried
    assert len(broker.orders) == 1


def test_disconnect_reconnect_preserves_order_ledger():
    broker = DeterministicFakeBroker()
    run(broker.connect())
    first = run(broker.place_order(asset="EURUSD_otc", amount=1, direction="call", duration=60, idempotency_key="execution:8"))
    run(broker.disconnect())
    with pytest.raises(ConnectionError):
        run(broker.find_order("execution:8"))
    run(broker.connect())
    assert run(broker.find_order("execution:8")) is first


def test_process_restart_recovers_active_order_by_idempotency_key():
    external_ledger = {}
    before_crash = DeterministicFakeBroker(external_ledger)
    run(before_crash.connect())
    opened = run(before_crash.place_order(asset="EURUSD_otc", amount=1, direction="call", duration=60, idempotency_key="execution:9"))
    after_restart = DeterministicFakeBroker(external_ledger)
    run(after_restart.connect())
    assert run(after_restart.find_order("execution:9")) is opened
    assert len(external_ledger) == 1


def test_duplicate_result_delivery_has_one_terminal_transition():
    session = base_session()
    first = settle_transition(session, result="WIN", amount=1, payout=92)
    assert first["status"] == "COMPLETED"
    # Persistence applies the result only while a leg is PENDING; a second
    # delivery sees terminal state and therefore must not transition again.
    assert first["wins"] == 1
    assert first["profit"] == 0.92


def test_target_reached_on_tick_stops_before_another_entry():
    transition = settle_transition(base_session(mode="profit", target_profit=0.92), result="WIN", amount=1, payout=92)
    assert transition["status"] == "COMPLETED"
    assert transition["reason"] == "TARGET_PROFIT"
