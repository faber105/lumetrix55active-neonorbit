from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from backend.models.db_models import AsyncSessionLocal, PaperPosition, utcnow
from backend.services.auto_trade import (
    MIN_AUTO_PAYOUT,
    execute_confirmed_signal,
    get_demo_account_snapshot,
    update_auto_trade_control,
)
from backend.services.control import admin_id
from backend.services.pocketoption_otc import OTC_ASSETS
from backend.services.positions import reconcile_positions
from backend.services.session_engine import (
    COUNT_CONFIRM_CONFIDENCE,
    COUNT_MIN_CONFIDENCE,
    PROFIT_MIN_CONFIDENCE,
    _active,
    _event,
    _load_signal,
    _next_amount,
    _payout,
    _register_open,
    _settle,
    _tradable,
    _update,
)
from backend.services.signal_engine import signal_engine
from backend.services.signal_store import save_signal
from backend.services.trade_runtime import update_trade_runtime

_PRELOAD_SCHEMA_READY = False
ENTRY_TOLERANCE_SECONDS = 3.0


def _to_naive_utc(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


async def ensure_preload_schema() -> None:
    global _PRELOAD_SCHEMA_READY
    if _PRELOAD_SCHEMA_READY:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS auto_preload_config (
                telegram_id BIGINT PRIMARY KEY,
                enabled BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS auto_preload_candidates (
                session_id BIGINT PRIMARY KEY,
                signal_id BIGINT,
                entry_time TIMESTAMP,
                expiry_time TIMESTAMP,
                amount DOUBLE PRECISION,
                payout DOUBLE PRECISION,
                opened_position_id BIGINT,
                status VARCHAR(24) NOT NULL DEFAULT 'SEARCHING',
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        await db.commit()
    _PRELOAD_SCHEMA_READY = True


async def get_preload_enabled() -> bool:
    await ensure_preload_schema()
    tid = admin_id()
    if tid <= 0:
        return False
    async with AsyncSessionLocal() as db:
        value = (await db.execute(
            text("SELECT enabled FROM auto_preload_config WHERE telegram_id=:tid"),
            {"tid": tid},
        )).scalar_one_or_none()
    return bool(value)


async def set_preload_enabled(enabled: bool) -> bool:
    await ensure_preload_schema()
    tid = admin_id()
    if tid <= 0:
        raise RuntimeError("ADMIN_ID is not configured")
    async with AsyncSessionLocal() as db:
        await db.execute(text("""
            INSERT INTO auto_preload_config (telegram_id,enabled,updated_at)
            VALUES (:tid,:enabled,:now)
            ON CONFLICT (telegram_id) DO UPDATE
            SET enabled=EXCLUDED.enabled, updated_at=EXCLUDED.updated_at
        """), {"tid": tid, "enabled": bool(enabled), "now": utcnow()})
        if not enabled:
            await db.execute(text("""
                UPDATE auto_preload_candidates
                   SET status='CANCELLED', updated_at=:now
                 WHERE session_id IN (
                    SELECT id FROM auto_trade_sessions
                     WHERE telegram_id=:tid AND status='ACTIVE'
                 ) AND status IN ('SEARCHING','PREPARED','WAIT_CLOSE')
            """), {"tid": tid, "now": utcnow()})
        await db.commit()
    try:
        await update_auto_trade_control(max_open_positions=1)
    except Exception:
        pass
    return bool(enabled)


async def preload_state() -> dict:
    await ensure_preload_schema()
    enabled = await get_preload_enabled()
    session = await _active()
    candidate = None
    if session:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                text("SELECT * FROM auto_preload_candidates WHERE session_id=:sid"),
                {"sid": int(session["id"])},
            )).mappings().first()
        if row:
            candidate = dict(row)
            for key in ("entry_time", "expiry_time", "updated_at"):
                candidate[key] = _iso(candidate.get(key)) if candidate.get(key) else None
    return {
        "enabled": enabled,
        "session_id": int(session["id"]) if session else None,
        "mode": session.get("mode") if session else None,
        "timeframe": session.get("timeframe") if session else None,
        "candidate": candidate,
    }


async def _candidate(session_id: int) -> dict | None:
    await ensure_preload_schema()
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("SELECT * FROM auto_preload_candidates WHERE session_id=:sid"),
            {"sid": int(session_id)},
        )).mappings().first()
    return dict(row) if row else None


