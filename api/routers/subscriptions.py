from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from api.models.app_setting import AppSetting
from api.models.database import get_db
from api.models.payment import Payment
from api.models.subscription import Subscription
from api.models.user import User
from api.schemas import (
    SubscriptionConfirmResponse,
    SubscriptionConfirmRequest,
    SubscriptionCreateRequest,
    SubscriptionCreateResponse,
    SubscriptionPlan,
    SubscriptionStatus,
)
from config import get_settings

router = APIRouter(prefix="/subscription", tags=["subscription"])
CRYPTO_WALLET_SETTING_KEY = "crypto_usdt_wallet"

PLANS: dict[str, dict[str, Any]] = {
    "week": {"title": "Базовый", "days": 7, "price_usd": Decimal("9.99"), "stars_amount": 500, "badge": None},
    "month": {"title": "Стандарт", "days": 30, "price_usd": Decimal("29.99"), "stars_amount": 1500, "badge": "Popular"},
    "year": {"title": "PRO", "days": 365, "price_usd": Decimal("199.99"), "stars_amount": 10000, "badge": "Best Value"},
}


async def get_crypto_wallet(db: AsyncSession) -> str:
    setting = await db.get(AppSetting, CRYPTO_WALLET_SETTING_KEY)
    wallet = setting.value.strip() if setting is not None else get_settings().usdt_trc20_wallet.strip()
    if not wallet:
        raise HTTPException(status_code=503, detail="Crypto wallet is not configured")
    return wallet


async def set_crypto_wallet(db: AsyncSession, wallet: str) -> AppSetting:
    clean_wallet = wallet.strip()
    if len(clean_wallet) < 10:
        raise HTTPException(status_code=400, detail="Wallet address is too short")
    setting = await db.get(AppSetting, CRYPTO_WALLET_SETTING_KEY)
    if setting is None:
        setting = AppSetting(key=CRYPTO_WALLET_SETTING_KEY, value=clean_wallet)
        db.add(setting)
    else:
        setting.value = clean_wallet
    await db.commit()
    await db.refresh(setting)
    return setting


def plan_to_schema(code: str) -> SubscriptionPlan:
    data = PLANS[code]
    return SubscriptionPlan(code=code, **data)


async def get_active_subscription(db: AsyncSession, user_id: int) -> Subscription | None:
    stmt = (
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.is_active.is_(True),
            Subscription.expires_at > datetime.utcnow(),
        )
        .order_by(desc(Subscription.expires_at))
        .limit(1)
    )
    return await db.scalar(stmt)


async def grant_subscription(db: AsyncSession, user_id: int, plan: str, payment_id: str | None = None) -> Subscription:
    now = datetime.utcnow()
    await db.execute(
        update(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.is_active.is_(True),
            Subscription.expires_at <= now,
        )
        .values(is_active=False)
    )
    active = await get_active_subscription(db, user_id)
    started_at = active.expires_at if active and active.expires_at > now else now
    subscription = Subscription(
        user_id=user_id,
        plan=plan,
        started_at=started_at,
        expires_at=started_at + timedelta(days=PLANS[plan]["days"]),
        is_active=True,
        payment_id=payment_id,
    )
    db.add(subscription)
    return subscription


@router.get("/plans", response_model=list[SubscriptionPlan])
async def get_plans() -> list[SubscriptionPlan]:
    return [plan_to_schema(code) for code in ("week", "month", "year")]


@router.get("/status", response_model=SubscriptionStatus)
async def get_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionStatus:
    settings = get_settings()
    if user.id == settings.admin_telegram_id:
        return SubscriptionStatus(is_active=True, plan="admin", expires_at=None)

    subscription = await get_active_subscription(db, user.id)
    if subscription is None:
        return SubscriptionStatus(is_active=False)
    return SubscriptionStatus(is_active=True, plan=subscription.plan, expires_at=subscription.expires_at)


async def _create_stars_invoice(payment: Payment, plan: SubscriptionPlan) -> str:
    settings = get_settings()
    payload = {
        "title": f"{settings.project_name} {plan.title}",
        "description": f"Подписка {plan.title} на {plan.days} дней",
        "payload": f"subscription:{payment.id}:{plan.code}",
        "currency": "XTR",
        "prices": [{"label": plan.title, "amount": plan.stars_amount}],
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.bot_token}/createInvoiceLink",
            json=payload,
        )
    data = response.json()
    if not data.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram invoice error: {data.get('description')}")
    return str(data["result"])


