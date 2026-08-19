import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy import select

from api.models.database import AsyncSessionLocal
from api.models.payment import Payment
from api.models.user import User
from api.routers.subscriptions import PLANS, get_active_subscription, grant_subscription
from config import get_settings

router = Router()
logger = logging.getLogger(__name__)


def subscription_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for code, plan in PLANS.items():
        badge = f" · {plan['badge']}" if plan["badge"] else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{plan['title']} {plan['days']} дн. · {plan['stars_amount']} Stars{badge}",
                    callback_data=f"sub:{code}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("subscribe"))
@router.callback_query(F.data == "open_subscriptions")
async def subscribe(event: Message | CallbackQuery) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    if message is None:
        return
    await message.answer("Выбери тариф AlphaPulse:", reply_markup=subscription_keyboard())
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data.startswith("sub:"))
async def create_stars_invoice(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return
    plan_code = callback.data.split(":", 1)[1]
    plan = PLANS.get(plan_code)
    if plan is None:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        user = await db.get(User, callback.from_user.id)
        if user is None:
            user = User(
                id=callback.from_user.id,
                username=callback.from_user.username,
                first_name=callback.from_user.first_name,
                language_code=callback.from_user.language_code,
            )
            db.add(user)
            await db.flush()
        payment = Payment(
            user_id=user.id,
            plan=plan_code,
            amount=plan["stars_amount"],
            currency="XTR",
            status="pending",
            provider="stars",
        )
        db.add(payment)
        await db.commit()
        await db.refresh(payment)

    await callback.message.answer_invoice(
        title=f"{get_settings().project_name} {plan['title']}",
        description=f"Подписка на {plan['days']} дней",
        payload=f"subscription:{payment.id}:{plan_code}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=plan["title"], amount=plan["stars_amount"])],
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message) -> None:
    payment_payload = message.successful_payment.invoice_payload if message.successful_payment else ""
    parts = payment_payload.split(":")
    if len(parts) != 3 or parts[0] != "subscription":
        await message.answer("Оплата получена, но payload подписки не распознан. Напиши администратору.")
        return

    payment_id = int(parts[1])
    plan_code = parts[2]
    async with AsyncSessionLocal() as db:
        payment = await db.get(Payment, payment_id)
        if payment is None:
            await message.answer("Оплата получена, но платеж не найден в базе. Напиши администратору.")
            return
        payment.status = "paid"
        payment.provider_payment_id = message.successful_payment.telegram_payment_charge_id
        subscription = await grant_subscription(db, payment.user_id, plan_code, str(payment.id))
        await db.commit()
        await db.refresh(subscription)

    await message.answer(
        f"Подписка активирована до <b>{subscription.expires_at:%d.%m.%Y %H:%M}</b> UTC.\n"
        "Теперь можно открывать Mini App и запускать торговую сессию."
    )


@router.message(Command("status"))
async def subscription_status(message: Message) -> None:
    if message.from_user is None:
        return
    async with AsyncSessionLocal() as db:
        subscription = await get_active_subscription(db, message.from_user.id)
    if subscription is None:
        await message.answer("Активной подписки нет. Нажми /subscribe, чтобы выбрать тариф.")
        return
    await message.answer(f"Активный тариф: <b>{subscription.plan}</b>\nДо: {subscription.expires_at:%d.%m.%Y %H:%M} UTC")

