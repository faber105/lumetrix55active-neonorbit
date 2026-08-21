from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Iterable

from backend.services import auto_trade, positions as positions_service, preload_next, session_engine
from backend.services.pocket_demo_trading import DirectDemoTradingClient
from backend.services.pocketoption_otc import market_data
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

# Low-latency PROFIT/preload continuation. The ordinary reconciliation path used
# to make one relatively long closed-deals probe and then wait for the next
# serverless tick. At a 5m boundary that could turn a prepared next entry into a
# 4-8 second late close. Keep the normal engine intact, but make its broker probe
# short and let the preload path burst-poll until Pocket publishes the result.
_ORIGINAL_CLOSED_BROKER_DEALS = positions_service._closed_broker_deals
_ORIGINAL_PRELOAD_EXECUTE = preload_next.execute_confirmed_signal
_ORIGINAL_PRELOAD_CONSUME = preload_next._consume_when_closed
_ORIGINAL_BUILD_TRADING_CLIENT = auto_trade._build_trading_client
_PRELOAD_READY_CLIENT: DirectDemoTradingClient | None = None


async def _fast_closed_broker_deals() -> dict[str, dict]:
    try:
        await market_data._refresh_private_ssid()
        if not market_data.configured:
            return {}
        client = DirectDemoTradingClient(market_data.ssid)
        try:
            if not await client.connect(persistent=False):
                return {}
            deals = await client._client.get_closed_deals(listen_seconds=0.18)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
        return {
            positions_service._deal_id(row): row
            for row in deals or []
            if isinstance(row, dict) and positions_service._deal_id(row)
        }
    except Exception:
        # A transient fast-path failure must not break settlement. The normal
        # implementation remains the final fallback.
        return await _ORIGINAL_CLOSED_BROKER_DEALS()


positions_service._closed_broker_deals = _fast_closed_broker_deals


async def _preload_reconcile_burst(limit: int = 100) -> dict:
    """Poll one Pocket socket several times per second at a prepared 5m boundary."""
    global _PRELOAD_READY_CLIENT
    deadline = asyncio.get_running_loop().time() + 4.6
    last: dict = {"closed": 0, "checked": 0, "errors": []}
    client: DirectDemoTradingClient | None = None
    previous_closed_reader = positions_service._closed_broker_deals

    try:
        await market_data._refresh_private_ssid()
        if market_data.configured:
            client = DirectDemoTradingClient(market_data.ssid)
            if not await client.connect(persistent=False):
                client = None
    except Exception:
        client = None

    if client is None:
        # Keep the fast reconnecting fallback if one persistent connection cannot
        # be established on this invocation.
        while True:
            last = await positions_service.reconcile_positions(limit=limit)
            if int(last.get("closed") or 0) > 0 or int(last.get("checked") or 0) <= 0:
                return last
            if asyncio.get_running_loop().time() >= deadline:
                return last
            await asyncio.sleep(0.06)

    async def _same_socket_closed_deals() -> dict[str, dict]:
        try:
            deals = await client._client.get_closed_deals(listen_seconds=0.08)
        except Exception:
            return {}
        return {
            positions_service._deal_id(row): row
            for row in deals or []
            if isinstance(row, dict) and positions_service._deal_id(row)
        }

    positions_service._closed_broker_deals = _same_socket_closed_deals
    keep_for_entry = False
    try:
        while True:
            last = await positions_service.reconcile_positions(limit=limit)
            if int(last.get("closed") or 0) > 0:
                # The same authenticated socket is immediately reused to place the
                # pre-analyzed order, removing another full Pocket handshake.
                _PRELOAD_READY_CLIENT = client
                keep_for_entry = True
                return last
            if int(last.get("checked") or 0) <= 0:
                return last
            if asyncio.get_running_loop().time() >= deadline:
                return last
            await asyncio.sleep(0.03)
    finally:
        positions_service._closed_broker_deals = previous_closed_reader
        if not keep_for_entry:
            try:
                await client.disconnect()
            except Exception:
                pass


preload_next.reconcile_positions = _preload_reconcile_burst


