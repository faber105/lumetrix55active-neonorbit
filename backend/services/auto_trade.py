from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError

from backend.models.db_models import (
    AsyncSessionLocal,
    AutoTradeControl,
    PaperPosition,
    SignalDirection,
    SignalResult,
    TradeExecution,
    utcnow,
)
from backend.services.control import admin_id
from backend.services.pocketoption_otc import MarketDataUnavailable, _parse_wire_auth, market_data
from backend.services.trade_mode import get_execution_mode, get_trade_account_mode

logger = logging.getLogger("alphapulse.auto_trade")
_trade_lock = asyncio.Lock()
MIN_TRADE_AMOUNT = 1.0
MAX_TRADE_AMOUNT = 50000.0


def _to_utc_naive(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(tzinfo=None)


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
            row = AutoTradeControl(
                telegram_id=tid,
                enabled=False,
                regular_enabled=True,
                vip_enabled=True,
                amount=1.0,
                max_open_positions=1,
            )
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
        if "enabled" in changes and changes["enabled"] is not None:
            row.enabled = bool(changes["enabled"])
        if "regular_enabled" in changes and changes["regular_enabled"] is not None:
            row.regular_enabled = bool(changes["regular_enabled"])
        if "vip_enabled" in changes and changes["vip_enabled"] is not None:
            row.vip_enabled = bool(changes["vip_enabled"])
        if "amount" in changes and changes["amount"] is not None:
            amount = float(changes["amount"])
            if amount < MIN_TRADE_AMOUNT or amount > MAX_TRADE_AMOUNT:
                raise ValueError(f"Trade amount must be between {MIN_TRADE_AMOUNT:g} and {MAX_TRADE_AMOUNT:g}")
            row.amount = round(amount, 2)
        if "max_open_positions" in changes and changes["max_open_positions"] is not None:
            row.max_open_positions = max(1, min(10, int(changes["max_open_positions"])))
        await db.commit()
        await db.refresh(row)
        return row


def serialize_auto_trade(control: AutoTradeControl | None) -> dict:
    if control is None:
        return {
            "auto_trade_enabled": False,
            "auto_trade_regular": True,
            "auto_trade_vip": True,
            "trade_amount": 1.0,
            "max_open_positions": 1,
        }
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
        row = (
            await db.execute(
                select(TradeExecution)
                .where(TradeExecution.telegram_id == tid)
                .order_by(desc(TradeExecution.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        position = await db.get(PaperPosition, row.position_id) if row and row.position_id else None
    if row is None:
        return None
    display_status = row.status
    if position is not None and position.status == "CLOSED":
        display_status = position.result.value
    return {
        "signal_id": row.signal_id,
        "position_id": row.position_id,
        "broker_order_id": row.broker_order_id,
        "amount": row.amount,
        "status": display_status,
        "error": row.error,
        "created_at": row.created_at.isoformat() + "Z",
        "updated_at": row.updated_at.isoformat() + "Z",
    }


def _build_trading_client():
    """Create the upstream order-capable client without replacing read-only market data."""
    from backend.services import pocketoption_compat  # noqa: F401
    from pocketoptionapi_async import AsyncPocketOptionClient

    payload = _parse_wire_auth(market_data.ssid)
    is_demo = trading_is_demo()
    if payload and payload.get("sessionToken") and not payload.get("session"):
        token = str(payload.get("sessionToken") or "")
        try:
            uid = int(payload.get("uid") or 0)
        except Exception:
            uid = 0
        if len(token) < 10 or uid <= 0:
            raise MarketDataUnavailable("Pocket Option auth packet is incomplete")
        client = AsyncPocketOptionClient(
            ssid=token,
            is_demo=is_demo,
            uid=uid,
            platform=1,
            persistent_connection=False,
            auto_reconnect=False,
            enable_logging=False,
        )
        exact_wire_frame = market_data.ssid
        client._format_session_message = lambda: exact_wire_frame
        market_data._patch_socketio_event_parser(client)
        return client

    client = AsyncPocketOptionClient(
        ssid=market_data.ssid,
        is_demo=is_demo,
        persistent_connection=False,
        auto_reconnect=False,
        enable_logging=False,
    )
    if payload and payload.get("session"):
        exact_wire_frame = market_data.ssid
        client._format_session_message = lambda: exact_wire_frame
    market_data._patch_socketio_event_parser(client)
    return client


async def _claim(signal: dict, amount: float) -> TradeExecution | None:
    tid = admin_id()
    if tid <= 0:
        return None
    row = TradeExecution(
        telegram_id=tid,
        signal_id=int(signal["id"]),
        amount=float(amount),
        status="EXECUTING",
    )
    async with AsyncSessionLocal() as db:
        db.add(row)
        try:
            await db.commit()
            await db.refresh(row)
            return row
        except IntegrityError:
            await db.rollback()
            return None


async def _mark_failed(execution_id: int, error: str) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(TradeExecution, execution_id)
        if row is not None:
            row.status = "FAILED"
            row.error = str(error)[:128]
            await db.commit()


async def maybe_execute_signal(signal: dict) -> dict:
    """Execute a freshly published signal only when AUTO mode is enabled."""
    if await get_execution_mode() != "auto":
        return {"status": "CONFIRMATION_REQUIRED", "execution_mode": "confirm"}
    return await _execute_signal(signal, confirmed=False)


async def execute_confirmed_signal(signal: dict) -> dict:
    """Execute after an explicit admin confirmation.

    This still goes through account, idempotency, amount and position-limit checks.
    Real-account orders are intentionally never sent by this backend.
    """
    return await _execute_signal(signal, confirmed=True)


async def _execute_signal(signal: dict, *, confirmed: bool) -> dict:
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
        return {
            "status": "REAL_CONFIRMATION_REQUIRED",
            "account": "real",
            "reason": "open_manually_in_pocket",
        }
    if connected_account != "demo":
        return {
            "status": "ACCOUNT_MISMATCH",
            "account": connected_account,
            "selected_account": selected_account,
        }
    if not confirmed and await get_execution_mode() != "auto":
        return {"status": "CONFIRMATION_REQUIRED", "execution_mode": "confirm"}

    now = utcnow()
    expiry = _to_utc_naive(signal["expiry_time"])
    duration = int((expiry - now).total_seconds())
    if duration < 5:
        return {"status": "SKIPPED", "reason": "expired"}

    async with AsyncSessionLocal() as db:
        open_count = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(PaperPosition)
                    .where(
                        PaperPosition.telegram_id == tid,
                        PaperPosition.source == "auto",
                        PaperPosition.status == "OPEN",
                    )
                )
            ).scalar_one()
            or 0
        )
    if open_count >= int(control.max_open_positions or 1):
        return {"status": "SKIPPED", "reason": "max_open_positions"}

    amount = float(control.amount or 1.0)
    execution = await _claim(signal, amount)
    if execution is None:
        return {"status": "DUPLICATE"}

    async with _trade_lock:
        client = None
        try:
            await market_data._refresh_private_ssid()
            if not market_data.configured:
                raise MarketDataUnavailable("Pocket Option session is not configured")

            from pocketoptionapi_async import OrderDirection, OrderStatus

            entry_price = float(
                signal.get("entry_price")
                or signal.get("analysis_price")
                or await market_data.latest_price(signal["asset"])
            )
            client = _build_trading_client()
            connected = await asyncio.wait_for(client.connect(persistent=False), timeout=35)
            if not connected:
                raise RuntimeError("Pocket Option trading connection failed")

            direction = OrderDirection.CALL if signal["direction"] == "BUY" else OrderDirection.PUT
            result = await asyncio.wait_for(
                client.place_order(
                    asset=signal["asset"],
                    amount=amount,
                    direction=direction,
                    duration=duration,
                ),
                timeout=35,
            )
            status_value = getattr(result.status, "value", str(result.status)).lower()
            if result.status == OrderStatus.CANCELLED or result.error_message:
                raise RuntimeError(result.error_message or "Pocket Option cancelled the order")

            placed_at = _to_utc_naive(result.placed_at)
            expires_at = _to_utc_naive(result.expires_at)
            async with AsyncSessionLocal() as db:
                position = PaperPosition(
                    telegram_id=tid,
                    signal_id=int(signal["id"]),
                    source="auto",
                    pair=signal["pair"],
                    asset=signal["asset"],
                    timeframe=signal["timeframe"],
                    strategy=signal["strategy"],
                    direction=SignalDirection(signal["direction"]),
                    status="OPEN",
                    entry_price=entry_price,
                    entry_time=placed_at,
                    expiry_time=expires_at,
                    result=SignalResult.PENDING,
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

            logger.warning(
                "Admin demo trade opened signal=%s asset=%s confirmed=%s",
                signal["id"],
                signal["asset"],
                confirmed,
            )
            return {
                "status": "OPEN",
                "position_id": position.id,
                "amount": amount,
                "account": "demo",
                "confirmed": confirmed,
            }
        except Exception as exc:
            logger.exception("Auto trade failed for signal %s: %s", signal.get("id"), type(exc).__name__)
            await _mark_failed(execution.id, type(exc).__name__)
            return {"status": "FAILED", "error": type(exc).__name__}
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
