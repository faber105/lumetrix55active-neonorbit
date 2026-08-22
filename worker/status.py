from __future__ import annotations

import asyncio
import json
import os
from datetime import timezone

from dotenv import load_dotenv
from sqlalchemy import text


async def collect_status() -> dict:
    from backend.models.db_models import AsyncSessionLocal, utcnow
    from backend.services.worker_protocol import worker_id

    async with AsyncSessionLocal() as db:
        active_sessions = int(
            (await db.execute(text("SELECT COUNT(*) FROM auto_trade_sessions WHERE status='ACTIVE'"))).scalar_one()
        )
        unresolved_positions = int(
            (
                await db.execute(
                    text("""SELECT COUNT(*) FROM auto_trade_legs
                        WHERE result IN ('PENDING','UNKNOWN')""")
                )
            ).scalar_one()
        )
        worker = (
            await db.execute(
                text("SELECT heartbeat_at,status,version FROM workers WHERE id=:id"),
                {"id": worker_id()},
            )
        ).mappings().first()

    heartbeat_age = None
    status = "OFFLINE"
    if worker and worker.get("heartbeat_at"):
        heartbeat = worker["heartbeat_at"]
        if heartbeat.tzinfo:
            heartbeat = heartbeat.astimezone(timezone.utc).replace(tzinfo=None)
        heartbeat_age = max(0.0, (utcnow() - heartbeat).total_seconds())
        status = "ONLINE" if heartbeat_age <= 10 else ("DEGRADED" if heartbeat_age <= 20 else "OFFLINE")
    return {
        "worker_status": status,
        "worker_version": worker.get("version") if worker else None,
        "heartbeat_age_seconds": round(heartbeat_age, 2) if heartbeat_age is not None else None,
        "active_sessions": active_sessions,
        "unresolved_positions": unresolved_positions,
        "safe_to_update": active_sessions == 0 and unresolved_positions == 0,
    }


def main() -> None:
    load_dotenv(override=False)
    print(json.dumps(asyncio.run(collect_status()), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
