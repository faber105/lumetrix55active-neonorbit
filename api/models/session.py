from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.database import Base

if TYPE_CHECKING:
    from api.models.signal import Signal
    from api.models.user import User


class TradingSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    goal_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    trade_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    timeframe_filter: Mapped[str | None] = mapped_column(String(5))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), default="active", index=True)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    pnl: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    goal_reached: Mapped[bool] = mapped_column(default=False)

    user: Mapped["User"] = relationship(back_populates="sessions")
    trades: Mapped[list["SessionTrade"]] = relationship(back_populates="session")


class SessionTrade(Base):
    __tablename__ = "session_trades"
    __table_args__ = (UniqueConstraint("session_id", "signal_id", name="uq_session_signal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    result: Mapped[str] = mapped_column(String(4))
    trade_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    pnl: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    marked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    session: Mapped["TradingSession"] = relationship(back_populates="trades")
    signal: Mapped["Signal"] = relationship(back_populates="trades")
    user: Mapped["User"] = relationship(back_populates="trades")

