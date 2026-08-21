from __future__ import annotations

import asyncio
import logging

from backend.services import auto_trade
from backend.services.pocket_demo_trading import DirectDemoTradingClient
from backend.services.trade_runtime import reset_trade_runtime

logger = logging.getLogger("alphapulse.auto_recovery")

_ORIGINAL_PLACE_ORDER = DirectDemoTradingClient.place_order
_ORIGINAL_PROCESS_PENDING = auto_trade.process_pending_auto_trade
_PATCHED = False


async def _resilient_place_order(self, *args, **kwargs):
    amount = float(kwargs.get("amount") or 0)
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            return await _ORIGINAL_PLACE_ORDER(self, *args, **kwargs)
        except RuntimeError as exc:
            last_exc = exc
            if "notenoughfunds" not in str(exc).replace(" ", "").lower():
                raise

            # Pocket can briefly keep funds locked immediately after a deal is
            # settled. Verify the live DEMO balance before deciding whether this
            # is a real insufficient-funds condition or a transient broker race.
            balance = None
            try:
                snapshot = await self.account_snapshot(listen_seconds=0.8)
                if snapshot.get("balance") is not None:
                    balance = float(snapshot["balance"])
            except Exception:
                pass

            if balance is not None and amount > 0 and balance + 1e-9 < amount:
                logger.warning(
                    "AUTO order rejected: real insufficient demo balance amount=%s balance=%s",
                    amount,
                    balance,
                )
                raise

            if attempt >= 2:
                raise

            delay = 1.25 * (attempt + 1)
            logger.warning(
                "Pocket returned NotEnoughFunds with sufficient/unknown balance; retrying AUTO order attempt=%s delay=%.2fs",
                attempt + 2,
                delay,
            )
            await asyncio.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Pocket AUTO order failed")


async def _resilient_process_pending_auto_trade() -> dict:
    result = await _ORIGINAL_PROCESS_PENDING()
    if str(result.get("status") or "").upper() == "FAILED":
        error = str(result.get("error") or result.get("reason") or "PocketError")
        await reset_trade_runtime(
            "SCANNING",
            f"Pocket не принял ордер ({error}) · продолжаю поиск следующего входа",
        )
        return {**result, "recovered": True}
    return result


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return
    DirectDemoTradingClient.place_order = _resilient_place_order
    auto_trade.process_pending_auto_trade = _resilient_process_pending_auto_trade
    _PATCHED = True


install()
