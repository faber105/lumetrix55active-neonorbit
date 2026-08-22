from __future__ import annotations

import json
import os
import platform
import uuid
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


def worker_version() -> str:
    return str(
        os.getenv("GIT_COMMIT_SHA")
        or os.getenv("VERCEL_GIT_COMMIT_SHA")
        or "local"
    ).strip()[:64]


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


async def register_heartbeat(*, status: str = "ONLINE", capabilities: dict | None = None) -> None:
    await ensure_worker_schema()
    now = utcnow()
    payload = json.dumps(
        capabilities
        or {"demo_orders": True, "real_orders": False, "realtime": True},
        ensure_ascii=False,
    )
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""INSERT INTO workers
                (id,hostname,version,heartbeat_at,status,capabilities,updated_at)
                VALUES (:id,:host,:version,:heartbeat,:status,CAST(:capabilities AS JSONB),:updated)
                ON CONFLICT (id) DO UPDATE SET
                hostname=EXCLUDED.hostname,version=EXCLUDED.version,
                heartbeat_at=EXCLUDED.heartbeat_at,status=EXCLUDED.status,
                capabilities=EXCLUDED.capabilities,updated_at=EXCLUDED.updated_at"""),
            {
                "id": worker_id(),
                "host": platform.node()[:128],
                "version": worker_version(),
                "heartbeat": now,
                "status": status[:20],
                "capabilities": payload,
                "updated": now,
            },
        )
        await db.commit()


async def acquire_lease(account_id: int, *, seconds: int = 15) -> int | None:
    await ensure_worker_schema()
    async with AsyncSessionLocal() as db:
        generation = (
            await db.execute(
                text("""INSERT INTO worker_leases
                    (account_id,worker_id,lease_until,generation,updated_at)
                    VALUES (:account,:worker,NOW() + (:seconds * INTERVAL '1 second'),1,NOW())
                    ON CONFLICT (account_id) DO UPDATE SET
                    worker_id=EXCLUDED.worker_id,
                    lease_until=EXCLUDED.lease_until,
                    generation=CASE
                        WHEN worker_leases.worker_id=EXCLUDED.worker_id THEN worker_leases.generation
                        ELSE worker_leases.generation+1 END,
                    updated_at=NOW()
                    WHERE worker_leases.worker_id=EXCLUDED.worker_id
                       OR worker_leases.lease_until < NOW()
                    RETURNING generation"""),
                {"account": int(account_id), "worker": worker_id(), "seconds": max(5, int(seconds))},
            )
        ).scalar_one_or_none()
        await db.commit()
    return int(generation) if generation is not None else None


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


async def release_lease(account_id: int) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM worker_leases WHERE account_id=:account AND worker_id=:worker"),
            {"account": int(account_id), "worker": worker_id()},
        )
        await db.commit()


async def enqueue_command(
    *, account_id: int, command_type: str, payload: dict, idempotency_key: str
) -> dict[str, Any]:
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


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


async def append_session_event(
    session_id: int,
    stage: str,
    message: str,
    payload: dict | None = None,
    *,
    source_ts: datetime | None = None,
) -> dict[str, Any]:
    await ensure_worker_schema()
    event_id = uuid.uuid4().hex
    source = source_ts or utcnow()
    async with AsyncSessionLocal() as db:
        async with db.begin():
            sequence = (
                await db.execute(
                    text("""UPDATE auto_trade_sessions
                        SET version=version+1,updated_at=:now WHERE id=:sid
                        RETURNING version"""),
                    {"now": utcnow(), "sid": int(session_id)},
                )
            ).scalar_one()
            await db.execute(
                text("""INSERT INTO auto_trade_events
                    (session_id,stage,message,payload,event_id,sequence,source_ts)
                    VALUES (:sid,:stage,:message,:payload,:event_id,:sequence,:source_ts)"""),
                {
                    "sid": int(session_id),
                    "stage": str(stage)[:32],
                    "message": str(message),
                    "payload": json.dumps(payload or {}, ensure_ascii=False),
                    "event_id": event_id,
                    "sequence": int(sequence),
                    "source_ts": source,
                },
            )
    return {
        "event_id": event_id,
        "session_id": int(session_id),
        "sequence": int(sequence),
        "stage": str(stage),
        "message": str(message),
        "payload": payload or {},
        "source_ts": _json_value(source),
    }


async def realtime_snapshot(account_id: int, *, after_sequence: int = 0) -> dict[str, Any]:
    await ensure_worker_schema()
    async with AsyncSessionLocal() as db:
        account = (
            await db.execute(
                text("SELECT id,owner_telegram_id,mode,status FROM broker_accounts WHERE id=:id"),
                {"id": int(account_id)},
            )
        ).mappings().first()
        if not account:
            raise ValueError("Broker account not found")
        session = (
            await db.execute(
                text("""SELECT * FROM auto_trade_sessions
                    WHERE account_id=:account ORDER BY id DESC LIMIT 1"""),
                {"account": int(account_id)},
            )
        ).mappings().first()
        legs = []
        events = []
        if session:
            legs = (
                await db.execute(
                    text("""SELECT * FROM auto_trade_legs
                        WHERE session_id=:sid ORDER BY id DESC LIMIT 100"""),
                    {"sid": int(session["id"])},
                )
            ).mappings().all()
            events = (
                await db.execute(
                    text("""SELECT id,event_id,sequence,stage,message,payload,source_ts,created_at
                        FROM auto_trade_events WHERE session_id=:sid
                          AND (sequence>:after OR (:after=0 AND sequence IS NULL))
                        ORDER BY COALESCE(sequence,id) ASC LIMIT 200"""),
                    {"sid": int(session["id"]), "after": max(0, int(after_sequence))},
                )
            ).mappings().all()
        lease = (
            await db.execute(
                text("""SELECT l.worker_id,l.lease_until,l.generation,w.hostname,w.version,
                    w.heartbeat_at,w.status FROM worker_leases l
                    LEFT JOIN workers w ON w.id=l.worker_id WHERE l.account_id=:account"""),
                {"account": int(account_id)},
            )
        ).mappings().first()
        runtime_payload = (
            await db.execute(
                text("SELECT payload FROM ml_state WHERE strategy='__auto_trade_runtime__'"),
            )
        ).scalar_one_or_none()

    now = utcnow()
    worker_status = "OFFLINE"
    if lease and lease.get("heartbeat_at"):
        age = max(0.0, (now - lease["heartbeat_at"]).total_seconds())
        worker_status = "ONLINE" if age <= 10 else ("DEGRADED" if age <= 20 else "OFFLINE")
    else:
        age = None
    event_rows = []
    for row in events:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["payload"] = {}
        event_rows.append(_json_value(item))
    latest_sequence = max(
        [int(item.get("sequence") or 0) for item in event_rows] + [int(after_sequence or 0)]
    )
    try:
        runtime = json.loads(runtime_payload or "{}")
    except (TypeError, json.JSONDecodeError):
        runtime = {}
    return {
        "active": bool(session and str(session.get("status")) == "ACTIVE"),
        "account": _json_value(dict(account)),
        "session": _json_value(dict(session)) if session else None,
        "legs": _json_value([dict(row) for row in legs]),
        "events": event_rows,
        "runtime": _json_value(runtime),
        "min_payout": 92.0,
        "sequence": latest_sequence,
        "worker": {
            **(_json_value(dict(lease)) if lease else {}),
            "status": worker_status,
            "heartbeat_age_seconds": round(age, 3) if age is not None else None,
        },
        "server_time": _json_value(now),
    }


async def _claim_command(account_id: int) -> dict | None:
    if not await owns_lease(account_id):
        return None
    async with AsyncSessionLocal() as db:
        async with db.begin():
            row = (
                await db.execute(
                    text("""SELECT * FROM worker_commands
                        WHERE account_id=:account AND status='PENDING'
                        ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"""),
                    {"account": int(account_id)},
                )
            ).mappings().first()
            if not row:
                return None
            await db.execute(
                text("""UPDATE worker_commands SET
                    status='CLAIMED',claimed_at=:now,claimed_by=:worker WHERE id=:id"""),
                {"now": utcnow(), "worker": worker_id(), "id": int(row["id"])},
            )
            return dict(row)


async def _finish_command(command_id: int, *, result: dict | None = None, error: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""UPDATE worker_commands SET
                status=:status,completed_at=:now,result=CAST(:result AS JSONB),error=:error
                WHERE id=:id AND claimed_by=:worker"""),
            {
                "status": "FAILED" if error else "COMPLETED",
                "now": utcnow(),
                "result": json.dumps(result or {}, ensure_ascii=False),
                "error": str(error)[:256] if error else None,
                "id": int(command_id),
                "worker": worker_id(),
            },
        )
        await db.commit()


