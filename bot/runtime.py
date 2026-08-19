from __future__ import annotations

import hashlib
import hmac
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update

from bot.handlers import admin, start, subscription
from config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()
bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dispatcher = Dispatcher()
dispatcher.include_router(start.router)
dispatcher.include_router(admin.router)
dispatcher.include_router(subscription.router)


def webhook_secret() -> str:
    return hashlib.sha256(f'alphapulse-webhook:{settings.bot_token}'.encode()).hexdigest()


def valid_webhook_secret(value: str | None) -> bool:
    return bool(value) and hmac.compare_digest(value, webhook_secret())


async def configure_webhook() -> str | None:
    base = settings.effective_public_api_base_url
    if not base.startswith('https://'):
        return None
    url = f"{base.rstrip('/')}/telegram/webhook"
    await bot.set_webhook(url=url, secret_token=webhook_secret(), drop_pending_updates=False)
    logger.info('Telegram webhook configured: %s', url)
    return url


async def feed_update(payload: dict) -> None:
    update = Update.model_validate(payload, context={'bot': bot})
    await dispatcher.feed_update(bot, update)
