from __future__ import annotations

from backend.models.db_models import utcnow
from backend.services.auto_scan_scope import get_auto_scan_scope, set_auto_scan_scope
from backend.services.control import get_control
from backend.services.scanner import _vip_tick
from backend.services.signal_engine import signal_engine

_original_scan_vip = signal_engine.scan_vip


async def _scan_vip_full_market(assets):
    """Run VIP against the full requested OTC universe, never AUTO's payout scope."""
    previous_assets, previous_count = get_auto_scan_scope()
    set_auto_scan_scope([], 0)
    try:
        return await _original_scan_vip(assets)
    finally:
        set_auto_scan_scope(previous_assets, previous_count)


# All callers of scanner._vip_tick use the same singleton, so patch once here.
signal_engine.scan_vip = _scan_vip_full_market


async def run_due_vip(bot):
    control = await get_control()
    if control is None:
        return {"status": "NO_CONTROL"}
    return await _vip_tick(bot, control, utcnow())
