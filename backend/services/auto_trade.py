from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError

from backend.models.db_models import (
    AsyncSessionLocal, AutoTradeControl, PaperPosition, Signal, SignalDirection,
    SignalResult, TradeExecution, utcnow,
)
from backend.services.control import admin_id
from backend.services.pocket_demo_trading import DirectDemoTradingClient
from backend.services.pocketoption_otc import MarketDataUnavailable, _parse_wire_auth, market_data
from backend.services.trade_mode import get_execution_mode, get_trade_account_mode
from backend.services.trade_runtime import get_trade_runtime, reset_trade_runtime, update_trade_runtime

logger = logging.getLogger("alphapulse.auto_trade")
_trade_lock = asyncio.Lock()
MIN_TRADE_AMOUNT = 1.0
MAX_TRADE_AMOUNT = 50000.0
MIN_AUTO_PAYOUT = 92.0
# GitHub scanner runs every few seconds. Enter the blocking exact-entry path only
# when the candle boundary is close enough to keep a Vercel request short.
AUTO_DUE_WINDOW_SECONDS = max(5, min(10, int(os.getenv("AUTO_DUE_WINDOW_SECONDS", "7"))))
ENTRY_GRACE_SECONDS = max(0.5, min(3.0, float(os.getenv("AUTO_ENTRY_GRACE_SECONDS", "1.5"))))
_snapshot_cache: dict = {"at": 0.0, "data": None}


def _to_utc_naive(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(tzinfo=None)


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def trading_is_demo() -> bool:
    payload = _parse_wire_auth(market_data.ssid)
    if payload:
        if "isDemo" in payload:
            try:
                return bool(int(payload.get("isDemo") or 0))
            except Exception:
                pass
        current_url = str(payload.get("currentUrl") or "").strip().lower()
        if current_url:
            return "demo" in current_url
    return bool(market_data.demo)


async def get_auto_trade_control() -> AutoTradeControl | None:
    tid = admin_id()
    if tid <= 0:
        return None
    async with AsyncSessionLocal() as db:
        row = await db.get(AutoTradeControl, tid)
        if row is None:
            row = AutoTradeControl(telegram_id=tid, enabled=False, regular_enabled=True, vip_enabled=True, amount=1.0, max_open_positions=1)
            db.add(row)
            await db.commit()
            await db.refresh(row)
        return row


async def update_auto_trade_control(**changes) -> AutoTradeControl:
    tid = admin_id()
    if tid <= 0:
        raise RuntimeError("ADMIN_ID is not configured")
    async with AsyncSessionLocal() as db:
        row = await db.get(AutoTradeControl, tid)
        if row is None:
            row = AutoTradeControl(telegram_id=tid)
            db.add(row)
            await db.flush()
        if changes.get("enabled") is not None:
            row.enabled = bool(changes["enabled"])
        if changes.get("regular_enabled") is not None:
            row.regular_enabled = bool(changes["regular_enabled"])
        if changes.get("vip_enabled") is not None:
            row.vip_enabled = bool(changes["vip_enabled"])
        if changes.get("amount") is not None:
            amount = float(changes["amount"])
            if amount < MIN_TRADE_AMOUNT or amount > MAX_TRADE_AMOUNT:
                raise ValueError(f"Trade amount must be between {MIN_TRADE_AMOUNT:g} and {MAX_TRADE_AMOUNT:g}")
            row.amount = round(amount, 2)
        if changes.get("max_open_positions") is not None:
            row.max_open_positions = max(1, min(10, int(changes["max_open_positions"])))
        await db.commit()
        await db.refresh(row)
        return row


def serialize_auto_trade(control: AutoTradeControl | None) -> dict:
    if control is None:
        return {"auto_trade_enabled": False, "auto_trade_regular": True, "auto_trade_vip": True, "trade_amount": 1.0, "max_open_positions": 1}
    return {
        "auto_trade_enabled": bool(control.enabled),
        "auto_trade_regular": bool(control.regular_enabled),
        "auto_trade_vip": bool(control.vip_enabled),
        "trade_amount": float(control.amount or 1.0),
        "max_open_positions": int(control.max_open_positions or 1),
    }


async def latest_execution() -> dict | None:
    tid = admin_id()
    if tid <= 0:
        return None
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(TradeExecution).where(TradeExecution.telegram_id == tid).order_by(desc(TradeExecution.created_at)).limit(1))).scalar_one_or_none()
        position = await db.get(PaperPosition, row.position_id) if row and row.position_id else None
    if row is None:
        return None
    display_status = position.result.value if position is not None and position.status == "CLOSED" else row.status
    return {
        "signal_id": row.signal_id, "position_id": row.position_id, "broker_order_id": row.broker_order_id,
        "amount": row.amount, "status": display_status, "error": row.error,
        "created_at": row.created_at.isoformat() + "Z", "updated_at": row.updated_at.isoformat() + "Z",
    }


