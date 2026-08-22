# Install the persistent Pocket Option transport patch before service modules are used.
from backend.services import pocket_persistent_patch as _pocket_persistent_patch  # noqa: F401

# Keep AUTO scan status/journal live and make the 92% payout gate explicit in UI text.
from backend.services import auto_session_live_patch as _auto_session_live_patch  # noqa: F401

# Enable Smart Confluence in both AUTO modes.
from backend.services import smart_confluence_patch as _smart_confluence_patch  # noqa: F401

# The public Mini App no longer needs an inbound trycloudflare tunnel. When the
# persistent Windows runtime imports backend.services it opens one outbound
# WebSocket to the Cloudflare relay and forwards authenticated API requests to
# the local 127.0.0.1 gateway. The task is cancelled automatically with the
# worker event loop during shutdown.
import asyncio as _asyncio
import os as _os

_cloudflare_bridge_task = None
_cloudflare_bridge_stop = None
if str(_os.getenv("APP_RUNTIME_ROLE") or "").strip().lower() == "worker":
    try:
        from worker.cloudflare_bridge import run_cloudflare_bridge as _run_cloudflare_bridge

        _loop = _asyncio.get_running_loop()
        _cloudflare_bridge_stop = _asyncio.Event()
        _cloudflare_bridge_task = _loop.create_task(
            _run_cloudflare_bridge(_cloudflare_bridge_stop),
            name="alphapulse-cloudflare-outbound-bridge",
        )
    except RuntimeError:
        # Imports outside the running worker loop (tests/build tooling) stay inert.
        pass
