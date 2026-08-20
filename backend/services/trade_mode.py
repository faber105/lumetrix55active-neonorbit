from __future__ import annotations

from backend.models.db_models import AsyncSessionLocal, MLState, utcnow

TRADE_MODE_KEY = "__trade_account_mode__"
VALID_TRADE_MODES = {"demo", "real"}


async def get_trade_account_mode() -> str:
    async with AsyncSessionLocal() as db:
        row = await db.get(MLState, TRADE_MODE_KEY)
        value = str(row.payload or "").strip().lower() if row else ""
        return value if value in VALID_TRADE_MODES else "demo"


async def set_trade_account_mode(mode: str) -> str:
    value = str(mode or "").strip().lower()
    if value not in VALID_TRADE_MODES:
        raise ValueError("Trade account mode must be demo or real")
    async with AsyncSessionLocal() as db:
        row = await db.get(MLState, TRADE_MODE_KEY)
        if row is None:
            row = MLState(strategy=TRADE_MODE_KEY, payload=value, samples=0, updated_at=utcnow())
            db.add(row)
        else:
            row.payload = value
            row.updated_at = utcnow()
        await db.commit()
    return value
