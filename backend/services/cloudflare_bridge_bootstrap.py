from __future__ import annotations

import asyncio
import logging

from worker.cloudflare_bridge import run_cloudflare_bridge

logger = logging.getLogger("alphapulse.cloudflare_bridge_bootstrap")
_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


def ensure_cloudflare_bridge_started() -> None:
    global _task, _stop_event
    if _task is not None and not _task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("Cloudflare bridge bootstrap skipped: no running event loop")
        return
    _stop_event = asyncio.Event()
    _task = loop.create_task(run_cloudflare_bridge(_stop_event), name="alphapulse-cloudflare-bridge")
    logger.info("Cloudflare outbound bridge task started")


ensure_cloudflare_bridge_started()
