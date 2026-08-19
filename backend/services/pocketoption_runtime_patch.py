"""Runtime compatibility fixes for pocketoptionapi-async 2.0.1.

The upstream client starts its websocket receiver before registering the temporary
`authenticated` waiter. On a fast connection Pocket Option can answer with
`42["successauth", ...]` in that gap, so the client later times out even though
the broker accepted the session. We persist auth state on the websocket and make
the waiter race-safe. This module only affects connection/message handling; it
never opens orders or performs trading actions.
"""
from __future__ import annotations

import asyncio
import json

from pocketoptionapi_async.client import AsyncPocketOptionClient
from pocketoptionapi_async.exceptions import AuthenticationError
from pocketoptionapi_async.websocket_client import AsyncWebSocketClient


if not getattr(AsyncWebSocketClient, "_alphapulse_class_patch", False):
    _original_process_message = AsyncWebSocketClient._process_message

    async def _patched_process_message(self, message):
        text = message
        if isinstance(text, (bytes, bytearray, memoryview)):
            try:
                text = bytes(text).decode("utf-8")
            except Exception:
                text = None

        if isinstance(text, str) and text.startswith("42"):
            if "NotAuthorized" in text:
                self._alphapulse_auth_error = "Invalid or expired Pocket Option session"
                await self._emit_event("auth_error", {"message": self._alphapulse_auth_error})
                return
            try:
                packet = json.loads(text[2:])
            except Exception:
                packet = None
            if isinstance(packet, list) and packet:
                event = str(packet[0])
                if event == "successauth":
                    self._alphapulse_authenticated = True
                    self._alphapulse_auth_error = None
                # The upstream 2.0.1 processor ignores normal 42 Socket.IO
                # events other than NotAuthorized. Route them through its own
                # JSON-event dispatcher so auth/history/stream events work.
                await self._handle_json_message(packet)
                return

        await _original_process_message(self, message)

    AsyncWebSocketClient._process_message = _patched_process_message
    AsyncWebSocketClient._alphapulse_class_patch = True


if not getattr(AsyncPocketOptionClient, "_alphapulse_auth_wait_patch", False):
    async def _race_safe_wait_for_authentication(self, timeout: float = 12.0) -> None:
        websocket = self._websocket
        if getattr(websocket, "_alphapulse_authenticated", False):
            return
        existing_error = getattr(websocket, "_alphapulse_auth_error", None)
        if existing_error:
            raise AuthenticationError(existing_error)

        completed = asyncio.Event()
        state = {"error": None}

        def on_auth(data):
            websocket._alphapulse_authenticated = True
            state["error"] = None
            completed.set()

        def on_auth_error(data):
            state["error"] = (data or {}).get("message", "Pocket Option authentication failed")
            websocket._alphapulse_auth_error = state["error"]
            completed.set()

        websocket.add_event_handler("authenticated", on_auth)
        websocket.add_event_handler("auth_error", on_auth_error)
        try:
            # Close the race where successauth arrived between the first check
            # above and handler registration.
            if getattr(websocket, "_alphapulse_authenticated", False):
                return
            existing_error = getattr(websocket, "_alphapulse_auth_error", None)
            if existing_error:
                raise AuthenticationError(existing_error)
            try:
                await asyncio.wait_for(completed.wait(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise AuthenticationError(
                    "Pocket Option authentication timeout: no successauth/NotAuthorized event received"
                ) from exc
            if state["error"]:
                raise AuthenticationError(state["error"])
        finally:
            websocket.remove_event_handler("authenticated", on_auth)
            websocket.remove_event_handler("auth_error", on_auth_error)

    AsyncPocketOptionClient._wait_for_authentication = _race_safe_wait_for_authentication
    AsyncPocketOptionClient._alphapulse_auth_wait_patch = True