async def _save_candidate(session_id: int, **values) -> None:
    await ensure_preload_schema()
    old = await _candidate(int(session_id))
    payload = {
        "sid": int(session_id),
        "signal": values.get("signal_id", old.get("signal_id") if old else None),
        "entry": values.get("entry_time", old.get("entry_time") if old else None),
        "expiry": values.get("expiry_time", old.get("expiry_time") if old else None),
        "amount": values.get("amount", old.get("amount") if old else None),
        "payout": values.get("payout", old.get("payout") if old else None),
        "position": values.get("opened_position_id", old.get("opened_position_id") if old else None),
        "status": str(values.get("status") or (old.get("status") if old else "SEARCHING")),
        "now": utcnow(),
    }
    async with AsyncSessionLocal() as db:
        await db.execute(text("""
            INSERT INTO auto_preload_candidates
                (session_id,signal_id,entry_time,expiry_time,amount,payout,opened_position_id,status,updated_at)
            VALUES (:sid,:signal,:entry,:expiry,:amount,:payout,:position,:status,:now)
            ON CONFLICT (session_id) DO UPDATE SET
                signal_id=EXCLUDED.signal_id,
                entry_time=EXCLUDED.entry_time,
                expiry_time=EXCLUDED.expiry_time,
                amount=EXCLUDED.amount,
                payout=EXCLUDED.payout,
                opened_position_id=EXCLUDED.opened_position_id,
                status=EXCLUDED.status,
                updated_at=EXCLUDED.updated_at
        """), payload)
        await db.commit()


def _lead_seconds(session: dict) -> float:
    if str(session.get("mode")) == "profit":
        return 120.0
    return {"15s": 15.0, "1m": 60.0, "3m": 120.0}.get(str(session.get("timeframe")), 120.0)


def _retry_seconds(session: dict) -> float:
    return {"15s": 1.0, "1m": 2.5, "3m": 4.0, "5m": 6.0}.get(str(session.get("timeframe")), 4.0)


async def _prepare(session: dict, position: PaperPosition) -> dict | None:
    expiry = _to_naive_utc(position.expiry_time)
    remaining = (expiry - utcnow()).total_seconds()
    if remaining <= 0 or remaining > _lead_seconds(session):
        return None

    existing = await _candidate(int(session["id"]))
    if existing and str(existing.get("status")) in {"PREPARED", "WAIT_CLOSE", "CONSUMED"}:
        return {"status": str(existing.get("status")), "signal_id": existing.get("signal_id")}
    if existing and str(existing.get("status")) == "SEARCHING" and existing.get("updated_at"):
        age = (utcnow() - _to_naive_utc(existing["updated_at"])).total_seconds()
        if age < _retry_seconds(session):
            return {"status": "SEARCHING", "seconds_to_expiry": round(remaining, 1)}

    await _save_candidate(int(session["id"]), signal_id=None, entry_time=None, expiry_time=None, amount=None, payout=None, opened_position_id=None, status="SEARCHING")
    snapshot = await get_demo_account_snapshot(max_age=1.0)
    all_assets = list(OTC_ASSETS.keys())
    eligible_assets = [asset for asset in all_assets if _tradable(snapshot, asset)]
    if not eligible_assets:
        await _update(
            int(session["id"]),
            last_message=f"Сделка открыта · среди {len(all_assets)} OTC пар сейчас нет payout ≥{MIN_AUTO_PAYOUT:g}% · жду обновления выплат",
        )
        return {"status": "WAIT_PAYOUT", "eligible": 0, "seconds_to_expiry": round(remaining, 1)}
    timeframe = str(session.get("timeframe") or "5m")
    strategy = str(session.get("strategy") or "smart_confluence")
    mixed_count = str(session.get("mode")) == "count"

    if mixed_count:
        candidates = await signal_engine.scan_best_candidates(timeframe, eligible_assets)
        threshold = COUNT_MIN_CONFIDENCE
    elif strategy == "smart_confluence":
        candidates = await signal_engine.scan_best_candidates(timeframe, eligible_assets)
        threshold = PROFIT_MIN_CONFIDENCE
    else:
        candidates = await signal_engine.scan_strategy_candidates(timeframe, eligible_assets, strategy)
        threshold = PROFIT_MIN_CONFIDENCE

    confirmed = sorted(
        [
            item for item in candidates
            if float(item.get("confidence") or 0) >= threshold
            and _tradable(snapshot, str(item.get("asset") or ""))
        ],
        key=lambda item: (
            float(item.get("confidence") or 0),
            float(_payout(snapshot, str(item.get("asset") or "")) or 0),
        ),
        reverse=True,
    )

    # Pre-analysis also prepares exactly one best pair; alternatives are never queued.
    for candidate in confirmed:
        chosen = candidate
        if mixed_count:
            try:
                recheck = await signal_engine.evaluate_asset(
                    str(candidate.get("asset") or ""), timeframe, str(candidate.get("strategy") or "")
                )
            except Exception:
                recheck = None
            if not recheck:
                continue
            if recheck.get("direction") != candidate.get("direction"):
                continue
            if float(recheck.get("confidence") or 0) < COUNT_CONFIRM_CONFIDENCE:
                continue
            chosen = recheck

        try:
            candidate_entry = _to_naive_utc(chosen.get("entry_time"))
        except Exception:
            continue
        if abs((candidate_entry - expiry).total_seconds()) > ENTRY_TOLERANCE_SECONDS:
            continue

        stored, duplicate = await save_signal(chosen, is_vip=False)
        if duplicate:
            continue
        payout = _payout(snapshot, stored["asset"])
        if payout is None or payout < MIN_AUTO_PAYOUT:
            continue
        await _save_candidate(
            int(session["id"]),
            signal_id=int(stored["id"]),
            entry_time=candidate_entry,
            expiry_time=_to_naive_utc(stored["expiry_time"]),
            payout=payout,
            status="PREPARED",
        )
        await _update(
            int(session["id"]),
            last_message=f"Сделка открыта · следующий вход {stored['pair']} подтверждён заранее на {_iso(candidate_entry)}",
        )
        await _event(
            int(session["id"]),
            "PRE_ANALYSIS_READY",
            f"Следующий вход подготовлен {stored['pair']} {stored['direction']}",
            {"signal_id": int(stored["id"]), "entry_time": _iso(candidate_entry), "payout": payout},
        )
        await update_trade_runtime(
            stage="PRE_ANALYSIS_READY",
            pair=stored["pair"], asset=stored["asset"], strategy=stored["strategy"], timeframe=stored["timeframe"],
            payout_percent=payout, balance=snapshot.get("balance"), entry_time=stored["entry_time"], expiry_time=stored["expiry_time"],
            message="Текущая сделка ещё открыта · следующий сетап уже подтверждён",
        )
        return {"status": "PREPARED", "signal_id": int(stored["id"]), "entry_time": _iso(candidate_entry)}

    await _update(
        int(session["id"]),
        last_message=f"Сделка открыта · до закрытия {max(0, int(remaining))}с · заранее анализирую следующий вход только среди payout ≥92%",
    )
    return {"status": "SEARCHING", "seconds_to_expiry": round(remaining, 1)}


