from __future__ import annotations

from typing import Any, Protocol


class DemoBrokerAdapter(Protocol):
    """Small injectable boundary around the unofficial broker transport."""

    last_open_price: float | None

    async def connect(self, persistent: bool = False) -> bool: ...

    async def disconnect(self) -> None: ...

    async def account_snapshot(self, listen_seconds: float = 1.2) -> dict: ...

    async def place_order(
        self,
        *,
        asset: str,
        amount: float,
        direction: Any,
        duration: int,
        idempotency_key: str | None = None,
    ) -> Any: ...

    async def find_order(self, idempotency_key: str) -> Any | None: ...
