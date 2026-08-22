from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pocketoptionapi_async.models import OrderResult, OrderStatus

from backend.services.pocket_telemetry import TelemetryPocketOptionClient


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

        # The AUTO execution path refreshes account telemetry on this same
        # client immediately before place_order. If Pocket has already told us
        # the current DEMO balance, fail closed before sending openOrder.
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

        async with self._client._lock:
            await ws.send_str("42" + json.dumps(order_payload, separators=(",", ":")))
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

        raise RuntimeError("Pocket Option did not confirm demo order")

    async def find_order(self, idempotency_key: str) -> OrderResult | None:
        # The current unofficial transport has no reliable order-by-client-id
        # endpoint. Returning None is explicit: UNKNOWN remains blocked for
        # operator/broker reconciliation and is never resent automatically.
        del idempotency_key
        return None