async def process_one_command(account_id: int) -> dict | None:
    command = await _claim_command(account_id)
    if not command:
        return None
    try:
        payload = command.get("payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        command_type = str(command["type"]).upper()
        if command_type == "START_SESSION":
            from backend.services.session_engine import start_session

            result = await start_session(dict(payload))
        elif command_type == "STOP_SESSION":
            from backend.services.session_engine import stop_session
            from backend.services.trade_mode import set_execution_mode

            await set_execution_mode("confirm")
            result = await stop_session(str(payload.get("reason") or "USER_STOP"))
        else:
            raise ValueError(f"Unsupported worker command: {command_type}")
        await _finish_command(int(command["id"]), result={"ok": True})
        return {"status": "COMMAND_COMPLETED", "command_id": int(command["id"]), "result": result}
    except Exception as exc:
        await _finish_command(int(command["id"]), error=f"{type(exc).__name__}: {exc}")
        return {"status": "COMMAND_FAILED", "command_id": int(command["id"]), "error": type(exc).__name__}


async def worker_supervisor(stop_event: Any, account_id: int) -> None:
    while not stop_event.is_set():
        try:
            generation = await acquire_lease(account_id)
            await register_heartbeat(status="ONLINE" if generation is not None else "STANDBY")
        except Exception:
            # A transient Neon/network failure must not kill the 24/7 supervisor.
            generation = None
        try:
            await asyncio_wait(stop_event, 5.0)
        except TimeoutError:
            pass


async def asyncio_wait(stop_event: Any, timeout: float) -> None:
    import asyncio

    await asyncio.wait_for(stop_event.wait(), timeout=timeout)