def _fast_preload_client():
    global _PRELOAD_READY_CLIENT
    client = _PRELOAD_READY_CLIENT
    _PRELOAD_READY_CLIENT = None
    if client is None or not client.is_connected:
        client = _ORIGINAL_BUILD_TRADING_CLIENT()

    original_snapshot = client.account_snapshot

    async def fast_snapshot(listen_seconds: float = 1.2):
        # When the socket came directly from the settlement burst, updateAssets was
        # captured during the same boundary. Reuse that fresh payout snapshot
        # immediately; otherwise perform the short normal telemetry read.
        try:
            cached = client._client.snapshot()
            if cached.get("payouts"):
                return cached
        except Exception:
            pass
        window = 0.18 if 0.75 <= float(listen_seconds) <= 0.85 else float(listen_seconds)
        return await original_snapshot(listen_seconds=window)

    client.account_snapshot = fast_snapshot
    return client


async def _fast_execute_confirmed_signal(signal: dict) -> dict:
    previous_builder = auto_trade._build_trading_client
    auto_trade._build_trading_client = _fast_preload_client
    try:
        return await _ORIGINAL_PRELOAD_EXECUTE(signal)
    finally:
        auto_trade._build_trading_client = previous_builder


preload_next.execute_confirmed_signal = _fast_execute_confirmed_signal


async def _dispose_unused_ready_client() -> None:
    global _PRELOAD_READY_CLIENT
    client = _PRELOAD_READY_CLIENT
    _PRELOAD_READY_CLIENT = None
    if client is not None:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _resilient_preload_consume(session: dict, candidate: dict) -> dict | None:
    """Do not discard a prepared setup on a one-tick Pocket race after settlement."""
    result = await _ORIGINAL_PRELOAD_CONSUME(session, candidate)
    # If the original path never reached order execution (target completed, hard
    # payout rejection, etc.), release the connection retained by the burst.
    if _PRELOAD_READY_CLIENT is not None:
        await _dispose_unused_ready_client()

    if not isinstance(result, dict):
        return result

    status = str(result.get("status") or "")
    trade = result.get("trade") if isinstance(result.get("trade"), dict) else {}
    trade_status = str(trade.get("status") or status)
    reason = str(trade.get("reason") or "")
    payout_missing = trade_status == "PAYOUT_TOO_LOW" and trade.get("payout") is None
    retryable = trade_status in {"FAILED", "DUPLICATE"} or payout_missing or (
        trade_status == "SKIPPED" and reason == "max_open_positions"
    )
    if not retryable or not candidate.get("entry_time") or not candidate.get("signal_id"):
        return result

    try:
        entry = preload_next._to_naive_utc(candidate.get("entry_time"))
        lateness = max(0.0, (preload_next.utcnow() - entry).total_seconds())
    except Exception:
        return result

    if lateness > 8.0:
        return result

    await preload_next._save_candidate(int(session["id"]), status="WAIT_CLOSE")
    await session_engine._event(
        int(session["id"]),
        "PRELOAD_RETRY",
        "Pocket завершает прошлую сделку или обновляет payout · заранее найденный вход сохранён и повторяется без нового 5m ожидания",
        {
            "signal_id": int(candidate["signal_id"]),
            "status": trade_status,
            "reason": reason or None,
            "payout": trade.get("payout"),
            "lateness": round(lateness, 2),
        },
    )
    return {"status": "WAIT_CLOSE", "block": True, "retry": True, "trade": trade}


preload_next._consume_when_closed = _resilient_preload_consume


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
        result = await _base_preload_cycle()
        if isinstance(result, dict):
            raw_status = str(result.get("status") or "")
            if raw_status == "PRELOAD_WAIT_CLOSE":
                result = {**result, "raw_status": raw_status, "status": "WAIT_CLOSE"}
            elif raw_status == "PRELOAD_ACTIVE":
                preparation = result.get("preparation") if isinstance(result.get("preparation"), dict) else {}
                prepared_status = str(preparation.get("status") or "")
                if prepared_status in {"PREPARED", "WAIT_CLOSE"}:
                    result = {**result, "raw_status": raw_status, "status": prepared_status}
        return result
    finally:
        if token is not None:
            _SELECTED_SCAN_STRATEGIES.reset(token)
