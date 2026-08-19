from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy import desc, select

from api.models.database import AsyncSessionLocal
from api.models.signal import Signal
from api.models.user import User
from config import get_settings
from signal_engine.otc_provider import display_asset

router = Router()


def _registration_keyboard(user_id: int) -> InlineKeyboardMarkup:
    settings = get_settings()
    go_url = f"{settings.effective_public_api_base_url.rstrip('/')}/go?{urlencode({'uid': user_id})}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📝 Зарегистрироваться', url=go_url)],
        [InlineKeyboardButton(text='✅ Я зарегистрировался', callback_data='verify:i_registered')],
        [InlineKeyboardButton(text='💬 Поддержка', callback_data='verify:support')],
    ])


def _verified_keyboard() -> InlineKeyboardMarkup:
    settings = get_settings()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📊 Открыть AlphaPulse', web_app=WebAppInfo(url=settings.effective_mini_app_url))],
        [InlineKeyboardButton(text='🔥 Последние OTC сигналы', callback_data='otc:latest')],
        [InlineKeyboardButton(text='ℹ️ Как работает', callback_data='otc:about')],
    ])


async def _upsert(message: Message) -> User:
    tg = message.from_user
    if tg is None:
        raise RuntimeError('Telegram user missing')
    async with AsyncSessionLocal() as db:
        user = await db.get(User, tg.id)
        if user is None:
            user = User(
                id=tg.id,
                username=tg.username,
                first_name=tg.first_name,
                full_name=tg.full_name,
                language_code=tg.language_code,
                verification_status='VERIFIED' if tg.id == get_settings().admin_telegram_id else 'NEW',
                verified_time=datetime.utcnow() if tg.id == get_settings().admin_telegram_id else None,
            )
            db.add(user)
        else:
            user.username = tg.username
            user.first_name = tg.first_name
            user.full_name = tg.full_name
            user.language_code = tg.language_code
        await db.commit()
        await db.refresh(user)
        return user


async def _get_user(user_id: int) -> User | None:
    async with AsyncSessionLocal() as db:
        return await db.get(User, user_id)


@router.message(CommandStart())
async def start(message: Message) -> None:
    user = await _upsert(message)
    settings = get_settings()
    if user.verification_status == 'BLOCKED' or user.is_banned:
        await message.answer('🚫 <b>Доступ заблокирован.</b> Обратитесь в поддержку.')
        return
    if user.verification_status == 'VERIFIED':
        await message.answer(
            f'<b>{settings.project_name}</b>\n\n'
            '✅ Аккаунт верифицирован.\n'
            'Бот сам сканирует Pocket Option OTC, выбирает подходящую стратегию и присылает только прошедшие фильтр сетапы.\n\n'
            'Автосделок нет — бот выдаёт направление и точное время входа.',
            reply_markup=_verified_keyboard(),
        )
        return
    if user.verification_status == 'PENDING':
        await message.answer('⏳ <b>Заявка уже на проверке.</b> Менеджер подтвердит доступ после проверки.')
        return
    await message.answer(
        f'<b>{settings.project_name}</b>\n\n'
        'Для доступа к OTC-сигналам пройдите верификацию:\n'
        '1️⃣ зарегистрируйтесь по кнопке ниже;\n'
        '2️⃣ завершите условия регистрации у брокера;\n'
        '3️⃣ нажмите «Я зарегистрировался».\n\n'
        '<i>Сигналы вероятностные и не гарантируют прибыль.</i>',
        reply_markup=_registration_keyboard(user.id),
    )


@router.callback_query(F.data == 'verify:i_registered')
async def i_registered(callback: CallbackQuery) -> None:
    await callback.answer()
    user = await _get_user(callback.from_user.id)
    if user is None:
        await callback.message.answer('Напишите /start.')
        return
    settings = get_settings()
    if user.verification_status == 'BLOCKED' or user.is_banned:
        await callback.message.answer('🚫 Доступ заблокирован.')
        return
    if user.verification_status == 'VERIFIED':
        await callback.message.answer('✅ Вы уже верифицированы.', reply_markup=_verified_keyboard())
        return
    if user.verification_status == 'NEW':
        await callback.message.answer('Сначала нажмите «Зарегистрироваться», затем вернитесь сюда.')
        return
    if user.attempts_count >= settings.max_verification_attempts:
        await callback.message.answer('🚫 Лимит попыток исчерпан. Напишите в поддержку.')
        return
    if user.click_time:
        clicked = user.click_time.replace(tzinfo=timezone.utc) if user.click_time.tzinfo is None else user.click_time
        elapsed = (datetime.now(timezone.utc) - clicked).total_seconds()
        if elapsed < settings.min_registration_seconds:
            await callback.message.answer(f'⏳ Подождите ещё {int(settings.min_registration_seconds - elapsed)} сек. после перехода на регистрацию.')
            return

    async with AsyncSessionLocal() as db:
        current = await db.get(User, callback.from_user.id)
        current.attempts_count += 1
        current.verification_status = 'PENDING'
        current.pending_time = datetime.utcnow()
        await db.commit()

    tg = callback.from_user
    await callback.bot.send_message(
        settings.admin_telegram_id,
        '🔔 <b>Новая заявка на верификацию</b>\n\n'
        f'ID: <code>{tg.id}</code>\n'
        f'Username: @{tg.username or "-"}\n'
        f'Имя: {tg.full_name}\n\n'
        f'/verify {tg.id}\n/block {tg.id}',
    )
    await callback.message.answer('✅ Заявка отправлена менеджеру. После подтверждения бот откроет сигналы.')


