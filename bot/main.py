from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, TelegramObject, Update, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from backend.models.db_models import AsyncSessionLocal, MLState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alphapulse.bot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BACKEND_URL = os.getenv("BACKEND_URL", "https://alphapulse-otc.vercel.app").rstrip("/")
MINI_APP_URL = os.getenv("MINI_APP_URL", BACKEND_URL)
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "change_me")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
MANAGER = os.getenv("MANAGER_USERNAME", "@alphapulse_manager")
MIN_CLICK = int(os.getenv("MIN_CLICK_SECONDS", "120"))
MAX_ATTEMPTS = int(os.getenv("MAX_VERIFY_ATTEMPTS", "3"))

rate_data: Dict[int, list[float]] = defaultdict(list)
blocked: Dict[int, float] = {}


def is_rate_limited(telegram_id: int) -> bool:
    if telegram_id == ADMIN_ID:
        return False
    now = time.time()
    if telegram_id in blocked:
        if now - blocked[telegram_id] < 120:
            return True
        blocked.pop(telegram_id, None)
    rate_data[telegram_id] = [x for x in rate_data[telegram_id] if now - x < 10]
    if len(rate_data[telegram_id]) >= 6:
        blocked[telegram_id] = now
        return True
    rate_data[telegram_id].append(now)
    return False


class AntiSpam(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: Dict[str, Any]) -> Any:
        user = getattr(event, "from_user", None) or getattr(getattr(event, "message", None), "from_user", None)
        if user and is_rate_limited(user.id):
            return None
        return await handler(event, data)


bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
dp.message.middleware(AntiSpam())
dp.callback_query.middleware(AntiSpam())


async def api(method: str, path: str, **kwargs):
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, BACKEND_URL + path, **kwargs) as response:
                body = await response.json(content_type=None)
                return body if response.status < 400 else None
    except Exception as exc:
        logger.warning("API %s %s failed: %s", method, path, exc)
        return None


async def get_user(telegram_id: int): return await api("GET", f"/api/auth/user/{telegram_id}")
async def create_user(telegram_id: int, username: str, full_name: str): return await api("POST", "/api/auth/user", json={"telegram_id": telegram_id, "username": username, "full_name": full_name})
async def set_status(telegram_id: int, status: str) -> bool:
    data = await api("POST", "/api/auth/status", json={"telegram_id": telegram_id, "status": status, "secret": ADMIN_SECRET})
    return bool(data and data.get("ok"))


async def get_user_timezone(telegram_id: int):
    try:
        async with AsyncSessionLocal() as db:
            state = await db.get(MLState, f"__user_tz__:{int(telegram_id)}")
        payload = json.loads(state.payload or "{}") if state else {}
    except Exception:
        payload = {}
    name = str(payload.get("name") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    try:
        minutes = max(-840, min(840, int(payload.get("offset_minutes") or 0)))
    except Exception:
        minutes = 0
    return timezone(timedelta(minutes=minutes))


def local_signal_time(value: str | None, tz) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).strftime("%H:%M:%S")
    except Exception:
        return "—"

async def verified_or_reply(message: types.Message) -> bool:
    user = await get_user(message.from_user.id)
    if not user or user.get("status") != "VERIFIED":
        await message.answer("Доступ ограничен. Напишите /start для регистрации.")
        return False
    return True


