from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal
from backend.telegram_auth import TelegramMiniAppUser, admin_user

router = APIRouter()


def _parse_bound(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat() + ("" if getattr(value, "tzinfo", None) else "Z")
    return value


def _filters(user_id: int, start, end):
    clauses = ["telegram_id=:tid"]
    params = {"tid": int(user_id)}
    if start is not None:
        clauses.append("created_at>=:start")
        params["start"] = start
    if end is not None:
        clauses.append("created_at<:end")
        params["end"] = end
    return " AND ".join(clauses), params


@router.get("/sessions")
async def sessions(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    limit: int = Query(500, ge=1, le=1000),
    user: TelegramMiniAppUser = Depends(admin_user),
):
    start, end = _parse_bound(from_ts), _parse_bound(to_ts)
    where, params = _filters(int(user.id), start, end)
    params["lim"] = int(limit)
    sql = f"""
        WITH leg_stats AS (
            SELECT session_id,
                   COUNT(*) AS leg_count,
                   COUNT(*) FILTER (WHERE result='WIN') AS wins_count,
                   COUNT(*) FILTER (WHERE result='LOSS') AS losses_count,
                   COUNT(*) FILTER (WHERE result='DRAW') AS draws_count,
                   COUNT(*) FILTER (WHERE martingale_level>0) AS covered_count,
                   COALESCE(SUM(amount),0) AS total_staked,
                   COALESCE(SUM(CASE WHEN pnl>0 THEN pnl ELSE 0 END),0) AS gross_wins,
                   COALESCE(ABS(SUM(CASE WHEN pnl<0 THEN pnl ELSE 0 END)),0) AS gross_losses
              FROM auto_trade_legs
             GROUP BY session_id
        )
        SELECT s.id,s.status,s.stage,s.mode,s.strategy,s.timeframe,s.target_wins,s.target_profit,
               s.base_amount,s.max_martingale,s.max_failed_series,s.failed_series,s.total_legs,
               s.profit,s.start_balance,s.current_balance,s.stop_reason,s.created_at,s.updated_at,s.ended_at,
               COALESCE(ls.leg_count,0) AS leg_count,
               COALESCE(ls.wins_count,0) AS wins_count,
               COALESCE(ls.losses_count,0) AS losses_count,
               COALESCE(ls.draws_count,0) AS draws_count,
               COALESCE(ls.covered_count,0) AS covered_count,
               COALESCE(ls.total_staked,0) AS total_staked,
               COALESCE(ls.gross_wins,0) AS gross_wins,
               COALESCE(ls.gross_losses,0) AS gross_losses
          FROM auto_trade_sessions s
          LEFT JOIN leg_stats ls ON ls.session_id=s.id
         WHERE {where.replace('telegram_id', 's.telegram_id').replace('created_at', 's.created_at')}
         ORDER BY s.created_at DESC
         LIMIT :lim
    """
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(sql), params)).mappings().all()
    output = []
    for row in rows:
        item = dict(row)
        for key in ("created_at", "updated_at", "ended_at"):
            item[key] = _iso(item.get(key))
        resolved = int(item.get("wins_count") or 0) + int(item.get("losses_count") or 0)
        item["winrate"] = round(int(item.get("wins_count") or 0) / resolved * 100, 1) if resolved else None
        output.append(item)
    return output


@router.delete("/sessions")
async def clear_sessions(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    user: TelegramMiniAppUser = Depends(admin_user),
):
    start, end = _parse_bound(from_ts), _parse_bound(to_ts)
    where, params = _filters(int(user.id), start, end)
    where += " AND status<>'ACTIVE'"
    subquery = f"SELECT id FROM auto_trade_sessions WHERE {where}"
    async with AsyncSessionLocal() as db:
        count = int((await db.execute(text(f"SELECT COUNT(*) FROM auto_trade_sessions WHERE {where}"), params)).scalar_one() or 0)
        if not count:
            return {"ok": True, "deleted": 0, "active_preserved": True}
        await db.execute(text(f"DELETE FROM auto_preload_candidates WHERE session_id IN ({subquery})"), params)
        await db.execute(text(f"DELETE FROM auto_trade_events WHERE session_id IN ({subquery})"), params)
        await db.execute(text(f"DELETE FROM auto_trade_legs WHERE session_id IN ({subquery})"), params)
        await db.execute(text(f"DELETE FROM auto_trade_sessions WHERE {where}"), params)
        await db.commit()
    return {"ok": True, "deleted": count, "active_preserved": True}
