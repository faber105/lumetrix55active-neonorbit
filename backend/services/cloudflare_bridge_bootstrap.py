from __future__ import annotations

import asyncio
import logging

from worker.cloudflare_bridge import run_cloudflare_bridge

logger = logging.getLogger("alphapulse.cloudflare_bridge_bootstrap")
_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


def ensure_cloudflare_bridge_started() -> asyncio.Task | None:
    """Legacy explicit bootstrap helper.

    worker.main owns the production relay lifecycle. This helper remains for
    compatibility with older imports but never starts anything merely because
    the module was imported.
    """
    global _task, _stop_event
    if _task is not None and not _task.done():
        return _task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("Cloudflare bridge bootstrap skipped: no running event loop")
        return None
    _stop_event = asyncio.Event()
    _task = loop.create_task(run_cloudflare_bridge(_stop_event), name="alphapulse-cloudflare-bridge-legacy")
    logger.info("Cloudflare outbound bridge task started explicitly")
    return _task