async def _notify_admin_crypto_review(payment: Payment, user: User, wallet: str, provider_data: dict[str, Any]) -> None:
    settings = get_settings()
    plan = PLANS[payment.plan]
    tx_note = str(provider_data.get("tx_hash") or provider_data.get("note") or "-")
    text = (
        "Новая crypto-заявка на подписку\n\n"
        f"Платеж: #{payment.id}\n"
        f"Пользователь: <code>{user.id}</code>\n"
        f"Username: @{user.username or '-'}\n"
        f"Тариф: {plan['title']} ({plan['days']} дней)\n"
        f"Сумма: {payment.amount} {payment.currency}\n"
        f"Кошелек: <code>{wallet}</code>\n"
        f"Комментарий/TX: <code>{tx_note}</code>"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Подтвердить", "callback_data": f"pay:approve:{payment.id}"},
                {"text": "Отклонить", "callback_data": f"pay:reject:{payment.id}"},
            ]
        ]
    }
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            f"https://api.telegram.org/bot{settings.bot_token}/sendMessage",
            json={
                "chat_id": settings.admin_telegram_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": keyboard,
            },
        )


@router.post("/create", response_model=SubscriptionCreateResponse)
async def create_subscription_payment(
    payload: SubscriptionCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionCreateResponse:
    plan = plan_to_schema(payload.plan)
    currency = "XTR" if payload.provider == "stars" else "USDT"
    amount = Decimal(plan.stars_amount) if payload.provider == "stars" else plan.price_usd
    payment = Payment(
        user_id=user.id,
        plan=payload.plan,
        amount=amount,
        currency=currency,
        provider=payload.provider,
        status="pending",
    )
    db.add(payment)
    await db.flush()

    payment_url: str | None = None
    provider_payment_id: str | None = None
    wallet: str | None = None
    message: str | None = None
    if payload.provider == "stars":
        payment_url = await _create_stars_invoice(payment, plan)
    else:
        wallet = await get_crypto_wallet(db)
        message = "Переведи точную сумму на кошелек и нажми 'Я оплатил'. Админ проверит перевод."

    await db.commit()
    await db.refresh(payment)
    return SubscriptionCreateResponse(
        payment_id=payment.id,
        provider=payload.provider,
        status=payment.status,
        payment_url=payment_url,
        provider_payment_id=provider_payment_id,
        wallet=wallet,
        amount=payment.amount,
        currency=payment.currency,
        message=message,
    )


async def _cryptobot_invoice_is_paid(provider_payment_id: str) -> bool:
    settings = get_settings()
    if not settings.cryptobot_api_token:
        raise HTTPException(status_code=503, detail="CRYPTOBOT_API_TOKEN is not configured")
    headers = {"Crypto-Pay-API-Token": settings.cryptobot_api_token}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://pay.crypt.bot/api/getInvoices",
            params={"invoice_ids": provider_payment_id},
            headers=headers,
        )
    data = response.json()
    if not data.get("ok"):
        raise HTTPException(status_code=502, detail=f"CryptoBot status error: {data.get('error')}")
    invoices = data.get("result", {}).get("items", [])
    return bool(invoices and invoices[0].get("status") == "paid")


@router.post("/confirm", response_model=SubscriptionConfirmResponse)
async def confirm_subscription_payment(
    payload: SubscriptionConfirmRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionConfirmResponse:
    payment = await db.get(Payment, payload.payment_id)
    if payment is None or payment.user_id != user.id:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status == "paid":
        subscription = await get_active_subscription(db, user.id)
        return SubscriptionConfirmResponse(
            payment_id=payment.id,
            payment_status=payment.status,
            subscription=SubscriptionStatus(
                is_active=subscription is not None,
                plan=subscription.plan if subscription else None,
                expires_at=subscription.expires_at if subscription else None,
            ),
            message="Подписка уже активирована.",
        )

    if payment.provider == "stars":
        raise HTTPException(status_code=400, detail="Telegram Stars payments are confirmed by bot payment updates")
    if payment.status == "rejected":
        raise HTTPException(status_code=409, detail="Crypto payment was rejected by admin")
    wallet = await get_crypto_wallet(db)
    if payment.status != "review":
        payment.status = "review"
        await db.commit()
        await _notify_admin_crypto_review(payment, user, wallet, payload.provider_data)
    subscription = await get_active_subscription(db, user.id)
    return SubscriptionConfirmResponse(
        payment_id=payment.id,
        payment_status=payment.status,
        subscription=SubscriptionStatus(
            is_active=subscription is not None,
            plan=subscription.plan if subscription else None,
            expires_at=subscription.expires_at if subscription else None,
        ),
        message="Заявка отправлена админу. Доступ включится после подтверждения перевода.",
    )


@router.get("/crypto-wallet")
async def crypto_wallet(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    return {"wallet": await get_crypto_wallet(db), "currency": "USDT"}


async def approve_crypto_payment(db: AsyncSession, payment_id: int) -> Subscription:
    payment = await db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    payment.status = "paid"
    subscription = await grant_subscription(db, payment.user_id, payment.plan, str(payment.id))
    await db.commit()
    await db.refresh(subscription)
    return subscription


async def reject_crypto_payment(db: AsyncSession, payment_id: int) -> Payment:
    payment = await db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    payment.status = "rejected"
    await db.commit()
    await db.refresh(payment)
    return payment
