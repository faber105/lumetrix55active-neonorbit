import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from api.models.database import init_db
from bot.handlers import admin, start, subscription
from config import get_settings
from signal_engine.otc_scanner import OTCScanner

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(start.router)
    dispatcher.include_router(admin.router)
    dispatcher.include_router(subscription.router)

    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)

    scanner = OTCScanner(bot)
    scanner_task = asyncio.create_task(scanner.run_forever(), name='otc-scanner')
    logger.info('Starting %s unified bot polling', settings.project_name)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await scanner.stop()
        scanner_task.cancel()
        await asyncio.gather(scanner_task, return_exceptions=True)
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
