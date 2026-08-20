from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import aiohttp
from aiohttp import WSMsgType

logger = logging.getLogger("alphapulse.pocketoption.direct")

_SOCKET_PATH = "/socket.io/?EIO=4&transport=websocket"
_DEMO_ENDPOINTS = (
    "wss://demo-api-eu.po.market" + _SOCKET_PATH,
    "wss://try-demo-eu.po.market" + _SOCKET_PATH,
)
_REAL_ENDPOINTS = (
    "wss://api-eu.po.market" + _SOCKET_PATH,
    "wss://api-fr.po.market" + _SOCKET_PATH,
    "wss://api-fr2.po.market" + _SOCKET_PATH,
    "wss://api-us4.po.market" + _SOCKET_PATH,
    "wss://api-us3.po.market" + _SOCKET_PATH,
    "wss://api-us2.po.market" + _SOCKET_PATH,
    "wss://api-us-north.po.market" + _SOCKET_PATH,
    "wss://api-us-south.po.market" + _SOCKET_PATH,
    "wss://api-asia.po.market" + _SOCKET_PATH,
)


class DirectPocketOptionClient:
    """Minimal direct Socket.IO client used for Pocket market data and deal state.

    The base client never sends order messages. Demo order execution is implemented
    only by DirectDemoTradingClient. Every failed/cancelled connection path closes
    its websocket and aiohttp session before returning/raising.
    """

    def __init__(self, ssid: str, is_demo: bool = True):
        self.ssid = (ssid or "").strip()
        self.is_demo = bool(is_demo)
        self.is_connected = False
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._endpoint: str | None = None
        self._lock = asyncio.Lock()
        self._opened_deals: dict[str, dict] = {}
        self._closed_deals: dict[str, dict] = {}
        self._last_deals_event_at: float | None = None

    async def connect(self, persistent: bool = False) -> bool:
        del persistent
        if self.is_connected and self._ws is not None and not self._ws.closed:
            return True

        async with self._lock:
            if self.is_connected and self._ws is not None and not self._ws.closed:
                return True

            await self.disconnect()
            last_error: Exception | None = None
            endpoints = _DEMO_ENDPOINTS if self.is_demo else _REAL_ENDPOINTS

            try:
                for endpoint in endpoints:
                    try:
                        if await self._connect_endpoint(endpoint):
                            return True
                    except asyncio.CancelledError:
                        # asyncio.wait_for cancellation used by Vercel callers must
                        # not leave a ClientSession alive after the function exits.
                        await self.disconnect()
                        raise
                    except Exception as exc:
                        last_error = exc
                        logger.warning(
                            "Pocket direct endpoint failed (%s): %s",
                            endpoint.split("/")[2],
                            type(exc).__name__,
                        )
                        await self.disconnect()
            except asyncio.CancelledError:
                await self.disconnect()
                raise

            await self.disconnect()
            if last_error:
                raise RuntimeError(
                    f"Pocket Option market authentication failed: {type(last_error).__name__}"
                ) from last_error
            return False

    async def _connect_endpoint(self, endpoint: str) -> bool:
        timeout = aiohttp.ClientTimeout(
            total=20, connect=10, sock_connect=10, sock_read=12
        )
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            self._ws = await self._session.ws_connect(
                endpoint,
                origin="https://pocketoption.com",
                ssl=False,
                autoping=True,
                heartbeat=None,
                max_msg_size=8 * 1024 * 1024,
                compress=0,
            )
            self._endpoint = endpoint
            deadline = time.monotonic() + 15.0
            sent_namespace = False
            sent_auth = False

            while time.monotonic() < deadline:
                kind, _payload = await self._recv_packet(
                    max(0.5, deadline - time.monotonic())
                )
                if kind == "engine_open" and not sent_namespace:
                    await self._ws.send_str("40")
                    sent_namespace = True
                elif kind == "namespace_open" and not sent_auth:
                    await self._ws.send_str(self.ssid)
                    sent_auth = True
                elif kind == "successauth":
                    self.is_connected = True
                    logger.info(
                        "Pocket Option direct auth ready (%s)",
                        endpoint.split("/")[2],
                    )
                    return True
                elif kind == "unauthorized":
                    raise RuntimeError("Pocket Option rejected the market session")

            raise asyncio.TimeoutError("Pocket Option successauth was not received")
        except BaseException:
            # Covers TimeoutError, RuntimeError and task cancellation. connect()
            # also calls disconnect, but closing here makes this helper safe if it
            # is ever reused directly.
            await self._close_transport()
            raise

    @staticmethod
    def _deal_id(deal: dict) -> str | None:
        for key in ("id", "uuid", "ticket", "dealId", "deal_id"):
            value = deal.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    @staticmethod
    def _deal_rows(payload: Any) -> list[dict]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("deals", "data", "openedDeals", "closedDeals", "items"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
            if any(k in payload for k in ("id", "uuid", "ticket", "asset")):
                return [payload]
        return []

    def _capture_deal_event(self, event: str, payload: Any) -> None:
        if event not in {"updateOpenedDeals", "updateClosedDeals", "successcloseOrder"}:
            return
        rows = self._deal_rows(payload)
        self._last_deals_event_at = time.time()
        if event == "updateOpenedDeals":
            snapshot: dict[str, dict] = {}
            for row in rows:
                key = self._deal_id(row)
                if key:
                    snapshot[key] = row
            self._opened_deals = snapshot
            return
        for row in rows:
            key = self._deal_id(row)
            if not key:
                continue
            self._opened_deals.pop(key, None)
            self._closed_deals[key] = row

    async def _recv_packet(self, timeout: float) -> tuple[str | None, Any]:
        if self._ws is None:
            raise RuntimeError("Pocket Option websocket is not open")

        while True:
            msg = await asyncio.wait_for(self._ws.receive(), timeout=timeout)
            if msg.type in {WSMsgType.CLOSED, WSMsgType.CLOSE}:
                raise RuntimeError("Pocket Option websocket closed")
            if msg.type == WSMsgType.ERROR:
                raise RuntimeError("Pocket Option websocket error")
            if msg.type == WSMsgType.TEXT:
                text = msg.data
                if text == "2":
                    await self._ws.send_str("3")
                    continue
                if text == "41":
                    return "unauthorized", None
                if text.startswith("0") and "sid" in text:
                    return "engine_open", text
                if text.startswith("40") and "sid" in text:
                    return "namespace_open", text
                if text.startswith("42"):
                    if "NotAuthorized" in text:
                        return "unauthorized", text
                    try:
                        packet = json.loads(text[2:])
                    except Exception:
                        continue
                    if isinstance(packet, list) and packet:
                        event = str(packet[0])
                        payload = packet[1] if len(packet) > 1 else None
                        self._capture_deal_event(event, payload)
                        return event, payload
                if text.startswith("451-"):
                    try:
                        wrapper = json.loads(text.split("-", 1)[1])
                        event = (
                            str(wrapper[0])
                            if isinstance(wrapper, list) and wrapper
                            else "binary"
                        )
                    except Exception:
                        event = "binary"
                    binary = await asyncio.wait_for(
                        self._ws.receive(), timeout=min(timeout, 6.0)
                    )
                    if binary.type not in {WSMsgType.BINARY, WSMsgType.TEXT}:
                        continue
                    raw = binary.data
                    if isinstance(raw, (bytes, bytearray, memoryview)):
                        raw = bytes(raw).decode("utf-8")
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = raw
                    self._capture_deal_event(event, data)
                    return event, data
                continue
            if msg.type == WSMsgType.BINARY:
                try:
                    raw = bytes(msg.data).decode("utf-8")
                    return "binary", json.loads(raw)
                except Exception:
                    continue

    async def get_opened_deals(self, listen_seconds: float = 0.7) -> list[dict]:
        if not await self.connect(persistent=False):
            return []
        if self._ws is None or self._ws.closed:
            return []
        deadline = time.monotonic() + max(0.05, min(float(listen_seconds), 1.5))
        failed = False
        async with self._lock:
            while time.monotonic() < deadline:
                try:
                    await self._recv_packet(
                        min(0.25, max(0.05, deadline - time.monotonic()))
                    )
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    failed = True
                    raise
                except Exception:
                    failed = True
                    break
        if failed:
            await self.disconnect()
        return list(self._opened_deals.values())

    async def get_closed_deals(self, listen_seconds: float = 0.25) -> list[dict]:
        if not await self.connect(persistent=False):
            return []
        if self._ws is None or self._ws.closed:
            return list(self._closed_deals.values())
        deadline = time.monotonic() + max(0.05, min(float(listen_seconds), 1.0))
        failed = False
        async with self._lock:
            while time.monotonic() < deadline:
                try:
                    await self._recv_packet(
                        min(0.2, max(0.05, deadline - time.monotonic()))
                    )
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    failed = True
                    raise
                except Exception:
                    failed = True
                    break
        if failed:
            await self.disconnect()
        return list(self._closed_deals.values())

    async def get_candles(
        self,
        asset: str,
        timeframe: int,
        count: int = 240,
        end_time: int | None = None,
    ):
        if not await self.connect(persistent=False):
            return []
        if self._ws is None or self._ws.closed:
            self.is_connected = False
            if not await self.connect(persistent=False):
                return []

        period = int(timeframe)
        end = int(end_time or time.time())
        if period >= 60:
            end = (end // period) * period
        offset = max(9000, period * max(int(count), 100))
        index = int(time.time() * 100)

        try:
            async with self._lock:
                await self._ws.send_str(
                    "42"
                    + json.dumps(
                        ["changeSymbol", {"asset": asset, "period": period}],
                        separators=(",", ":"),
                    )
                )
                await self._ws.send_str(
                    "42"
                    + json.dumps(
                        [
                            "loadHistoryPeriod",
                            {
                                "asset": asset,
                                "index": index,
                                "offset": offset,
                                "period": period,
                                "time": end,
                            },
                        ],
                        separators=(",", ":"),
                    )
                )
                deadline = time.monotonic() + 18.0
                while time.monotonic() < deadline:
                    event, data = await self._recv_packet(
                        max(0.5, deadline - time.monotonic())
                    )
                    if event == "unauthorized":
                        raise RuntimeError("Pocket Option rejected the market session")
                    if event in {"loadHistoryPeriodFast", "loadHistoryPeriod"}:
                        candles = self._normalize_history(data, period)
                        if candles:
                            return candles[-int(count) :]
                return []
        except asyncio.CancelledError:
            self.is_connected = False
            await self.disconnect()
            raise
        except Exception:
            self.is_connected = False
            await self.disconnect()
            raise

    @staticmethod
    def _normalize_history(payload: Any, period: int) -> list[dict]:
        if isinstance(payload, dict):
            data = (
                payload.get("data")
                or payload.get("candles")
                or payload.get("history")
                or []
            )
        elif isinstance(payload, list):
            data = payload
        else:
            data = []

        direct: list[dict] = []
        ticks: list[tuple[int, float]] = []
        for item in data:
            try:
                if isinstance(item, dict):
                    ts = int(
                        float(
                            item.get(
                                "time", item.get("timestamp", item.get("from"))
                            )
                        )
                    )
                    if ts > 10_000_000_000:
                        ts //= 1000
                    if all(k in item for k in ("open", "high", "low", "close")):
                        direct.append(
                            {
                                "time": ts,
                                "open": float(item["open"]),
                                "high": float(item["high"]),
                                "low": float(item["low"]),
                                "close": float(item["close"]),
                            }
                        )
                    else:
                        price = item.get("price", item.get("close"))
                        if price is not None:
                            ticks.append((ts, float(price)))
                elif isinstance(item, (list, tuple)) and len(item) >= 3:
                    ts = int(float(item[0]))
                    if ts > 10_000_000_000:
                        ts //= 1000
                    if len(item) >= 5:
                        direct.append(
                            {
                                "time": ts,
                                "open": float(item[1]),
                                "close": float(item[2]),
                                "high": float(item[3]),
                                "low": float(item[4]),
                            }
                        )
                    else:
                        ticks.append((ts, float(item[2])))
            except Exception:
                continue

        if direct:
            direct.sort(key=lambda candle: candle["time"])
            unique = {candle["time"]: candle for candle in direct}
            return [unique[key] for key in sorted(unique)]

        buckets: dict[int, list[float]] = {}
        for ts, value in sorted(ticks):
            buckets.setdefault(ts - (ts % period), []).append(value)
        return [
            {
                "time": ts,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
            }
            for ts, prices in sorted(buckets.items())
        ]

    async def _close_transport(self) -> None:
        self.is_connected = False
        ws, self._ws = self._ws, None
        session, self._session = self._session, None

        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except BaseException:
                pass
        if session is not None and not session.closed:
            try:
                await session.close()
            except BaseException:
                pass
        self._endpoint = None

    async def disconnect(self) -> None:
        await self._close_transport()
