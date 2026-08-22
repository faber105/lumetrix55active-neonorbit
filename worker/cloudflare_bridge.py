from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("alphapulse.cloudflare_bridge")

DEFAULT_RELAY_URL = "https://lumetrix55active-neonorbit.onerfaber.workers.dev"


def _ws_url() -> str:
    base = str(os.getenv("CLOUDFLARE_RELAY_URL") or DEFAULT_RELAY_URL).strip().rstrip("/")
    parsed = urlparse(base)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/__bridge/connect"


async def _handle_request(http: aiohttp.ClientSession, ws: aiohttp.ClientWebSocketResponse, payload: dict) -> None:
    req_id = str(payload.get("id") or "")
    if not req_id:
        return
    method = str(payload.get("method") or "GET").upper()
    path = str(payload.get("path") or "/")
    if not path.startswith("/"):
        path = "/" + path
    headers = {
        str(k): str(v)
        for k, v in (payload.get("headers") or {}).items()
        if str(k).lower() in {"content-type", "x-telegram-init-data", "x-idempotency-key"}
    }
    body = payload.get("body")
    url = f"http://127.0.0.1:{int(os.getenv('WORKER_HTTP_PORT') or 8765)}{path}"
    try:
        async with http.request(
            method,
            url,
            headers=headers,
            data=None if method in {"GET", "HEAD"} else ("" if body is None else str(body)),
            timeout=aiohttp.ClientTimeout(total=8.0),
        ) as response:
            text = await response.text()
            response_headers = {}
            content_type = response.headers.get("content-type")
            if content_type:
                response_headers["content-type"] = content_type
            message = {
                "type": "response",
                "id": req_id,
                "status": int(response.status),
                "headers": response_headers,
                "body": text,
            }
    except Exception as exc:
        message = {
            "type": "response",
            "id": req_id,
            "status": 502,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"detail": f"Local gateway error: {type(exc).__name__}"}),
        }
    try:
        await ws.send_json(message)
    except Exception:
        pass


async def run_cloudflare_bridge(stop_event: asyncio.Event) -> None:
    secret = str(os.getenv("WORKER_SHARED_SECRET") or "").strip()
    if not secret:
        logger.warning("Cloudflare bridge disabled: WORKER_SHARED_SECRET is empty")
        return

    delay = 1.0
    connector = aiohttp.TCPConnector(limit=24, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as http:
        while not stop_event.is_set():
            try:
                async with http.ws_connect(
                    _ws_url(),
                    headers={"Authorization": f"Bearer {secret}"},
                    heartbeat=20,
                    receive_timeout=45,
                    timeout=aiohttp.ClientTimeout(total=10),
                    max_msg_size=2 * 1024 * 1024,
                ) as ws:
                    logger.info("Cloudflare outbound bridge connected")
                    delay = 1.0
                    async for message in ws:
                        if stop_event.is_set():
                            break
                        if message.type == aiohttp.WSMsgType.TEXT:
                            try:
                                payload = json.loads(message.data)
                            except Exception:
                                continue
                            if payload.get("type") == "request":
                                asyncio.create_task(_handle_request(http, ws, payload))
                        elif message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Cloudflare bridge reconnect after %s", type(exc).__name__)
            if stop_event.is_set():
                break
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(15.0, delay * 1.7)