@router.callback_query(F.data == 'verify:support')
async def support(callback: CallbackQuery) -> None:
    await callback.answer()
    username = get_settings().support_username.strip().lstrip('@')
    suffix = f'@{username}' if username else 'администратору бота'
    await callback.message.answer(f'💬 Поддержка: {suffix}\nВаш ID: <code>{callback.from_user.id}</code>')


async def _require_verified(callback: CallbackQuery) -> User | None:
    user = await _get_user(callback.from_user.id)
    if user is None or (user.verification_status != 'VERIFIED' and user.id != get_settings().admin_telegram_id):
        await callback.answer('Сначала пройдите верификацию', show_alert=True)
        return None
    return user


@router.callback_query(F.data == 'otc:latest')
async def latest_signals(callback: CallbackQuery) -> None:
    if not await _require_verified(callback):
        return
    await callback.answer()
    async with AsyncSessionLocal() as db:
        signals = list((await db.scalars(select(Signal).where(Signal.requested_by_user_id.is_(None)).order_by(desc(Signal.created_at)).limit(5))).all())
    if not signals:
        await callback.message.answer('Пока нет сохранённых OTC-сигналов.')
        return
    lines = ['🔥 <b>Последние OTC сигналы</b>\n']
    for s in signals:
        icon = '🟢' if s.direction == 'CALL' else '🔴'
        lines.append(
            f'{icon} <b>{display_asset(s.asset)}</b> {s.direction} · {s.timeframe}\n'
            f'{s.strategy} · {s.confidence:.0%} · {s.result}\n'
            f'Вход UTC: {s.entry_time:%H:%M:%S}'
        )
    await callback.message.answer('\n\n'.join(lines))


@router.callback_query(F.data == 'otc:about')
async def about(callback: CallbackQuery) -> None:
    if not await _require_verified(callback):
        return
    await callback.answer()
    await callback.message.answer(
        '🧠 <b>Три стратегии</b>\n\n'
        '1. EMA + MACD Trend — трендовые продолжения.\n'
        '2. RSI + Bollinger Reversal — развороты во флэте.\n'
        '3. Donchian + ATR Breakout — импульсные пробои.\n\n'
        'Сначала определяется режим рынка (trend/range/breakout), затем выбирается лучший подходящий сетап. '
        'После экспирации результат сохраняется, а online-ML получает новый реальный пример.'
    )


@router.message(Command('verify'))
async def admin_verify(message: Message) -> None:
    if message.from_user.id != get_settings().admin_telegram_id:
        return
    parts = (message.text or '').split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer('Использование: /verify TELEGRAM_ID')
        return
    uid = int(parts[1])
    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        if user is None:
            await message.answer('Пользователь не найден.')
            return
        user.verification_status = 'VERIFIED'
        user.verified_time = datetime.utcnow()
        user.is_banned = False
        await db.commit()
    await message.answer(f'✅ {uid} верифицирован.')
    try:
        await message.bot.send_message(uid, '🎉 Верификация подтверждена. Напишите /start для доступа к AlphaPulse.')
    except Exception:
        pass


@router.message(Command('block'))
async def admin_block(message: Message) -> None:
    if message.from_user.id != get_settings().admin_telegram_id:
        return
    parts = (message.text or '').split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer('Использование: /block TELEGRAM_ID')
        return
    uid = int(parts[1])
    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        if user is None:
            await message.answer('Пользователь не найден.')
            return
        user.verification_status = 'BLOCKED'
        user.is_banned = True
        await db.commit()
    await message.answer(f'🚫 {uid} заблокирован.')


@router.message(Command('unblock'))
async def admin_unblock(message: Message) -> None:
    if message.from_user.id != get_settings().admin_telegram_id:
        return
    parts = (message.text or '').split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer('Использование: /unblock TELEGRAM_ID')
        return
    uid = int(parts[1])
    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        if user is None:
            await message.answer('Пользователь не найден.')
            return
        user.verification_status = 'NEW'
        user.is_banned = False
        user.attempts_count = 0
        await db.commit()
    await message.answer(f'✅ {uid} разблокирован.')


@router.message(Command('users'))
async def admin_users(message: Message) -> None:
    if message.from_user.id != get_settings().admin_telegram_id:
        return
    async with AsyncSessionLocal() as db:
        users = list((await db.scalars(select(User).order_by(desc(User.created_at)).limit(30))).all())
    icons = {'NEW': '🆕', 'CLICKED': '👆', 'PENDING': '⏳', 'VERIFIED': '✅', 'BLOCKED': '🚫'}
    lines = ['👥 <b>Пользователи</b>']
    for u in users:
        lines.append(f"{icons.get(u.verification_status, '❓')} <code>{u.id}</code> @{u.username or '-'} — {u.verification_status}")
    await message.answer('\n'.join(lines))
