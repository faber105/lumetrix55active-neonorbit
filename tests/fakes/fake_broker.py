from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class FakeOrder:
    order_id: str
    idempotency_key: str
    asset: str
    amount: float
    result: str = "PENDING"


class DeterministicFakeBroker:
    def __init__(self, orders: dict[str, FakeOrder] | None = None) -> None:
        self.connected = False
        self.orders: dict[str, FakeOrder] = orders if orders is not None else {}
        self.timeout_after_accept = False
        self.last_open_price = 1.23456

    async def connect(self, persistent: bool = False) -> bool:
        del persistent
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.connected = False

    async def account_snapshot(self, listen_seconds: float = 0) -> dict:
        del listen_seconds
        if not self.connected:
            raise ConnectionError("fake broker disconnected")
        return {"balance": 1000.0, "balance_is_demo": True, "payouts": {"EURUSD_otc": 92}}

    async def place_order(self, *, asset, amount, direction, duration, idempotency_key=None):
        del direction, duration
        if not self.connected:
            raise ConnectionError("fake broker disconnected")
        key = str(idempotency_key)
        order = self.orders.setdefault(
            key,
            FakeOrder(f"fake-{len(self.orders) + 1}", key, str(asset), float(amount)),
        )
        if self.timeout_after_accept:
            self.timeout_after_accept = False
            raise asyncio.TimeoutError("accepted but response lost")
        return order

    async def find_order(self, idempotency_key: str) -> FakeOrder | None:
        if not self.connected:
            raise ConnectionError("fake broker disconnected")
        return self.orders.get(str(idempotency_key))

    def settle(self, idempotency_key: str, result: str) -> FakeOrder:
        order = self.orders[str(idempotency_key)]
        order.result = result.upper()
        return order
