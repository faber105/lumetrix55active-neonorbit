from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.models.db_models import AsyncSessionLocal, MLState, utcnow

RUNTIME_KEY = "__auto_trade_runtime__"
DEFAULT_RUNTIME = {
    "stage": "IDLE",
    "pending_signal_id": None,
    "pair": None,
    "asset": None,
    "strategy": None,
    "timeframe": None,
    "payout_percent": None,
    "balance": None,
    "balance_is_demo": None,
    "entry_time": None,
    "expiry_time": None,
    "message": None,
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode(payload: str | None) -> dict:
    try:
        value = json.loads(payload or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


async def get_trade_runtime() -> dict:
    async with AsyncSessionLocal() as db:
        row = await db.get(MLState, RUNTIME_KEY)
        current = _decode(row.payload if row else None)
    return {**DEFAULT_RUNTIME, **current}


async def update_trade_runtime(**changes) -> dict:
    async with AsyncSessionLocal() as db:
        row = await db.get(MLState, RUNTIME_KEY)
        current = {**DEFAULT_RUNTIME, **_decode(row.payload if row else None)}
        current.update(changes)
        current["updated_at"] = _iso_now()
        payload = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
        if row is None:
            row = MLState(strategy=RUNTIME_KEY, payload=payload, samples=0, updated_at=utcnow())
            db.add(row)
        else:
            row.payload = payload
            row.updated_at = utcnow()
        await db.commit()
    return current


async def reset_trade_runtime(stage: str = "IDLE", message: str | None = None) -> dict:
    return await update_trade_runtime(
        stage=stage,
        pending_signal_id=None,
        pair=None,
        asset=None,
        strategy=None,
        timeframe=None,
        payout_percent=None,
        entry_time=None,
        expiry_time=None,
        message=message,
    )
