from __future__ import annotations

import asyncio
import time
from typing import Any

from backend.services.pocket_direct import DirectPocketOptionClient


def _percent(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1.0:
        number *= 100.0
    return round(number, 2)


class TelemetryPocketOptionClient(DirectPocketOptionClient):
    """Direct Pocket client that also records broker balance and live asset payouts.

    Pocket sends `successupdateBalance` and `updateAssets` on the authenticated
    Socket.IO stream. The base client remains responsible for handshake/candles;
    this subclass only captures those two read-only telemetry events.
    """

    def __init__(self, ssid: str, is_demo: bool = True):
        super().__init__(ssid, is_demo=is_demo)
        self.balance: float | None = None
        self.balance_is_demo: bool | None = None
        self.payouts: dict[str, float] = {}
        self.available_assets: dict[str, bool] = {}
        self._telemetry_updated_at: float | None = None

    def _capture_telemetry(self, event: str | None, payload: Any) -> None:
        if event == "successupdateBalance" and isinstance(payload, dict):
            try:
                if payload.get("balance") is not None:
                    self.balance = float(payload["balance"])
            except (TypeError, ValueError):
                pass
            if payload.get("isDemo") is not None:
                try:
                    self.balance_is_demo = bool(int(payload["isDemo"]))
                except Exception:
                    self.balance_is_demo = bool(payload["isDemo"])
            self._telemetry_updated_at = time.time()
            return

        if event == "updateAssets" and isinstance(payload, list):
            payouts: dict[str, float] = {}
            available: dict[str, bool] = {}
            for row in payload:
                if not isinstance(row, list) or len(row) < 6:
                    continue
                symbol = str(row[1] or "").strip()
                if not symbol:
                    continue
                payout = _percent(row[5])
                if payout is not None:
                    payouts[symbol] = payout
                value = row[14] if len(row) > 14 else True
                if isinstance(value, str):
                    available[symbol] = value.strip().lower() not in {"", "0", "false", "off", "closed"}
                else:
                    available[symbol] = bool(value)
            if payouts:
                self.payouts = payouts
                self.available_assets = available
                self._telemetry_updated_at = time.time()

    async def _recv_packet(self, timeout: float):
        event, payload = await super()._recv_packet(timeout)
        self._capture_telemetry(event, payload)
        return event, payload

    def snapshot(self) -> dict:
        return {
            "balance": self.balance,
            "balance_is_demo": self.balance_is_demo,
            "payouts": dict(self.payouts),
            "available_assets": dict(self.available_assets),
            "captured_at": self._telemetry_updated_at,
        }

    async def refresh_telemetry(self, listen_seconds: float = 1.2) -> dict:
        if not await self.connect(persistent=False):
            return self.snapshot()
        if self._ws is None or self._ws.closed:
            return self.snapshot()
        deadline = time.monotonic() + max(0.1, min(float(listen_seconds), 2.5))
        async with self._lock:
            while time.monotonic() < deadline:
                try:
                    await self._recv_packet(min(0.25, max(0.05, deadline - time.monotonic())))
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break
                if self.balance is not None and self.payouts:
                    break
        return self.snapshot()
