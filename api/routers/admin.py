from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import require_admin_token
from api.models.channel_join import ChannelJoinRequest
from api.models.database import get_db
from api.models.payment import Payment
from api.models.signal import Signal
from api.models.user import User
from api.routers.subscriptions import approve_crypto_payment
from api.schemas import AdminPaymentConfirmRequest, AdminSignalResultRequest, SignalSchema, UserSchema
from config import get_settings

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_token)])


@router.get("/users", response_model=list[UserSchema])
async def list_users(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[User]:
    stmt = select(User).order_by(desc(User.created_at)).limit(limit).offset(offset)
    return list((await db.scalars(stmt)).all())


@router.post("/users/{user_id}/ban", response_model=UserSchema)
async def ban_user(user_id: int, db: AsyncSession = Depends(get_db)) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_banned = True
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/users/{user_id}/unban", response_model=UserSchema)
async def unban_user(user_id: int, db: AsyncSession = Depends(get_db)) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_banned = False
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/join-requests")
async def list_join_requests(
    db: AsyncSession = Depends(get_db),
    status: str = Query(default="pending", pattern="^(pending|approved|rejected)$"),
) -> list[dict[str, object]]:
    stmt = (
        select(ChannelJoinRequest)
        .where(ChannelJoinRequest.status == status)
        .order_by(desc(ChannelJoinRequest.requested_at))
        .limit(100)
    )
    requests = list((await db.scalars(stmt)).all())
    return [
        {
            "id": item.id,
            "user_id": item.user_id,
            "username": item.username,
            "status": item.status,
            "requested_at": item.requested_at,
            "reviewed_at": item.reviewed_at,
            "reviewed_by": item.reviewed_by,
            "note": item.note,
        }
        for item in requests
    ]


async def _review_join_request(db: AsyncSession, request_id: int, status: str) -> dict[str, object]:
    item = await db.get(ChannelJoinRequest, request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Join request not found")
    item.status = status
    item.reviewed_at = datetime.utcnow()
    item.reviewed_by = get_settings().admin_telegram_id
    await db.commit()
    return {"id": item.id, "status": item.status}


@router.post("/join-requests/{request_id}/approve")
async def approve_join_request(request_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    return await _review_join_request(db, request_id, "approved")


@router.post("/join-requests/{request_id}/reject")
async def reject_join_request(request_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    return await _review_join_request(db, request_id, "rejected")


@router.post("/payments/confirm")
async def confirm_payment_manually(
    payload: AdminPaymentConfirmRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    payment = await db.get(Payment, payload.payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    subscription = await approve_crypto_payment(db, payment.id)
    return {"payment_id": payment.id, "subscription_id": subscription.id, "expires_at": subscription.expires_at}


@router.post("/signals/{signal_id}/result", response_model=SignalSchema)
async def update_signal_result(
    signal_id: int,
    payload: AdminSignalResultRequest,
    db: AsyncSession = Depends(get_db),
) -> Signal:
    signal = await db.get(Signal, signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    signal.result = payload.result
    await db.commit()
    await db.refresh(signal)
    return signal
