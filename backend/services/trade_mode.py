from __future__ import annotations

from backend.models.db_models import AsyncSessionLocal, MLState, utcnow

TRADE_MODE_KEY = "__trade_account_mode__"
EXECUTION_MODE_KEY = "__trade_execution_mode__"
VALID_TRADE_MODES = {"demo", "real"}
VALID_EXECUTION_MODES = {"auto", "confirm"}


async def _get_value(key: str, default: str, allowed: set[str]) -> str:
    async with AsyncSessionLocal() as db:
        row = await db.get(MLState, key)
        value = str(row.payload or "").strip().lower() if row else ""
        return value if value in allowed else default


async def _set_value(key: str, value: str, allowed: set[str], label: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise ValueError(f"{label} must be one of: {', '.join(sorted(allowed))}")
    async with AsyncSessionLocal() as db:
        row = await db.get(MLState, key)
        if row is None:
            row = MLState(strategy=key, payload=normalized, samples=0, updated_at=utcnow())
            db.add(row)
        else:
            row.payload = normalized
            row.updated_at = utcnow()
        await db.commit()
    return normalized


async def get_trade_account_mode() -> str:
    return await _get_value(TRADE_MODE_KEY, "demo", VALID_TRADE_MODES)


async def set_trade_account_mode(mode: str) -> str:
    return await _set_value(TRADE_MODE_KEY, mode, VALID_TRADE_MODES, "Trade account mode")


async def get_execution_mode() -> str:
    return await _get_value(EXECUTION_MODE_KEY, "confirm", VALID_EXECUTION_MODES)


async def set_execution_mode(mode: str) -> str:
    return await _set_value(EXECUTION_MODE_KEY, mode, VALID_EXECUTION_MODES, "Execution mode")
