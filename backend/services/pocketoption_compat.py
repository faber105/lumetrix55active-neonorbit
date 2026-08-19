from __future__ import annotations

import asyncio
import json
import logging

from pocketoptionapi_async.client import AsyncPocketOptionClient
from pocketoptionapi_async.exceptions import AuthenticationError
from pocketoptionapi_async.websocket_client import AsyncWebSocketClient

logger = logging.getLogger("alphapulse.pocketoption.compat")


def install() -> None:
    """Patch pocketoptionapi-async 2.0.1 auth handling without touching trade methods.

    The upstream client can lose a very fast `42["successauth", ...]` event because
    the receive loop starts before `_wait_for_authentication()` installs its
    temporary handler. It also does not route normal Socket.IO `42` packets through
    `_handle_json_message()` consistently. We retain an auth state flag and make
    authentication waiting race-free.
    """
    if getattr(AsyncWebSocketClient, "_alphapulse_compat_installed", False):
        return

    original_process = AsyncWebSocketClient._process_message

    async def patched_process(self, message):
        text = message
        if isinstance(text, (bytes, bytearray, memoryview)):
            try:
                text = bytes(text).decode("utf-8")
            except Exception:
                text = None

        if isinstance(text, str) and text.startswith("42"):
            if "NotAuthorized" in text:
                self._alphapulse_auth_error = "NotAuthorized"
            else:
                try:
                    packet = json.loads(text[2:])
                except Exception:
                    packet = None
                if isinstance(packet, list) and packet:
                    event_type = str(packet[0])
                    if event_type == "successauth":
                        self._alphapulse_authenticated = True
                    # Route regular Socket.IO events to the library's own event
                    # dispatcher. This keeps candle/history parsing upstream.
                    await self._handle_json_message(packet)
                    return

        await original_process(self, message)

    async def patched_wait_for_authentication(self, timeout: float = 10.0) -> None:
        ws = self._websocket
        if getattr(ws, "_alphapulse_authenticated", False):
            return
        if getattr(ws, "_alphapulse_auth_error", None):
            raise AuthenticationError("Authentication failed: Pocket Option rejected the session")

        event = asyncio.Event()
        state = {"error": None}

        def on_auth(_data):
            ws._alphapulse_authenticated = True
            event.set()

        def on_auth_error(data):
            state["error"] = (data or {}).get("message", "Pocket Option rejected the session")
            ws._alphapulse_auth_error = state["error"]
            event.set()

        ws.add_event_handler("authenticated", on_auth)
        ws.add_event_handler("auth_error", on_auth_error)
        try:
            # Re-check after handlers are installed, closing the race window.
            if getattr(ws, "_alphapulse_authenticated", False):
                return
            if getattr(ws, "_alphapulse_auth_error", None):
                raise AuthenticationError("Authentication failed: Pocket Option rejected the session")
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise AuthenticationError(
                    "Authentication timeout - no successauth event was received"
                ) from exc
            if state["error"] or getattr(ws, "_alphapulse_auth_error", None):
                raise AuthenticationError(
                    f"Authentication failed: {state['error'] or 'Pocket Option rejected the session'}"
                )
            if not getattr(ws, "_alphapulse_authenticated", False):
                raise AuthenticationError("Authentication failed: successauth was not confirmed")
        finally:
            ws.remove_event_handler("authenticated", on_auth)
            ws.remove_event_handler("auth_error", on_auth_error)

    AsyncWebSocketClient._process_message = patched_process
    AsyncPocketOptionClient._wait_for_authentication = patched_wait_for_authentication
    AsyncWebSocketClient._alphapulse_compat_installed = True
    logger.info("Pocket Option Socket.IO authentication compatibility patch active")


install()
