from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pocketoptionapi_async.models import OrderResult, OrderStatus

from backend.services.pocket_telemetry import TelemetryPocketOptionClient


class OrderUncertainError(RuntimeError):
    """The broker request was sent but acceptance could not be confirmed.

    The caller must block new entries and reconcile the request id before any
    retry. This prevents a lost acknowledgement from creating a duplicate deal.
    """

    def __init__(self, request_id: str, message: str = "Pocket order outcome is uncertain"):
        super().__init__(message)
        self.request_id = str(request_id)


class DirectDemoTradingClient:
    """Demo-only Pocket order client using AlphaPulse's direct Socket.IO handshake.

    It refuses non-demo operation, exposes read-only balance/payout telemetry, sends
    exactly one openOrder and requires `successopenOrder`. An uncertain order is
    never retried, which protects against duplicate broker positions.
    """

    def __init__(self, ssid: str):
        self._client = TelemetryPocketOptionClient(ssid, is_demo=True)
        self.last_open_payload: Any = None
        self.last_open_price: float | None = None

    @property
    def is_connected(self) -> bool:
        return bool(self._client.is_connected)

    async def connect(self, persistent: bool = False) -> bool:
        del persistent
        return await self._client.connect(persistent=False)

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def account_snapshot(self, listen_seconds: float = 1.2) -> dict:
        return await self._client.refresh_telemetry(listen_seconds=listen_seconds)

    @staticmethod
    def _error_text(payload: Any) -> str:
        if isinstance(payload, dict):
            for key in ("message", "error", "reason", "detail"):
                value = payload.get(key)
                if value:
                    return str(value)[:160]
        return "Pocket Option rejected demo order"

    @staticmethod
    def _nested_dicts(payload: Any) -> list[dict]:
        rows: list[dict] = []
        if isinstance(payload, dict):
            rows.append(payload)
            for key in ("deal", "data", "order"):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    rows.append(nested)
        elif isinstance(payload, list):
            rows.extend(row for row in payload if isinstance(row, dict))
        return rows

    @classmethod
    def _order_id(cls, payload: Any, fallback: str) -> str:
        for row in cls._nested_dicts(payload):
            for key in ("id", "uuid", "dealId", "deal_id", "orderId", "order_id", "ticket"):
                value = row.get(key)
                if value not in (None, ""):
                    return str(value)
        return fallback

    @classmethod
    def _open_price(cls, payload: Any) -> float | None:
        for row in cls._nested_dicts(payload):
            for key in ("openPrice", "open_price", "price", "entryPrice", "entry_price"):
                try:
                    value = float(row.get(key))
                    if value > 0:
                        return value
                except Exception:
                    continue
        return None

    @staticmethod
    def _request_id_from_deal(deal: dict) -> str | None:
        for key in (
            "requestId", "request_id", "clientRequestId", "client_request_id",
            "clientOrderId", "client_order_id", "externalId", "external_id",
        ):
            value = deal.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    @classmethod
    def _deal_to_order_result(cls, deal: dict, request_id: str, *, closed: bool) -> OrderResult:
        action = str(deal.get("action", deal.get("direction", "call"))).strip().lower()
        try:
            from pocketoptionapi_async import OrderDirection
            direction = OrderDirection.PUT if action in {"put", "sell", "down"} else OrderDirection.CALL
        except Exception:
            direction = action
        amount = float(deal.get("amount", deal.get("sum", 0)) or 0)
        duration = int(float(deal.get("time", deal.get("duration", 60)) or 60))
        placed_raw = deal.get("openTimestamp", deal.get("open_time", deal.get("timestamp")))
        try:
            placed_at = datetime.fromtimestamp(float(placed_raw), tz=timezone.utc) if placed_raw is not None else datetime.now(timezone.utc)
        except Exception:
            placed_at = datetime.now(timezone.utc)
        return OrderResult(
            order_id=cls._order_id(deal, request_id),
            asset=str(deal.get("asset", deal.get("symbol", ""))),
            amount=amount,
            direction=direction,
            duration=max(5, duration),
            status=OrderStatus.CLOSED if closed else OrderStatus.ACTIVE,
            placed_at=placed_at,
            expires_at=placed_at + timedelta(seconds=max(5, duration)),
            error_message=None,
        )

    async def place_order(
        self,
        *,
        asset: str,
        amount: float,
        direction,
        duration: int,
        idempotency_key: str | None = None,
    ) -> OrderResult:
        if not self._client.is_demo:
            raise RuntimeError("Direct trading client is demo-only")
        amount = float(amount)
        if amount <= 0:
            raise ValueError("Demo amount must be positive")
        if int(duration) < 5:
            raise ValueError("Demo duration must be at least 5 seconds")
        if not await self.connect(persistent=False):
            raise RuntimeError("Pocket Option demo connection failed")
        ws = self._client._ws
        if ws is None or ws.closed:
            raise RuntimeError("Pocket Option demo websocket is not open")

        balance = self._client.balance
        if balance is not None and amount > float(balance) + 1e-9:
            raise RuntimeError(
                f"InsufficientFunds: balance={float(balance):.2f}, required={amount:.2f}"
            )

        action = str(getattr(direction, "value", direction)).strip().lower()
        if action not in {"call", "put"}:
            raise ValueError("Direction must be call or put")

        request_id = str(idempotency_key or uuid.uuid4())[:128]
        order_payload = [
            "openOrder",
            {
                "asset": str(asset),
                "amount": amount,
                "action": action,
                "isDemo": 1,
                "requestId": request_id,
                "optionType": 100,
                "time": int(duration),
            },
        ]
        placed_at = datetime.now(timezone.utc)
        deadline = time.monotonic() + 12.0
        sent = False

        async with self._client._lock:
            try:
                await ws.send_str("42" + json.dumps(order_payload, separators=(",", ":")))
                sent = True
                while time.monotonic() < deadline:
                    try:
                        event, payload = await self._client._recv_packet(
                            max(0.2, min(2.0, deadline - time.monotonic()))
                        )
                    except asyncio.TimeoutError:
                        continue
                    if event == "unauthorized":
                        raise RuntimeError("Pocket Option rejected demo session")
                    if event == "failopenOrder":
                        raise RuntimeError(self._error_text(payload))
                    if event == "successopenOrder":
                        self.last_open_payload = payload
                        self.last_open_price = self._open_price(payload)
                        return OrderResult(
                            order_id=self._order_id(payload, request_id),
                            asset=str(asset),
                            amount=amount,
                            direction=direction,
                            duration=int(duration),
                            status=OrderStatus.ACTIVE,
                            placed_at=placed_at,
                            expires_at=placed_at + timedelta(seconds=int(duration)),
                            error_message=None,
                        )
            except asyncio.CancelledError:
                if sent:
                    raise OrderUncertainError(request_id, "Order task cancelled after broker send")
                raise
            except RuntimeError:
                raise
            except Exception as exc:
                if sent:
                    raise OrderUncertainError(request_id, f"Broker transport failed after send: {type(exc).__name__}") from exc
                raise

        raise OrderUncertainError(request_id, "Pocket did not confirm order after request was sent")

    async def find_order(self, idempotency_key: str) -> OrderResult | None:
        request_id = str(idempotency_key)
        if not await self.connect(persistent=False):
            return None
        opened = await self._client.get_opened_deals(listen_seconds=0.7)
        closed = await self._client.get_closed_deals(listen_seconds=0.35)
        for deal in opened:
            if not isinstance(deal, dict):
                continue
            if self._request_id_from_deal(deal) == request_id or self._order_id(deal, "") == request_id:
                return self._deal_to_order_result(deal, request_id, closed=False)
        for deal in closed:
            if not isinstance(deal, dict):
                continue
            if self._request_id_from_deal(deal) == request_id or self._order_id(deal, "") == request_id:
                return self._deal_to_order_result(deal, request_id, closed=True)
        return None