def _build_trading_client():
    return DirectDemoTradingClient(market_data.ssid)


async def get_demo_account_snapshot(*, force: bool = False, max_age: float = 12.0) -> dict:
    now_mono = time.monotonic()
    cached = _snapshot_cache.get("data")
    if not force and cached and now_mono - float(_snapshot_cache.get("at") or 0) <= max_age:
        return dict(cached)
    await market_data._refresh_private_ssid()
    if not market_data.configured or not trading_is_demo():
        return {"balance": None, "balance_is_demo": None, "payouts": {}, "available_assets": {}}
    client = DirectDemoTradingClient(market_data.ssid)
    try:
        await asyncio.wait_for(client.connect(persistent=False), timeout=20)
        snapshot = await asyncio.wait_for(client.account_snapshot(listen_seconds=1.7), timeout=4)
        _snapshot_cache["at"] = time.monotonic()
        _snapshot_cache["data"] = dict(snapshot)
        return snapshot
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def payout_for_asset(snapshot: dict, asset: str) -> float | None:
    try:
        value = (snapshot.get("payouts", {}) or {}).get(asset)
        return float(value) if value is not None else None
    except Exception:
        return None


async def eligible_auto_assets(assets: list[str], *, force: bool = False) -> tuple[list[str], dict]:
    snapshot = await get_demo_account_snapshot(force=force)
    payouts = snapshot.get("payouts", {}) or {}
    available = snapshot.get("available_assets", {}) or {}
    eligible: list[str] = []
    for asset in assets:
        try:
            payout = float(payouts.get(asset))
        except (TypeError, ValueError):
            continue
        if payout >= MIN_AUTO_PAYOUT and available.get(asset, True) is not False:
            eligible.append(asset)
    return eligible, snapshot


async def _claim(signal: dict, amount: float) -> TradeExecution | None:
    tid = admin_id()
    if tid <= 0:
        return None
    row = TradeExecution(telegram_id=tid, signal_id=int(signal["id"]), amount=float(amount), status="EXECUTING")
    async with AsyncSessionLocal() as db:
        db.add(row)
        try:
            await db.commit()
            await db.refresh(row)
            return row
        except IntegrityError:
            await db.rollback()
            return None


