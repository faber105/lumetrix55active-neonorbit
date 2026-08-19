from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Bot
from sqlalchemy import select

from api.models.database import AsyncSessionLocal
from api.models.signal import Signal
from config import get_settings
from signal_engine.online_ml import online_model
from signal_engine.otc_engine import OTCSignalEngine
from signal_engine.otc_provider import PocketOptionOTCProvider, price_at_entry, price_at_or_before
from signal_engine.publisher import publish_analysis

logger = logging.getLogger(__name__)


class OTCScanner:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.settings = get_settings()
        self.provider = PocketOptionOTCProvider()
        self.engine = OTCSignalEngine()
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()
        await self.provider.close()

    async def scan_once(self) -> int:
        if not self.settings.pocket_option_ssid.strip():
            logger.warning('OTC scanner disabled: POCKET_OPTION_SSID is empty')
            return 0
        semaphore = asyncio.Semaphore(2)
        async def analyze_asset(asset: str) -> int:
            async with semaphore:
                try:
                    base = await self.provider.fetch_1m(asset, count=360)
                    analyses = self.engine.analyze_all_timeframes(asset, base)
                    published = 0
                    for analysis in analyses:
                        if analysis.status == 'SIGNAL' and await publish_analysis(self.bot, analysis):
                            published += 1
                    return published
                except Exception as exc:
                    logger.warning('OTC scan failed for %s: %s', asset, exc)
                    return 0
        results = await asyncio.gather(*(analyze_asset(asset) for asset in self.settings.parsed_otc_assets))
        return sum(results)

    async def resolve_expired(self) -> int:
        now = datetime.utcnow()
        async with AsyncSessionLocal() as db:
            pending = list((await db.scalars(select(Signal).where(Signal.result == 'PENDING', Signal.expires_at <= now - timedelta(seconds=5)).order_by(Signal.expires_at).limit(50))).all())
        if not pending:
            return 0
        by_asset: dict[str, list[Signal]] = {}
        for signal in pending:
            by_asset.setdefault(signal.asset, []).append(signal)
        resolved = 0
        for asset, signals in by_asset.items():
            try:
                frame = await self.provider.fetch_1m(asset, count=360)
            except Exception as exc:
                logger.warning('Cannot resolve %s signals for %s: %s', len(signals), asset, exc)
                continue
            async with AsyncSessionLocal() as db:
                for stale in signals:
                    signal = await db.get(Signal, stale.id)
                    if signal is None or signal.result != 'PENDING':
                        continue
                    entry_price = price_at_entry(frame, signal.entry_time)
                    close_price = price_at_or_before(frame, signal.expires_at)
                    if entry_price is None or close_price is None:
                        continue
                    if abs(close_price - entry_price) < 1e-12:
                        signal.result = 'DRAW'
                    else:
                        went_up = close_price > entry_price
                        is_win = (signal.direction == 'CALL' and went_up) or (signal.direction == 'PUT' and not went_up)
                        signal.result = 'WIN' if is_win else 'LOSS'
                        online_model.learn(signal.feature_snapshot or {}, went_up=went_up)
                    signal.open_price = Decimal(str(entry_price))
                    signal.close_price = Decimal(str(close_price))
                    resolved += 1
                await db.commit()
        return resolved

    async def run_forever(self) -> None:
        logger.info('OTC scanner task started: assets=%s timeframes=%s', self.settings.parsed_otc_assets, self.settings.parsed_otc_timeframes)
        while not self._stop.is_set():
            started = asyncio.get_running_loop().time()
            try:
                await self.resolve_expired()
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('OTC scanner iteration failed')
            elapsed = asyncio.get_running_loop().time() - started
            delay = max(1.0, self.settings.scan_interval_seconds - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
