import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from api.models.channel_join import ChannelJoinRequest
from api.models.database import AsyncSessionLocal
from api.models.payment import Payment
from api.models.user import User
from api.routers.subscriptions import approve_crypto_payment, get_crypto_wallet, reject_crypto_payment, set_crypto_wallet
from config import get_settings

router = Router()
logger = logging.getLogger(__name__)


def is_admin(user_id: int | None) -> bool:
    return user_id == get_settings().admin_telegram_id


@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    if not is_admin(message.from_user.id if message.from_user else None):
        await message.answer("Команда доступна только администратору.")
        return
    await message.answer(
        "Admin AlphaPulse\n\n"
        "Заявки на вход приходят сюда автоматически.\n"
        "Кошелек USDT: /wallet <адрес>\n"
        "Для API админки используй Bearer ADMIN_TOKEN из локального .env."
    )


@router.message(Command("wallet"))
async def crypto_wallet(message: Message) -> None:
    if not is_admin(message.from_user.id if message.from_user else None):
        await message.answer("Команда доступна только администратору.")
        return

    parts = (message.text or "").split(maxsplit=1)
    async with AsyncSessionLocal() as db:
        if len(parts) == 1:
            try:
                wallet = await get_crypto_wallet(db)
                await message.answer(f"Текущий USDT кошелек:\n<code>{wallet}</code>")
            except Exception:
                await message.answer("USDT кошелек еще не настроен.\nИспользуй: <code>/wallet ADDRESS</code>")
            return

        setting = await set_crypto_wallet(db, parts[1])
        await message.answer(f"USDT кошелек обновлен:\n<code>{setting.value}</code>")


@router.callback_query(F.data.startswith("join:"))
async def review_join_request(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    _, action, request_id_raw = callback.data.split(":", 2)
    request_id = int(request_id_raw)
    status = "approved" if action == "approve" else "rejected"

    async with AsyncSessionLocal() as db:
        join_request = await db.get(ChannelJoinRequest, request_id)
        if join_request is None:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        if join_request.status != "pending":
            await callback.answer("Заявка уже обработана", show_alert=True)
            return
        join_request.status = status
        join_request.reviewed_at = datetime.utcnow()
        join_request.reviewed_by = callback.from_user.id
        await db.commit()

    if callback.message:
        await callback.message.edit_text(f"Заявка #{request_id} обработана: {status}.")
    user_text = (
        "Заявка на вход подтверждена. Администратор открыл доступ к каналу."
        if status == "approved"
        else "Заявка на вход отклонена. Можно связаться с поддержкой через администратора."
    )
    await callback.bot.send_message(join_request.user_id, user_text)
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("pay:"))
async def review_crypto_payment(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    if callback.data is None:
        return

    _, action, payment_id_raw = callback.data.split(":", 2)
    payment_id = int(payment_id_raw)

    async with AsyncSessionLocal() as db:
        payment = await db.get(Payment, payment_id)
        if payment is None:
            await callback.answer("Платеж не найден", show_alert=True)
            return
        user = await db.get(User, payment.user_id)

        if action == "approve":
            if payment.status != "paid":
                subscription = await approve_crypto_payment(db, payment.id)
            else:
                subscription = None
            status_text = "подтвержден"
            user_text = (
                "Crypto-оплата подтверждена.\n"
                f"Подписка активна до <b>{subscription.expires_at:%d.%m.%Y %H:%M}</b> UTC."
                if subscription is not None
                else "Crypto-оплата уже была подтверждена."
            )
        else:
            await reject_crypto_payment(db, payment.id)
            status_text = "отклонен"
            user_text = "Crypto-оплата отклонена администратором. Проверь перевод или свяжись с поддержкой."

    if callback.message:
        await callback.message.edit_text(f"Платеж #{payment_id} {status_text}.")
    if user is not None:
        await callback.bot.send_message(user.id, user_text)
    await callback.answer("Готово")
