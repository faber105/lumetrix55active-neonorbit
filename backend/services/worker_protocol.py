from __future__ import annotations

import asyncio
import json
import os
import platform
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, utcnow
from backend.services.control import admin_id

_SCHEMA_READY = False

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS broker_accounts (
        id BIGSERIAL PRIMARY KEY,
        owner_telegram_id BIGINT NOT NULL,
        mode VARCHAR(8) NOT NULL,
        credential_ref VARCHAR(160) NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(owner_telegram_id, mode)
    )""",
    """CREATE TABLE IF NOT EXISTS workers (
        id VARCHAR(128) PRIMARY KEY,
        hostname VARCHAR(128) NOT NULL,
        version VARCHAR(64) NOT NULL,
        heartbeat_at TIMESTAMP NOT NULL,
        status VARCHAR(20) NOT NULL,
        capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS worker_leases (
        account_id BIGINT PRIMARY KEY,
        worker_id VARCHAR(128) NOT NULL,
        lease_until TIMESTAMP NOT NULL,
        generation BIGINT NOT NULL DEFAULT 1,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS worker_commands (
        id BIGSERIAL PRIMARY KEY,
        account_id BIGINT NOT NULL,
        type VARCHAR(32) NOT NULL,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        claimed_at TIMESTAMP,
        completed_at TIMESTAMP,
        claimed_by VARCHAR(128),
        result JSONB,
        error VARCHAR(256),
        idempotency_key VARCHAR(128) NOT NULL UNIQUE
    )""",
    "CREATE INDEX IF NOT EXISTS ix_worker_commands_pending ON worker_commands (account_id,status,created_at)",
    "ALTER TABLE auto_trade_sessions ADD COLUMN IF NOT EXISTS account_id BIGINT",
    "ALTER TABLE auto_trade_sessions ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE auto_trade_legs ADD COLUMN IF NOT EXISTS broker_order_id VARCHAR(128)",
    "ALTER TABLE auto_trade_legs ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_auto_trade_legs_idempotency ON auto_trade_legs (idempotency_key) WHERE idempotency_key IS NOT NULL",
    "ALTER TABLE auto_trade_events ADD COLUMN IF NOT EXISTS event_id VARCHAR(64)",
    "ALTER TABLE auto_trade_events ADD COLUMN IF NOT EXISTS sequence BIGINT",
    "ALTER TABLE auto_trade_events ADD COLUMN IF NOT EXISTS source_ts TIMESTAMP",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_auto_trade_events_sequence ON auto_trade_events (session_id,sequence) WHERE sequence IS NOT NULL",
)


def worker_id() -> str:
    return str(os.getenv("WORKER_ID") or platform.node() or "alphapulse-worker").strip()[:128]


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


async def ensure_worker_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    async with AsyncSessionLocal() as db:
        for statement in SCHEMA_STATEMENTS:
            await db.execute(text(statement))
        await db.commit()
    _SCHEMA_READY = True


async def ensure_demo_account(owner_telegram_id: int | None = None) -> int:
    await ensure_worker_schema()
    owner = int(owner_telegram_id or admin_id())
    if owner <= 0:
        raise RuntimeError("ADMIN_ID is not configured")
    async with AsyncSessionLocal() as db:
        account_id = (
            await db.execute(
                text("""INSERT INTO broker_accounts
                    (owner_telegram_id,mode,credential_ref,status,updated_at)
                    VALUES (:owner,'DEMO','windows:POCKET_OPTION_SSID','ACTIVE',:now)
                    ON CONFLICT (owner_telegram_id,mode) DO UPDATE
                    SET status='ACTIVE',updated_at=EXCLUDED.updated_at
                    RETURNING id"""),
                {"owner": owner, "now": utcnow()},
            )
        ).scalar_one()
        await db.commit()
    return int(account_id)


async def owns_lease(account_id: int) -> bool:
    await ensure_worker_schema()
    async with AsyncSessionLocal() as db:
        value = (
            await db.execute(
                text("""SELECT 1 FROM worker_leases
                    WHERE account_id=:account AND worker_id=:worker AND lease_until>NOW()"""),
                {"account": int(account_id), "worker": worker_id()},
            )
        ).scalar_one_or_none()
    return value is not None


async def enqueue_command(*, account_id: int, command_type: str, payload: dict, idempotency_key: str) -> dict[str, Any]:
    await ensure_worker_schema()
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text("""INSERT INTO worker_commands
                    (account_id,type,payload,status,idempotency_key)
                    VALUES (:account,:type,CAST(:payload AS JSONB),'PENDING',:key)
                    ON CONFLICT (idempotency_key) DO UPDATE
                    SET idempotency_key=EXCLUDED.idempotency_key
                    RETURNING id,account_id,type,status,created_at,idempotency_key"""),
                {
                    "account": int(account_id),
                    "type": str(command_type).upper()[:32],
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "key": str(idempotency_key)[:128],
                },
            )
        ).mappings().one()
        await db.commit()
    return {key: _json_value(value) for key, value in dict(row).items()}


async def await_command(command_id: int, account_id: int, *, timeout_seconds: float = 1.25) -> dict:
    deadline = time.monotonic() + max(0.2, min(30.0, float(timeout_seconds)))
    while time.monotonic() < deadline:
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    text("""SELECT status,result,error FROM worker_commands
                        WHERE id=:id AND account_id=:account"""),
                    {"id": int(command_id), "account": int(account_id)},
                )
            ).mappings().first()
        if not row:
            raise ValueError("Worker command not found")
        status = str(row["status"])
        if status == "COMPLETED":
            result = row.get("result") or {}
            return json.loads(result) if isinstance(result, str) else dict(result)
        if status == "FAILED":
            raise RuntimeError(str(row.get("error") or "Worker command failed"))
        await asyncio.sleep(0.12)
    raise TimeoutError("Worker command queued")


async def realtime_snapshot(account_id: int, *, after_sequence: int = 0) -> dict[str, Any]:
    # Compatibility wrapper used by the admin router. Keep it to one DB round trip.
    from worker.fast_snapshot import fast_realtime_snapshot
    return await fast_realtime_snapshot(account_id, after_sequence=after_sequence)
