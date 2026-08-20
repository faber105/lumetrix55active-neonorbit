from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from pocketoptionapi_async.models import OrderResult, OrderStatus

from backend.services.pocket_direct import DirectPocketOptionClient


class DirectDemoTradingClient:
    """Demo-only Pocket order client using AlphaPulse's proven direct Socket.IO handshake.

    This wrapper intentionally refuses non-demo operation. It sends exactly one
    openOrder request and requires a broker successopenOrder confirmation; it never
    assumes a trade was opened after a timeout and never retries an uncertain order.
    """

    def __init__(self, ssid: str):
        self._client = DirectPocketOptionClient(ssid, is_demo=True)

    @property
    def is_connected(self) -> bool:
        return bool(self._client.is_connected)

    async def connect(self, persistent: bool = False) -> bool:
        del persistent
        return await self._client.connect(persistent=False)

    async def disconnect(self) -> None:
        await self._client.disconnect()

    @staticmethod
    def _error_text(payload: Any) -> str:
        if isinstance(payload, dict):
            for key in ("message", "error", "reason", "detail"):
                value = payload.get(key)
                if value:
                    return str(value)[:160]
        return "Pocket Option rejected demo order"

    @staticmethod
    def _order_id(payload: Any, fallback: str) -> str:
        candidates: list[Any] = []
        if isinstance(payload, dict):
            candidates.append(payload)
            for key in ("deal", "data", "order"):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    candidates.append(nested)
        elif isinstance(payload, list):
            candidates.extend(row for row in payload if isinstance(row, dict))
        for row in candidates:
            for key in ("id", "uuid", "dealId", "deal_id", "orderId", "order_id", "ticket"):
                value = row.get(key)
                if value not in (None, ""):
                    return str(value)
        return fallback

    async def place_order(self, *, asset: str, amount: float, direction, duration: int) -> OrderResult:
        if not self._client.is_demo:
            raise RuntimeError("Direct trading client is demo-only")
        if float(amount) <= 0:
            raise ValueError("Demo amount must be positive")
        if int(duration) < 5:
            raise ValueError("Demo duration must be at least 5 seconds")
        if not await self.connect(persistent=False):
            raise RuntimeError("Pocket Option demo connection failed")
        ws = self._client._ws
        if ws is None or ws.closed:
            raise RuntimeError("Pocket Option demo websocket is not open")

        action = str(getattr(direction, "value", direction)).strip().lower()
        if action not in {"call", "put"}:
            raise ValueError("Direction must be call or put")

        request_id = str(uuid.uuid4())
        order_payload = [
            "openOrder",
            {
                "asset": str(asset),
                "amount": float(amount),
                "action": action,
                "isDemo": 1,
                "requestId": request_id,
                "optionType": 100,
                "time": int(duration),
            },
        ]
        placed_at = datetime.now()
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
                    return OrderResult(
                        order_id=self._order_id(payload, request_id),
                        asset=str(asset),
                        amount=float(amount),
                        direction=direction,
                        duration=int(duration),
                        status=OrderStatus.ACTIVE,
                        placed_at=placed_at,
                        expires_at=placed_at + timedelta(seconds=int(duration)),
                        error_message=None,
                    )

        # Important: never retry an order when the broker response is uncertain.
        # The scanner may publish another signal later, but this signal remains
        # failed/unknown rather than risking a duplicate order.
        raise RuntimeError("Pocket Option did not confirm demo order")
