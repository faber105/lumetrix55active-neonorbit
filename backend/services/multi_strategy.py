from __future__ import annotations

from contextvars import ContextVar
from typing import Iterable

from backend.services import auto_trade, preload_next, session_engine
from backend.services.preload_journal import preload_cycle as _base_preload_cycle
from backend.services.signal_engine import SMART_EXECUTION_STRATEGIES, signal_engine

_SELECTED_SCAN_STRATEGIES: ContextVar[tuple[str, ...] | None] = ContextVar(
    "auto_selected_scan_strategies", default=None
)

# Serverless/Pocket ticks can occasionally land several seconds after a 5m
# candle boundary. Keep enough grace so a valid prepared PROFIT entry is not
# discarded only because one scheduler tick arrived late. This still keeps the
# entry close to the intended candle open rather than drifting into the candle.
auto_trade.ENTRY_GRACE_SECONDS = max(float(auto_trade.ENTRY_GRACE_SECONDS), 12.0)

# PROFIT / "к цели" uses the AUTO payout setting as the hard admission gate.
# Strategy evaluators still decide whether a setup exists and rank candidates by
# confidence, but there is no second session-level 82% confidence cutoff.
session_engine.PROFIT_MIN_CONFIDENCE = 0.0
preload_next.PROFIT_MIN_CONFIDENCE = 0.0


def split_strategy_key(value: object) -> list[str]:
    """Return 1-2 unique AUTO strategies stored as a '+' separated DB key."""
    raw = [part.strip() for part in str(value or "smart_confluence").split("+") if part.strip()]
    unique: list[str] = []
    for strategy in raw:
        if strategy not in unique:
            unique.append(strategy)
    if not unique:
        unique = ["smart_confluence"]
    if len(unique) > 2:
        raise ValueError("Choose from 1 to 2 AUTO strategies")
    return unique


def _execution_strategies(selected: Iterable[str]) -> tuple[str, ...]:
    items = tuple(dict.fromkeys(str(item) for item in selected if item))
    if "smart_confluence" in items or "mixed_smart" in items:
        return tuple(SMART_EXECUTION_STRATEGIES)
    return items


def _validate_config(config: dict) -> dict:
    mode = str(config.get("mode") or "count").lower()
    selected = split_strategy_key(config.get("strategy"))
    strategy_key = "+".join(selected)
    amount = round(float(config.get("amount") or 1), 2)
    max_martingale = int(config.get("max_martingale", 3))

    if amount < 1 or amount > session_engine.MAX_SESSION_AMOUNT:
        raise ValueError("Amount must be between 1 and 50000")
    if max_martingale < 0 or max_martingale > 3:
        raise ValueError("Martingale covers must be between 0 and 3")

    if mode == "count":
        timeframe = str(config.get("timeframe") or "1m")
        target = int(config.get("target_wins") or 5)
        if any(strategy not in session_engine.COUNT_STRATEGIES for strategy in selected):
            raise ValueError("Unknown AUTO strategy")
        if timeframe not in session_engine.COUNT_TIMEFRAMES:
            raise ValueError("Count mode timeframe must be 15s, 1m or 3m")
        if target < 5 or target > 25:
            raise ValueError("Target wins must be between 5 and 25")
        return {
            "mode": mode,
            "strategy": strategy_key,
            "timeframe": timeframe,
            "target_wins": target,
            "target_profit": None,
            "amount": amount,
            "max_martingale": max_martingale,
            "max_failed_series": 1,
        }

    if mode == "profit":
        target = round(float(config.get("target_profit") or 1), 2)
        failed = int(config.get("max_failed_series") or 1)
        allowed_profit = set(session_engine.PROFIT_STRATEGIES) | {"mixed_smart"}
        if any(strategy not in allowed_profit for strategy in selected):
            raise ValueError("Unknown profit-mode strategy")
        if target <= 0:
            raise ValueError("Target profit must be positive")
        if failed < 1 or failed > 10:
            raise ValueError("Failed-series limit must be between 1 and 10")
        return {
            "mode": mode,
            "strategy": strategy_key,
            "timeframe": session_engine.PROFIT_TIMEFRAME,
            "target_wins": None,
            "target_profit": target,
            "amount": amount,
            "max_martingale": max_martingale,
            "max_failed_series": failed,
        }

    raise ValueError("Unknown session mode")


