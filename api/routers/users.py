from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from api.models.database import get_db
from api.models.session import SessionTrade, TradingSession
from api.models.user import User
from api.routers.subscriptions import get_active_subscription
from api.schemas import SubscriptionStatus, UserSchema, UserStatsResponse

router = APIRouter(prefix="/user", tags=["user"])


@router.get("/me", response_model=UserSchema)
async def get_me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/stats", response_model=UserStatsResponse)
async def get_user_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserStatsResponse:
    sessions = list((await db.scalars(select(TradingSession).where(TradingSession.user_id == user.id))).all())
    trades = list((await db.scalars(select(SessionTrade).where(SessionTrade.user_id == user.id))).all())
    wins = sum(1 for trade in trades if trade.result == "WIN")
    total = len(trades)
    subscription = await get_active_subscription(db, user.id)

    return UserStatsResponse(
        sessions=len(sessions),
        winrate=round((wins / total) * 100, 2) if total else 0.0,
        total_pnl=sum((trade.pnl for trade in trades), Decimal("0.00")),
        active_subscription=SubscriptionStatus(
            is_active=subscription is not None,
            plan=subscription.plan if subscription else None,
            expires_at=subscription.expires_at if subscription else None,
        ),
    )

