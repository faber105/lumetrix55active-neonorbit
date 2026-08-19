"""AlphaPulse backend package runtime compatibility patches."""
from __future__ import annotations

import asyncio


def _patch_pocketoption_auth_waiter() -> None:
    """Fix auth-event race in pocketoptionapi-async 2.0.1.

    The upstream client starts the WebSocket receiver and only afterwards adds
    temporary authentication handlers. Pocket Option can answer successauth
    immediately, so the response may be consumed before the waiter subscribes.
    We attach the waiter during client construction, before connect() sends auth.
    """
    try:
        from pocketoptionapi_async.client import AsyncPocketOptionClient
        from pocketoptionapi_async.exceptions import AuthenticationError
    except Exception:
        return

    if getattr(AsyncPocketOptionClient, '_alphapulse_auth_wait_patch', False):
        return

    original_init = AsyncPocketOptionClient.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._alphapulse_auth_event = asyncio.Event()
        self._alphapulse_auth_error = None

        def on_auth(_data):
            self._alphapulse_auth_event.set()

        def on_auth_error(data):
            if isinstance(data, dict):
                self._alphapulse_auth_error = data.get('message') or 'Pocket Option rejected authentication'
            else:
                self._alphapulse_auth_error = 'Pocket Option rejected authentication'
            self._alphapulse_auth_event.set()

        self._websocket.add_event_handler('authenticated', on_auth)
        self._websocket.add_event_handler('auth_error', on_auth_error)

    async def patched_wait_for_authentication(self, timeout: float = 10.0) -> None:
        try:
            await asyncio.wait_for(self._alphapulse_auth_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise AuthenticationError('Authentication timeout - Pocket Option did not confirm auth') from exc
        if self._alphapulse_auth_error:
            raise AuthenticationError(self._alphapulse_auth_error)

    AsyncPocketOptionClient.__init__ = patched_init
    AsyncPocketOptionClient._wait_for_authentication = patched_wait_for_authentication
    AsyncPocketOptionClient._alphapulse_auth_wait_patch = True


_patch_pocketoption_auth_waiter()
