from __future__ import annotations
import asyncio, json, logging, time
logger = logging.getLogger("alphapulse.pocketoption.persistence")
def _install():
    from backend.services.pocket_direct import DirectPocketOptionClient
    from backend.services.pocketoption_otc import PocketOptionOTCService
    if getattr(DirectPocketOptionClient, "_alphapulse_persistent_patch", False):
        return
    original_drop_client = PocketOptionOTCService._drop_client
    async def persistent_get_candles(self, asset: str, timeframe: int, count: int = 240, end_time: int | None = None):
        if not await self.connect(persistent=True):
            return []
        if self._ws is None or self._ws.closed:
            self.is_connected = False
            if not await self.connect(persistent=True):
                return []
        period = int(timeframe)
        end = int(end_time or time.time())
        if period >= 60:
            end = (end // period) * period
        offset = max(9000, period * max(int(count), 100))
        index = int(time.time() * 100)
        async with self._lock:
            if self._ws is None or self._ws.closed:
                self.is_connected = False
                raise RuntimeError("Pocket Option websocket closed")
            try:
                await self._ws.send_str("42" + json.dumps(["changeSymbol", {"asset": asset, "period": period}], separators=(",", ":")))
                await self._ws.send_str("42" + json.dumps(["loadHistoryPeriod", {"asset": asset, "index": index, "offset": offset, "period": period, "time": end}], separators=(",", ":")))
                deadline = time.monotonic() + 12.0
                while time.monotonic() < deadline:
                    remaining = max(0.15, deadline - time.monotonic())
                    try:
                        event, data = await self._recv_packet(min(1.5, remaining))
                    except asyncio.TimeoutError:
                        continue
                    if event == "unauthorized":
                        self.is_connected = False
                        await self.disconnect()
                        raise RuntimeError("Pocket Option rejected the market session")
                    if event in {"loadHistoryPeriodFast", "loadHistoryPeriod"}:
                        candles = self._normalize_history(data, period)
                        if candles:
                            return candles[-int(count):]
                logger.debug("Pocket history response timed out on healthy stream (%s/%ss)", asset, period)
                return []
            except asyncio.CancelledError:
                if self._ws is None or self._ws.closed:
                    self.is_connected = False
                    await self.disconnect()
                raise
            except Exception:
                if self._ws is None or self._ws.closed:
                    self.is_connected = False
                    await self.disconnect()
                raise
    async def preserve_healthy_drop(self):
        if getattr(self, "_runtime_role", "") != "worker":
            await original_drop_client(self)
            return
        client = self._client
        if client is None:
            return
        ws = getattr(client, "_ws", None)
        connected = bool(getattr(client, "is_connected", False))
        if connected and ws is not None and not ws.closed:
            return
        old, self._client = self._client, None
        if old is not None:
            try:
                await old.disconnect()
            except Exception:
                pass
    DirectPocketOptionClient.get_candles = persistent_get_candles
    DirectPocketOptionClient._alphapulse_persistent_patch = True
    PocketOptionOTCService._drop_client = preserve_healthy_drop
    logger.info("Persistent Pocket Option transport patch installed")
_install()