_ORIGINAL_SCAN_STRATEGY = signal_engine.scan_strategy_candidates
_ORIGINAL_SCAN_BEST = signal_engine.scan_best_candidates
_ORIGINAL_SESSION_TICK = session_engine.session_tick
_ORIGINAL_SETTLE = session_engine._settle
_ORIGINAL_EVENT = session_engine._event


async def _scan_strategy_candidates(timeframe: str, assets, strategy: str) -> list[dict]:
    selected = split_strategy_key(strategy)
    if len(selected) == 1 and selected[0] not in {"smart_confluence", "mixed_smart"}:
        return await _ORIGINAL_SCAN_STRATEGY(timeframe, assets, selected[0])

    execution = _execution_strategies(selected)
    results = await signal_engine._gather_candidates(
        assets,
        lambda asset: signal_engine._evaluate_asset_best(asset, timeframe, execution),
    )
    return sorted(results, key=lambda item: float(item.get("confidence") or 0), reverse=True)


async def _scan_best_candidates(timeframe: str, assets) -> list[dict]:
    selected = _SELECTED_SCAN_STRATEGIES.get()
    if not selected:
        return await _ORIGINAL_SCAN_BEST(timeframe, assets)
    results = await signal_engine._gather_candidates(
        assets,
        lambda asset: signal_engine._evaluate_asset_best(asset, timeframe, selected),
    )
    return sorted(results, key=lambda item: float(item.get("confidence") or 0), reverse=True)


async def _event(session_id, stage, message, payload=None):
    """Make the session journal explicit about the setup chosen for entry."""
    if str(stage) == "SIGNAL_FOUND":
        text = str(message or "")
        if text.startswith("Найден сигнал "):
            selected = text[len("Найден сигнал "):].strip()
            message = f"Из найденных сетапов выбрана лучшая пара: {selected}"
        elif text:
            message = f"Выбрана лучшая пара для входа: {text}"
        data = dict(payload or {})
        data["selected_best"] = True
        payload = data
    await _ORIGINAL_EVENT(session_id, stage, message, payload)


async def _settle(session):
    """After a confirmed LOSS, arm martingale immediately and persist its stake."""
    previous_level = int(session.get("current_level") or 0)
    settled = await _ORIGINAL_SETTLE(session)
    if not settled or str(settled.get("status")) != "ACTIVE":
        return settled

    level = int(settled.get("current_level") or 0)
    if str(settled.get("mode")) != "count" or level <= previous_level or level <= 0:
        return settled

    amount = round(float(session_engine._next_amount(settled, session_engine.MIN_AUTO_PAYOUT)), 2)
    await session_engine.update_auto_trade_control(amount=amount, max_open_positions=1)
    message = f"Догон {level}/{settled.get('max_martingale')} подготовлен · следующая ставка {amount:.2f}"
    await session_engine._update(int(settled["id"]), stage="MARTINGALE", last_message=message)
    await session_engine.update_trade_runtime(stage="MARTINGALE", amount=amount, message=message)
    await _ORIGINAL_EVENT(
        int(settled["id"]),
        "MARTINGALE_READY",
        message,
        {"level": level, "amount": amount, "series_loss": float(settled.get("current_series_loss") or 0)},
    )
    settled["stage"] = "MARTINGALE"
    settled["last_message"] = message
    return settled


session_engine._validate_config = _validate_config
session_engine._event = _event
session_engine._settle = _settle
signal_engine.scan_strategy_candidates = _scan_strategy_candidates
signal_engine.scan_best_candidates = _scan_best_candidates


async def session_tick() -> dict:
    session = await session_engine._active()
    token = None
    if session and str(session.get("mode")) == "count":
        selected = split_strategy_key(session.get("strategy"))
        token = _SELECTED_SCAN_STRATEGIES.set(_execution_strategies(selected))
    try:
        return await _ORIGINAL_SESSION_TICK()
    finally:
        if token is not None:
            _SELECTED_SCAN_STRATEGIES.reset(token)


async def preload_cycle() -> dict | None:
    session = await session_engine._active()
    token = None
    if session and str(session.get("mode")) == "count":
        selected = split_strategy_key(session.get("strategy"))
        token = _SELECTED_SCAN_STRATEGIES.set(_execution_strategies(selected))
    try:
        return await _base_preload_cycle()
    finally:
        if token is not None:
            _SELECTED_SCAN_STRATEGIES.reset(token)