async def _consume_when_closed(session: dict, candidate: dict) -> dict | None:
    if str(candidate.get("status")) not in {"PREPARED", "WAIT_CLOSE"} or not candidate.get("signal_id"):
        return None
    entry = _to_naive_utc(candidate.get("entry_time"))
    if (entry - utcnow()).total_seconds() > 0.35:
        return None

    await reconcile_positions()
    current = await _active()
    if not current:
        return {"status": "IDLE", "block": True}
    if current.get("active_position_id"):
        current = await _settle(current)

    if current.get("status") != "ACTIVE":
        await _save_candidate(int(session["id"]), status="CANCELLED")
        return {"status": str(current.get("status") or "STOPPED"), "block": True, "preload_cancelled": "session_finished"}

    if current.get("active_position_id"):
        await _save_candidate(int(session["id"]), status="WAIT_CLOSE")
        await _update(
            int(session["id"]),
            last_message="Следующий сетап готов · жду фактический результат Pocket, чтобы рассчитать точную следующую ставку",
        )
        return {"status": "PRELOAD_WAIT_CLOSE", "block": True, "position_id": current.get("active_position_id")}

    signal = await _load_signal(int(candidate["signal_id"]))
    if not signal:
        await _save_candidate(int(session["id"]), status="CANCELLED")
        return {"status": "PRELOAD_SIGNAL_MISSING", "block": True}

    payout = float(candidate.get("payout") or MIN_AUTO_PAYOUT)
    amount = float(_next_amount(current, payout))
    await update_auto_trade_control(amount=amount, max_open_positions=1)
    await update_trade_runtime(
        stage="OPENING", pending_signal_id=int(signal["id"]), pair=signal["pair"], asset=signal["asset"],
        strategy=signal["strategy"], timeframe=signal["timeframe"], payout_percent=payout,
        entry_time=signal["entry_time"], expiry_time=signal["expiry_time"], amount=amount,
        message="Преданализ готов · результат прошлой сделки зафиксирован · открываю сразу",
    )
    trade = await execute_confirmed_signal(signal)
    if trade.get("status") == "OPEN":
        await _register_open(current, signal, trade, amount, trade.get("payout") or payout)
        await _save_candidate(int(session["id"]), amount=amount, opened_position_id=int(trade["position_id"]), status="CONSUMED")
        await _event(
            int(session["id"]),
            "PRELOAD_OPEN",
            f"Предварительно найденный сетап открыт сразу после закрытия предыдущей сделки · {signal['pair']}",
            {"signal_id": int(signal["id"]), "position_id": int(trade["position_id"]), "amount": amount, "payout": payout},
        )
        return {"status": "OPEN", "block": True, "preloaded": True, "trade": trade}

    await _save_candidate(int(session["id"]), status="CANCELLED")
    return {"status": str(trade.get("status") or "PRELOAD_FAILED"), "block": True, "trade": trade}


async def preload_cycle() -> dict | None:
    """Run before normal session_tick. It only pre-analyzes; stake is decided after actual WIN/LOSS."""
    if not await get_preload_enabled():
        return None
    session = await _active()
    if not session or session.get("status") != "ACTIVE":
        return None

    candidate = await _candidate(int(session["id"]))
    if candidate and str(candidate.get("status")) in {"PREPARED", "WAIT_CLOSE"}:
        consumed = await _consume_when_closed(session, candidate)
        if consumed:
            return consumed

    position_id = session.get("active_position_id")
    if not position_id:
        return None
    async with AsyncSessionLocal() as db:
        position = await db.get(PaperPosition, int(position_id))
    if position is None or not position.expiry_time:
        return None

    prepared = await _prepare(session, position)
    return {"status": "PRELOAD_ACTIVE", "block": False, "preparation": prepared}
