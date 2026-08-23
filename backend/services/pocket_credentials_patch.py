from __future__ import annotations

import os

from backend.services import pocketoption_otc as _pocket
from backend.services.pocket_credentials import load_pocket_credential
from backend.services.trade_mode import get_trade_account_mode

_ORIGINAL_REFRESH = _pocket.PocketOptionOTCService._refresh_private_ssid


async def _refresh_private_ssid(self, force: bool = False) -> None:
    if str(getattr(self, '_runtime_role', '') or '').strip().lower() != 'worker':
        await _ORIGINAL_REFRESH(self)
        return

    mode = str(await get_trade_account_mode() or 'demo').strip().lower()
    if mode not in {'demo', 'real'}:
        mode = 'demo'
    stored = await load_pocket_credential(mode)
    fallback_name = 'POCKET_OPTION_SSID' if mode == 'demo' else 'POCKET_OPTION_REAL_SSID'
    desired = str(stored or os.getenv(fallback_name) or '').strip()
    desired_demo = mode == 'demo'

    changed = desired != str(getattr(self, 'ssid', '') or '') or bool(getattr(self, 'demo', True)) != desired_demo
    if force or changed:
        # close() must be used here: the persistent transport patch intentionally
        # keeps a healthy socket alive when ordinary candle retries call _drop_client().
        await self.close()
        self.demo = desired_demo
        self._apply_ssid(desired)
        # Account mode is authoritative even when the wire frame omits isDemo/currentUrl.
        self.demo = desired_demo
    self._private_secret_loaded = True


_pocket.PocketOptionOTCService._refresh_private_ssid = _refresh_private_ssid
