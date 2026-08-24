from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, utcnow


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _object(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else None
        except Exception:
            return None
    try:
        return dict(value)
    except Exception:
        return None


def _array(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, list) else []
        except Exception:
            return []
    return list(value) if isinstance(value, tuple) else []


async def fast_realtime_snapshot(account_id: int, *, after_sequence: int = 0) -> dict[str, Any]:
    after = max(0, int(after_sequence))
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    """
                    WITH account AS (
                        SELECT id,owner_telegram_id,mode,status
                          FROM broker_accounts
                         WHERE id=:account
                    ), latest_session AS (
                        SELECT * FROM auto_trade_sessions
                         WHERE account_id=:account
                         ORDER BY id DESC LIMIT 1
                    ), lease AS (
                        SELECT l.worker_id,l.lease_until,l.generation,
                               w.hostname,w.version,w.heartbeat_at,w.status
                          FROM worker_leases l
                          LEFT JOIN workers w ON w.id=l.worker_id
                         WHERE l.account_id=:account
                    )
                    SELECT
                        (SELECT row_to_json(a) FROM account a) AS account,
                        (SELECT row_to_json(s) FROM latest_session s) AS session,
                        COALESCE((
                            SELECT json_agg(row_to_json(x) ORDER BY x.id DESC)
                              FROM (
                                SELECT * FROM auto_trade_legs
                                 WHERE session_id=(SELECT id FROM latest_session)
                                 ORDER BY id DESC LIMIT 100
                              ) x
                        ), '[]'::json) AS legs,
                        COALESCE((
                            SELECT json_agg(row_to_json(e) ORDER BY COALESCE(e.sequence,e.id) ASC)
                              FROM (
                                SELECT id,event_id,sequence,stage,message,payload,source_ts,created_at
                                  FROM auto_trade_events
                                 WHERE session_id=(SELECT id FROM latest_session)
                                   AND (sequence>:after OR (:after=0 AND sequence IS NULL))
                                 ORDER BY COALESCE(sequence,id) ASC LIMIT 200
                              ) e
                        ), '[]'::json) AS events,
                        (SELECT row_to_json(l) FROM lease l) AS lease,
                        (SELECT payload FROM ml_state WHERE strategy='__auto_trade_runtime__') AS runtime,
                        COALESCE((
                            SELECT enabled
                              FROM auto_preload_config
                             WHERE telegram_id=(SELECT owner_telegram_id FROM account)
                        ), FALSE) AS preload_enabled,
                        (SELECT row_to_json(p)
                           FROM auto_preload_candidates p
                          WHERE p.session_id=(SELECT id FROM latest_session)
                          LIMIT 1) AS preload_candidate
                    """
                ),
                {"account": int(account_id), "after": after},
            )
        ).mappings().one()

    account = _object(row.get("account"))
    if not account:
        raise ValueError("Broker account not found")
    session = _object(row.get("session"))
    legs = _array(row.get("legs"))
    events = _array(row.get("events"))
    lease = _object(row.get("lease"))
    preload_candidate = _object(row.get("preload_candidate"))

    event_rows: list[dict] = []
    for raw in events:
        item = _object(raw) or {}
        payload = item.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload or "{}")
            except Exception:
                payload = {}
        item["payload"] = payload if isinstance(payload, dict) else {}
        event_rows.append(item)

    runtime_raw = row.get("runtime")
    if isinstance(runtime_raw, dict):
        runtime = runtime_raw
    else:
        try:
            runtime = json.loads(runtime_raw or "{}")
        except Exception:
            runtime = {}

    now = utcnow()
    worker_status = "OFFLINE"
    age = None
    if lease and lease.get("heartbeat_at"):
        heartbeat = lease["heartbeat_at"]
        if isinstance(heartbeat, str):
            try:
                heartbeat = datetime.fromisoformat(heartbeat.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                heartbeat = None
        if isinstance(heartbeat, datetime):
            current = now
            if current.tzinfo is not None and heartbeat.tzinfo is None:
                current = current.replace(tzinfo=None)
            if heartbeat.tzinfo is not None and current.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=None)
            age = max(0.0, (current - heartbeat).total_seconds())
            worker_status = "ONLINE" if age <= 10 else ("DEGRADED" if age <= 20 else "OFFLINE")

    latest_sequence = max([int((_object(item) or {}).get("sequence") or 0) for item in event_rows] + [after])
    worker = dict(lease or {})
    worker["status"] = worker_status
    worker["heartbeat_age_seconds"] = round(age, 3) if age is not None else None

    return _json_value({
        "active": bool(session and str(session.get("status")) == "ACTIVE"),
        "account": account,
        "session": session,
        "legs": legs,
        "events": event_rows,
        "runtime": runtime,
        "min_payout": 92.0,
        "sequence": latest_sequence,
        "worker": worker,
        "preload_enabled": bool(row.get("preload_enabled")),
        "preload_candidate": preload_candidate,
        "server_time": now,
    })
