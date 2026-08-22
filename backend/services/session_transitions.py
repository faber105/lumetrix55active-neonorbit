from __future__ import annotations

from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def loss_transition(session, *, amount, failed, level, series_loss):
    """Return the next session state after a confirmed losing position.

    A fully lost martingale chain is a failed series in every AUTO mode. The
    cover limit is therefore a hard stop once ``max_failed_series`` is reached.
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
            "stage": "MARTINGALE",
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
            "reason": "FAILED_SERIES_LIMIT",
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
