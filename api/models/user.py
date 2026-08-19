from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.database import Base

if TYPE_CHECKING:
    from api.models.payment import Payment
    from api.models.session import SessionTrade, TradingSession
    from api.models.subscription import Subscription


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)

    # Unified verification flow copied from alphapulse_auth:
    # NEW -> CLICKED -> PENDING -> VERIFIED | BLOCKED
    verification_status: Mapped[str] = mapped_column(String(20), default='NEW', index=True)
    click_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pending_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts_count: Mapped[int] = mapped_column(Integer, default=0)

    subscriptions: Mapped[list['Subscription']] = relationship(back_populates='user')
    sessions: Mapped[list['TradingSession']] = relationship(back_populates='user')
    trades: Mapped[list['SessionTrade']] = relationship(back_populates='user')
    payments: Mapped[list['Payment']] = relationship(back_populates='user')
