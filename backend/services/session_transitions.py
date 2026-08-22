from __future__ import annotations

from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def loss_transition(session, *, amount, failed, level, series_loss):
    """Return the next session state after a confirmed losing position.

    A fully lost martingale chain is a failed series in every AUTO mode. The
    cover limit is therefore a hard stop once ``max_failed_series`` is reached.
    Martingale is session data (``current_level``), not a durable state by
    itself, so the engine returns to SCANNING for the next confirmed setup.
    """
    series_loss += amount
    max_martingale = int(session["max_martingale"])
    if level < max_martingale:
        level += 1
        return {
            "failed": failed,
            "level": level,
            "series_loss": series_loss,
            "status": "ACTIVE",
            "stage": "SCANNING",
            "reason": None,
            "ended": None,
            "message": (
                f"LOSS · готовлю перекрытие {level}/{max_martingale} "
                "только на новом подтверждённом сетапе"
            ),
        }

    failed += 1
    if failed >= int(session["max_failed_series"]):
        return {
            "failed": failed,
            "level": 0,
            "series_loss": 0,
            "status": "STOPPED",
            "stage": "STOPPED",
            "reason": "MAX_FAILED_SERIES",
            "ended": _utcnow(),
            "message": (
                f"Сессия завершена · проиграны все перекрытия "
                f"({max_martingale}/{max_martingale})"
            ),
        }

    return {
        "failed": failed,
        "level": 0,
        "series_loss": 0,
        "status": "ACTIVE",
        "stage": "SCANNING",
        "reason": None,
        "ended": None,
        "message": "Полная минусовая серия учтена · анализирую пары с payout ≥92% дальше",
    }


def settle_transition(session, *, result, amount, payout):
    """Calculate one deterministic broker-result transition.

    This function has no I/O. The caller must persist its output together with
    the leg result in one transaction while holding the session row lock.
    """
    result = str(result).upper()
    if result not in {"WIN", "LOSS", "DRAW"}:
        raise ValueError(f"Unsupported broker result: {result}")

    amount = float(amount)
    payout = float(payout)
    wins = int(session.get("wins") or 0)
    failed = int(session.get("failed_series") or 0)
    level = int(session.get("current_level") or 0)
    series_loss = float(session.get("current_series_loss") or 0)
    pnl = amount * payout / 100 if result == "WIN" else (-amount if result == "LOSS" else 0.0)
    profit = round(float(session.get("profit") or 0) + pnl, 2)
    status, stage, reason, ended = "ACTIVE", "SCANNING", None, None

    if result == "WIN":
        wins += 1
        level, series_loss = 0, 0.0
        message = f"WIN +{pnl:.2f} · анализирую следующий подтверждённый сетап"
        if session.get("mode") == "count" and wins >= int(session["target_wins"]):
            status, stage, reason, ended = "COMPLETED", "COMPLETED", "TARGET_WINS", _utcnow()
            message = "Цель по успешным сделкам достигнута"
        elif session.get("mode") == "profit" and profit >= float(session["target_profit"]):
            status, stage, reason, ended = "COMPLETED", "COMPLETED", "TARGET_PROFIT", _utcnow()
            message = "Целевой профит достигнут"
    elif result == "LOSS":
        loss = loss_transition(
            session,
            amount=amount,
            failed=failed,
            level=level,
            series_loss=series_loss,
        )
        failed = loss["failed"]
        level = loss["level"]
        series_loss = loss["series_loss"]
        status = loss["status"]
        stage = loss["stage"]
        reason = loss["reason"]
        ended = loss["ended"]
        message = loss["message"]
    else:
        # DRAW returns the stake and explicitly retries the same level; it is
        # neither a win nor a loss and does not change targets or series limits.
        message = "DRAW · повторяю текущий уровень на следующем подтверждённом сетапе"

    return {
        "result": result,
        "pnl": round(pnl, 2),
        "profit": profit,
        "wins": wins,
        "failed": failed,
        "level": level,
        "series_loss": series_loss,
        "status": status,
        "stage": stage,
        "reason": reason,
        "ended": ended,
        "message": message,
    }
