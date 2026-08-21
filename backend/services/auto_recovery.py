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
_last_insufficient: dict | None = None


class InsufficientFundsError(RuntimeError):
    pass


async def _resilient_place_order(self, *args, **kwargs):
    global _last_insufficient
    amount = float(kwargs.get("amount") or 0)
    last_exc: Exception | None = None
    _last_insufficient = None

    for attempt in range(3):
        try:
            return await _ORIGINAL_PLACE_ORDER(self, *args, **kwargs)
        except RuntimeError as exc:
            last_exc = exc
            if "notenoughfunds" not in str(exc).replace(" ", "").lower():
                raise

            balance = None
            try:
                snapshot = await self.account_snapshot(listen_seconds=0.9)
                if snapshot.get("balance") is not None:
                    balance = float(snapshot["balance"])
            except Exception:
                pass

            # Real lack of funds: do not keep scanning for trades that cannot be
            # opened. Mark it explicitly so the session layer can stop cleanly.
            if balance is not None and amount > 0 and balance + 1e-9 < amount:
                _last_insufficient = {"balance": balance, "required": amount}
                logger.warning(
                    "AUTO stopping: insufficient demo balance amount=%s balance=%s",
                    amount,
                    balance,
                )
                raise InsufficientFundsError(
                    f"Недостаточно средств: баланс {balance:.2f}, нужно {amount:.2f}"
                ) from exc

            # If the live balance says the amount is available (or Pocket has not
            # returned a balance yet), treat NotEnoughFunds as a short broker race
            # after settlement and retry the same order a few times.
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


async def _stop_for_insufficient_funds() -> dict:
    global _last_insufficient
    info = dict(_last_insufficient or {})
    balance = info.get("balance")
    required = info.get("required")
    message = "Недостаточно средств для следующей ставки"
    if balance is not None and required is not None:
        message = f"Недостаточно средств · баланс {float(balance):.2f} · нужно {float(required):.2f}"

    # Import lazily to avoid a startup circular import: session_engine imports
    # auto_trade, while this recovery module is installed during app startup.
    from backend.services import session_engine

    await session_engine.stop_session("INSUFFICIENT_FUNDS")
    latest = await session_engine._latest()
    if latest:
        await session_engine._update(
            int(latest["id"]),
            last_message=message,
            stop_reason="INSUFFICIENT_FUNDS",
        )
        await session_engine._event(
            int(latest["id"]),
            "STOPPED",
            message,
            {"balance": balance, "required": required, "reason": "INSUFFICIENT_FUNDS"},
        )
    await reset_trade_runtime("STOPPED", message)
    _last_insufficient = None
    return {
        "status": "STOPPED",
        "reason": "INSUFFICIENT_FUNDS",
        "message": message,
        "balance": balance,
        "required": required,
    }


async def _resilient_process_pending_auto_trade() -> dict:
    result = await _ORIGINAL_PROCESS_PENDING()
    if str(result.get("status") or "").upper() != "FAILED":
        return result

    error = str(result.get("error") or result.get("reason") or "PocketError")
    if error == "InsufficientFundsError" or _last_insufficient:
        return await _stop_for_insufficient_funds()

    # Other broker failures must not leave the session visually frozen. They are
    # recoverable: clear the failed runtime and allow the next scan to proceed.
    await reset_trade_runtime(
        "SCANNING",
        f"Pocket не принял ордер ({error}) · продолжаю поиск следующего входа",
    )
    return {**result, "recovered": True}


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return
    DirectDemoTradingClient.place_order = _resilient_place_order
    auto_trade.process_pending_auto_trade = _resilient_process_pending_auto_trade
    _PATCHED = True


install()
