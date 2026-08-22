from __future__ import annotations

import time
from typing import Any

from backend.services import session_engine as _engine
from backend.services.trade_runtime import update_trade_runtime

_original_session_tick = _engine.session_tick
_last_journal_at: dict[int, float] = {}


def _clean_message(message: str, *, min_payout: float) -> str:
    text = str(message or "")
    if "подтверждённого сетапа ≥" in text and "пока нет" in text:
        prefix = text.split(" · подтверждённого сетапа ≥", 1)[0]
        return f"{prefix} · payout ≥{min_payout:g}% · подтверждённого сигнала пока нет"
    return text


async def _patched_session_tick() -> dict[str, Any]:
    result = await _original_session_tick()
    if not isinstance(result, dict):
        return result

    status = str(result.get("status") or "").upper()
    if status not in {"SCANNING", "WAIT_PAYOUT", "DUPLICATE"}:
        return result

    session = await _engine._active()
    if not session:
        return result

    sid = int(session["id"])
    min_payout = float(result.get("min_payout") or _engine.MIN_AUTO_PAYOUT)
    message = _clean_message(str(session.get("last_message") or ""), min_payout=min_payout)

    if message != str(session.get("last_message") or ""):
        await _engine._update(sid, stage="SCANNING", last_message=message)
        await update_trade_runtime(stage="SCANNING", message=message, min_payout=min_payout)

    scanned = int(result.get("scanned") or 0)
    eligible = int(result.get("eligible") or 0)
    if scanned > 0:
        now = time.monotonic()
        last = _last_journal_at.get(sid, 0.0)
        # One journal row per completed scan cycle, without flooding the DB when
        # the realtime driver performs fast non-scan ticks between cycles.
        if now - last >= 2.0:
            _last_journal_at[sid] = now
            await _engine._event(
                sid,
                "SCANNING",
                f"Скан завершён · {scanned}/{scanned} пар с payout ≥{min_payout:g}% проверено · подтверждённого сигнала пока нет",
                {
                    "scanned": scanned,
                    "eligible": eligible,
                    "min_payout": min_payout,
                    "strategy": session.get("strategy"),
                    "timeframe": session.get("timeframe"),
                },
            )
    return result


if getattr(_engine.session_tick, "__name__", "") != "_patched_session_tick":
    _engine.session_tick = _patched_session_tick