def kb_register(telegram_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Зарегистрироваться у брокера", url=f"{BACKEND_URL}/api/auth/go?uid={telegram_id}"))
    builder.row(InlineKeyboardButton(text="✅ Я зарегистрировался", callback_data="verify:i_registered"))
    builder.row(InlineKeyboardButton(text="Написать менеджеру", callback_data="support"))
    return builder.as_markup()

def kb_main():
    keyboard = ReplyKeyboardBuilder()
    for text in ["VIP Сигналы", "Настройки", "Поддержка", "О боте"]: keyboard.button(text=text)
    keyboard.adjust(2); return keyboard.as_markup(resize_keyboard=True)

def kb_miniapp():
    builder = InlineKeyboardBuilder(); builder.button(text="Открыть AlphaPulse", web_app=WebAppInfo(url=MINI_APP_URL)); return builder.as_markup()

def kb_user_actions(telegram_id: int):
    builder = InlineKeyboardBuilder(); builder.row(InlineKeyboardButton(text="Верифицировать", callback_data=f"verify_{telegram_id}")); builder.row(InlineKeyboardButton(text="Заблокировать", callback_data=f"block_{telegram_id}")); return builder.as_markup()


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    telegram_id = message.from_user.id
    user = await create_user(telegram_id, message.from_user.username or "", message.from_user.full_name or "")
    if not user:
        await message.answer("Сервер недоступен. Попробуйте позже."); return
    if telegram_id == ADMIN_ID and user.get("status") != "VERIFIED":
        await set_status(telegram_id, "VERIFIED"); user = await get_user(telegram_id) or user
    status = user.get("status", "NEW")
    if status == "BLOCKED":
        await message.answer("Доступ заблокирован. Обратитесь к менеджеру " + MANAGER); return
    if status == "PENDING":
        await message.answer(f"⏳ Ваша заявка на проверке.\nВаш ID: <code>{telegram_id}</code>"); return
    if status == "VERIFIED":
        await message.answer("⚡ <b>Добро пожаловать в AlphaPulse!</b>\n\n🔥 <b>VIP Сигналы</b> — автоматические OTC-сетапы\n📊 <b>Market AI</b> — анализ OTC-пар\n📈 <b>3 стратегии</b> — EMA Trend, Bollinger Reversal, ATR Breakout\n🤖 <b>Online ML</b> — обучается на закрытых сигналах\n\nСигналы вероятностные и не гарантируют прибыль.", reply_markup=kb_main())
        await message.answer("Открыть AlphaPulse 👇", reply_markup=kb_miniapp()); return
    await message.answer("Добро пожаловать в AlphaPulse!\n\n1. Зарегистрируйтесь по кнопке ниже\n2. Выполните условия доступа\n3. Нажмите «Я зарегистрировался»\n\nПосле проверки откроются Mini App и OTC-сигналы.", reply_markup=kb_register(telegram_id))

@dp.callback_query(F.data == "verify:i_registered")
async def request_verification(callback: types.CallbackQuery):
    await callback.answer(); telegram_id = callback.from_user.id; user = await get_user(telegram_id)
    if not user: await callback.message.answer("Напишите /start."); return
    if user.get("status") == "VERIFIED": await callback.message.answer("✅ Вы уже верифицированы."); return
    if user.get("status") == "NEW": await callback.message.answer("Сначала нажмите «Зарегистрироваться»."); return
    if int(user.get("attempts_count") or 0) >= MAX_ATTEMPTS: await callback.message.answer("Лимит попыток исчерпан."); return
    if user.get("click_time"):
        clicked = datetime.fromisoformat(user["click_time"].replace("Z", "+00:00")); clicked = clicked if clicked.tzinfo else clicked.replace(tzinfo=timezone.utc)
        remaining = MIN_CLICK - (datetime.now(timezone.utc) - clicked).total_seconds()
        if remaining > 0: await callback.message.answer(f"Подождите ещё {int(remaining)} сек. после перехода."); return
    await api("POST", f"/api/auth/attempt?telegram_id={telegram_id}&secret={ADMIN_SECRET}"); await set_status(telegram_id, "PENDING"); await callback.message.answer("✅ Заявка отправлена на проверку.")
    try: await bot.send_message(ADMIN_ID, "🔔 <b>Новая заявка</b>\n"+f"ID: <code>{telegram_id}</code>\n@{callback.from_user.username or '—'}\n/verify {telegram_id} | /block {telegram_id}")
    except Exception: pass

@dp.callback_query(F.data == "support")
async def support_callback(callback: types.CallbackQuery):
    await callback.answer(); await callback.message.answer(f"Поддержка: {MANAGER}\nID: <code>{callback.from_user.id}</code>")

@dp.message(Command("verify"))
async def admin_verify(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit(): await message.answer("/verify ID"); return
    telegram_id = int(parts[1]); ok = await set_status(telegram_id, "VERIFIED"); await message.answer(f"Верифицирован: {telegram_id}" if ok else "Ошибка")
    if ok:
        try: await bot.send_message(telegram_id, "Доступ открыт! Напишите /start")
        except Exception: pass

@dp.message(Command("block"))
async def admin_block(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    parts=(message.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): await message.answer("/block ID"); return
    telegram_id=int(parts[1]); ok=await set_status(telegram_id,"BLOCKED"); await message.answer(f"Заблокирован: {telegram_id}" if ok else "Ошибка")

@dp.message(Command("pending"))
async def admin_pending(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    users=await api("GET",f"/api/auth/users?secret={ADMIN_SECRET}") or []; pending_users=[u for u in users if u["status"]=="PENDING"]
    if not pending_users: await message.answer("Нет заявок на верификацию."); return
    for user in pending_users: await message.answer(f"Пользователь: {user.get('full_name','')}\nUsername: @{user.get('username') or '—'}\nID: <code>{user['telegram_id']}</code>",reply_markup=kb_user_actions(user["telegram_id"]))

@dp.message(Command("users"))
async def admin_users(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    users=await api("GET",f"/api/auth/users?secret={ADMIN_SECRET}") or []; lines=["Все пользователи:",""]+[f"{u['status']} | {u['telegram_id']} @{u.get('username') or '—'}" for u in users[:30]]; await message.answer("\n".join(lines))

@dp.message(Command("stats"))
async def admin_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    users=await api("GET",f"/api/auth/users?secret={ADMIN_SECRET}") or []; await message.answer("Статистика:\n"+f"Всего: {len(users)}\nВерифицированы: {sum(u['status']=='VERIFIED' for u in users)}\nНа проверке: {sum(u['status']=='PENDING' for u in users)}\nЗаблокированы: {sum(u['status']=='BLOCKED' for u in users)}")

@dp.callback_query(F.data.startswith("verify_"))
async def admin_verify_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    telegram_id=int(callback.data.split("_")[1]); ok=await set_status(telegram_id,"VERIFIED"); await callback.answer("Верифицирован" if ok else "Ошибка")

@dp.callback_query(F.data.startswith("block_"))
async def admin_block_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    telegram_id=int(callback.data.split("_")[1]); ok=await set_status(telegram_id,"BLOCKED"); await callback.answer("Заблокирован" if ok else "Ошибка")

@dp.message(F.text == "VIP Сигналы")
async def vip_signals(message: types.Message):
    if not await verified_or_reply(message): return
    signals=await api("GET","/api/signals/vip?limit=5") or []
    if not signals: await message.answer("VIP сигналов пока нет.",reply_markup=kb_miniapp()); return
    tz = await get_user_timezone(message.from_user.id)
    lines = ["🔥 <b>VIP Сигналы</b>", ""]
    for s in signals:
        direction = "CALL" if s.get("direction") == "BUY" else "PUT"
        entry = local_signal_time(s.get("entry_time"), tz)
        expiry = local_signal_time(s.get("expiry_time"), tz)
        lines.append(
            f"<b>{s.get('pair','—')}</b> · {s.get('timeframe','5m')} · {direction} · {s.get('confidence','—')}%\n"
            f"⏰ Вход: <b>{entry}</b> · ⌛ Экспирация: <b>{expiry}</b>"
        )
    await message.answer("\n\n".join(lines), reply_markup=kb_miniapp())

@dp.message(F.text == "Настройки")
async def settings(message: types.Message):
    if await verified_or_reply(message): await message.answer("Настройки находятся в Mini App.",reply_markup=kb_miniapp())
@dp.message(F.text == "Поддержка")
async def support(message: types.Message):
    if await verified_or_reply(message): await message.answer(f"Поддержка: {MANAGER}\nВаш ID: <code>{message.from_user.id}</code>")
@dp.message(F.text == "О боте")
async def about(message: types.Message):
    if await verified_or_reply(message): await message.answer("AlphaPulse\n\nPocket Option OTC market scanner\n3 независимые стратегии\nOnline ML обучается после закрытия сигналов\nАвтосделок нет.",reply_markup=kb_miniapp())

def webhook_secret() -> str:
    return hashlib.sha256(f"alphapulsesbot:{BOT_TOKEN}".encode()).hexdigest()

def legacy_webhook_secret() -> str:
    # Keep accepting the secret used by the previous production deployment.
    # Telegram does not expose the configured secret in getWebhookInfo, so a
    # same-URL migration otherwise causes every update to be rejected with 403.
    return hashlib.sha256(f"alphapulse-webhook:{BOT_TOKEN}".encode()).hexdigest()

def valid_secret(value: str | None) -> bool:
    if not value:
        return False
    return hmac.compare_digest(value, webhook_secret()) or hmac.compare_digest(value, legacy_webhook_secret())

async def configure_webhook() -> str | None:
    if not BACKEND_URL.startswith("https://"): return None
    url=BACKEND_URL+"/telegram/webhook"; info=await bot.get_webhook_info()
    if info.url != url:
        await bot.set_webhook(url=url,secret_token=webhook_secret(),drop_pending_updates=False)
    return url
async def feed_update(payload: dict) -> None:
    update=Update.model_validate(payload,context={"bot":bot}); await dp.feed_update(bot,update)
async def main() -> None: await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