async def _mark_execution(execution_id: int, status: str, error: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(TradeExecution, execution_id)
        if row is not None:
            row.status = str(status)[:20]
            row.error = str(error)[:128] if error else None
            await db.commit()


async def _basic_guard(signal: dict, *, confirmed: bool) -> dict | None:
    tid = admin_id()
    control = await get_auto_trade_control()
    if tid <= 0 or control is None or not control.enabled:
        return {"status": "DISABLED"}
    is_vip = bool(signal.get("is_vip"))
    if is_vip and not control.vip_enabled:
        return {"status": "SKIPPED", "reason": "vip_disabled"}
    if not is_vip and not control.regular_enabled:
        return {"status": "SKIPPED", "reason": "regular_disabled"}
    selected_account = await get_trade_account_mode()
    connected_account = "demo" if trading_is_demo() else "real"
    if selected_account == "real":
        return {"status": "REAL_CONFIRMATION_REQUIRED", "account": "real", "reason": "open_manually_in_pocket"}
    if connected_account != "demo":
        return {"status": "ACCOUNT_MISMATCH", "account": connected_account, "selected_account": selected_account}
    if not confirmed and await get_execution_mode() != "auto":
        return {"status": "CONFIRMATION_REQUIRED", "execution_mode": "confirm"}
    return None


async def maybe_execute_signal(signal: dict) -> dict:
    guard = await _basic_guard(signal, confirmed=False)
    if guard:
        return guard
    entry = _to_utc_naive(signal["entry_time"])
    expiry = _to_utc_naive(signal["expiry_time"])
    now = utcnow()
    if expiry <= now:
        return {"status": "SKIPPED", "reason": "expired"}

    eligible, snapshot = await eligible_auto_assets([signal["asset"]], force=True)
    payout = payout_for_asset(snapshot, signal["asset"])
    if signal["asset"] not in eligible:
        await update_trade_runtime(
            stage="PAYOUT_TOO_LOW", pending_signal_id=None, pair=signal.get("pair"), asset=signal.get("asset"),
            strategy=signal.get("strategy"), timeframe=signal.get("timeframe"), payout_percent=payout,
            balance=snapshot.get("balance"), balance_is_demo=snapshot.get("balance_is_demo"),
            entry_time=_iso(entry), expiry_time=_iso(expiry),
            message=f"Выплата ниже {MIN_AUTO_PAYOUT:g}% или недоступна — продолжаю поиск",
        )
        return {"status": "PAYOUT_TOO_LOW", "payout": payout, "min_payout": MIN_AUTO_PAYOUT}

    seconds = (entry - now).total_seconds()
    await update_trade_runtime(
        stage="WAIT_ENTRY" if seconds > 0 else "OPENING", pending_signal_id=int(signal["id"]),
        pair=signal.get("pair"), asset=signal.get("asset"), strategy=signal.get("strategy"), timeframe=signal.get("timeframe"),
        payout_percent=payout, balance=snapshot.get("balance"), balance_is_demo=snapshot.get("balance_is_demo"),
        entry_time=_iso(entry), expiry_time=_iso(expiry), seconds_to_entry=max(0, round(seconds, 1)),
        message="Сигнал найден — жду открытие новой свечи",
    )
    if seconds > AUTO_DUE_WINDOW_SECONDS:
        return {"status": "SCHEDULED", "entry_time": _iso(entry), "seconds_to_entry": round(seconds, 1), "payout": payout}
    return await _execute_signal(signal, confirmed=False, exact_entry=True)


async def execute_confirmed_signal(signal: dict) -> dict:
    return await _execute_signal(signal, confirmed=True, exact_entry=False)


async def process_pending_auto_trade() -> dict:
    runtime = await get_trade_runtime()
    signal_id = runtime.get("pending_signal_id")
    if not signal_id:
        return {"status": "IDLE"}
    control = await get_auto_trade_control()
    if control is None or not control.enabled or await get_execution_mode() != "auto":
        await reset_trade_runtime("IDLE", "Автоторговля выключена")
        return {"status": "DISABLED"}
    async with AsyncSessionLocal() as db:
        row = await db.get(Signal, int(signal_id))
        if row is None:
            await reset_trade_runtime("FAILED", "Отложенный сигнал не найден")
            return {"status": "FAILED", "reason": "signal_not_found"}
        from backend.routers.signals import out
        signal = out(row)
    entry = _to_utc_naive(signal["entry_time"])
    seconds = (entry - utcnow()).total_seconds()
    if seconds > AUTO_DUE_WINDOW_SECONDS:
        await update_trade_runtime(stage="WAIT_ENTRY", seconds_to_entry=round(seconds, 1), message="Жду открытие новой свечи")
        return {"status": "WAIT_ENTRY", "seconds_to_entry": round(seconds, 1), "signal_id": int(signal_id)}
    if seconds < -ENTRY_GRACE_SECONDS:
        await reset_trade_runtime("MISSED_ENTRY", "Точное время новой свечи уже прошло — сигнал пропущен")
        return {"status": "MISSED_ENTRY", "signal_id": int(signal_id)}
    return await _execute_signal(signal, confirmed=False, exact_entry=True)


async def _execute_signal(signal: dict, *, confirmed: bool, exact_entry: bool) -> dict:
    guard = await _basic_guard(signal, confirmed=confirmed)
    if guard:
        return guard
    tid = admin_id()
    control = await get_auto_trade_control()
    assert control is not None
    entry = _to_utc_naive(signal["entry_time"])
    expiry = _to_utc_naive(signal["expiry_time"])
    if expiry <= utcnow():
        await reset_trade_runtime("MISSED_ENTRY", "Сигнал истёк до открытия")
        return {"status": "SKIPPED", "reason": "expired"}

    async with AsyncSessionLocal() as db:
        open_count = int((await db.execute(select(func.count()).select_from(PaperPosition).where(
            PaperPosition.telegram_id == tid, PaperPosition.source == "auto", PaperPosition.status == "OPEN",
        ))).scalar_one() or 0)
    if open_count >= int(control.max_open_positions or 1):
        return {"status": "SKIPPED", "reason": "max_open_positions"}

    amount = float(control.amount or 1.0)
    execution = None

    async with _trade_lock:
        client = None
        try:
            await market_data._refresh_private_ssid()
            if not market_data.configured:
                raise MarketDataUnavailable("Pocket Option session is not configured")
            from pocketoptionapi_async import OrderDirection, OrderStatus

            if exact_entry:
                seconds = (entry - utcnow()).total_seconds()
                if seconds > AUTO_DUE_WINDOW_SECONDS:
                    await update_trade_runtime(stage="WAIT_ENTRY", seconds_to_entry=round(seconds, 1), message="Жду открытие новой свечи")
                    return {"status": "WAIT_ENTRY", "seconds_to_entry": round(seconds, 1)}
                if seconds < -ENTRY_GRACE_SECONDS:
                    await reset_trade_runtime("MISSED_ENTRY", "Точное время входа пропущено")
                    return {"status": "MISSED_ENTRY"}

            client = _build_trading_client()
            if not await asyncio.wait_for(client.connect(persistent=False), timeout=20):
                raise RuntimeError("Pocket Option demo trading connection failed")
            snapshot = await asyncio.wait_for(client.account_snapshot(listen_seconds=(0.20 if confirmed and not exact_entry else 0.8)), timeout=4)
            payout = payout_for_asset(snapshot, signal["asset"])
            if payout is None or payout < MIN_AUTO_PAYOUT:
                await reset_trade_runtime("PAYOUT_TOO_LOW", f"Выплата {payout if payout is not None else '—'}% < {MIN_AUTO_PAYOUT:g}% — сигнал пропущен")
                return {"status": "PAYOUT_TOO_LOW", "payout": payout, "min_payout": MIN_AUTO_PAYOUT}

            if exact_entry:
                remaining = (entry - utcnow()).total_seconds()
                if remaining > 0.35:
                    await update_trade_runtime(
                        stage="WAIT_ENTRY", payout_percent=payout, balance=snapshot.get("balance"),
                        seconds_to_entry=round(remaining, 1), message="Pocket подключён · жду новую свечу",
                    )
                    await asyncio.sleep(max(0.0, remaining - 0.30))
                remaining = (entry - utcnow()).total_seconds()
                if remaining < -ENTRY_GRACE_SECONDS:
                    await reset_trade_runtime("MISSED_ENTRY", "Pocket не успел к открытию новой свечи")
                    return {"status": "MISSED_ENTRY"}
                await update_trade_runtime(
                    stage="OPENING", payout_percent=payout, balance=snapshot.get("balance"),
                    seconds_to_entry=max(0, round(remaining, 2)), message="Pocket готов · открываю на новой свече",
                )
                if remaining > 0:
                    await asyncio.sleep(remaining)

                # Never let a stopped session or switched execution mode open a
                # trade after a wait. This also closes the stale scanner race.
                guard = await _basic_guard(signal, confirmed=False)
                if guard:
                    return guard

            # Claim only at the actual send point. Previously an invocation could
            # claim EXECUTING, die while sleeping, and permanently block retries.
            execution = await _claim(signal, amount)
            if execution is None:
                return {"status": "DUPLICATE"}

            duration = int((expiry - (entry if exact_entry else utcnow())).total_seconds())
            if duration < 5:
                await _mark_execution(execution.id, "SKIPPED", "expired")
                return {"status": "SKIPPED", "reason": "expired"}
            if not exact_entry:
                await update_trade_runtime(stage="OPENING", message="Отправляю подтверждённый DEMO ордер")

            direction = OrderDirection.CALL if signal["direction"] == "BUY" else OrderDirection.PUT
            result = await asyncio.wait_for(client.place_order(asset=signal["asset"], amount=amount, direction=direction, duration=duration), timeout=20)
            status_value = getattr(result.status, "value", str(result.status)).lower()
            if result.status == OrderStatus.CANCELLED or result.error_message:
                raise RuntimeError(result.error_message or "Pocket Option cancelled the order")

            placed_at = _to_utc_naive(result.placed_at)
            broker_price = getattr(client, "last_open_price", None)
            if not broker_price:
                try:
                    broker_price = await market_data.latest_price(signal["asset"])
                except Exception:
                    broker_price = signal.get("analysis_price") or signal.get("entry_price")
            entry_price = float(broker_price)
            position_expiry = expiry if exact_entry else _to_utc_naive(result.expires_at)

            async with AsyncSessionLocal() as db:
                position = PaperPosition(
                    telegram_id=tid, signal_id=int(signal["id"]), source="auto", pair=signal["pair"], asset=signal["asset"],
                    timeframe=signal["timeframe"], strategy=signal["strategy"], direction=SignalDirection(signal["direction"]),
                    status="OPEN", entry_price=entry_price, entry_time=placed_at, expiry_time=position_expiry, result=SignalResult.PENDING,
                )
                db.add(position)
                await db.flush()
                row = await db.get(TradeExecution, execution.id)
                if row is not None:
                    row.position_id = position.id
                    row.broker_order_id = str(result.order_id)
                    row.status = "OPEN" if status_value in {"pending", "active", "closed", "win", "lose"} else status_value.upper()[:20]
                    row.error = None
                await db.commit()
                await db.refresh(position)

            try:
                after = await client.account_snapshot(listen_seconds=0.55)
                balance = after.get("balance") if after.get("balance") is not None else snapshot.get("balance")
            except Exception:
                balance = snapshot.get("balance")
            await update_trade_runtime(
                stage="OPEN", pending_signal_id=None, position_id=position.id, broker_order_id=str(result.order_id),
                pair=signal["pair"], asset=signal["asset"], strategy=signal["strategy"], timeframe=signal["timeframe"],
                payout_percent=payout, balance=balance, balance_is_demo=True, entry_time=_iso(placed_at), expiry_time=_iso(position_expiry),
                seconds_to_entry=0, amount=amount, message="DEMO сделка открыта · Live отслеживание активно",
            )
            logger.warning("Admin demo trade opened signal=%s asset=%s exact_entry=%s", signal["id"], signal["asset"], exact_entry)
            return {
                "status": "OPEN", "position_id": position.id, "amount": amount, "account": "demo", "confirmed": confirmed,
                "payout": payout, "balance": balance, "entry_time": _iso(placed_at), "scheduled_entry_time": _iso(entry),
                "entry_delay_ms": round((placed_at - entry).total_seconds() * 1000) if exact_entry else None,
            }
        except Exception as exc:
            logger.exception("Auto trade failed for signal %s: %s", signal.get("id"), type(exc).__name__)
            if execution is not None:
                await _mark_execution(execution.id, "FAILED", type(exc).__name__)
            await update_trade_runtime(stage="FAILED", pending_signal_id=None, message=f"Ошибка открытия: {type(exc).__name__}")
            return {"status": "FAILED", "error": type(exc).__name__}
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
