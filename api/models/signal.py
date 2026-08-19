from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.database import Base

if TYPE_CHECKING:
    from api.models.session import SessionTrade


class Signal(Base):
    __tablename__ = 'signals'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset: Mapped[str] = mapped_column(String(40), index=True)
    asset_category: Mapped[str] = mapped_column(String(20), default='otc', index=True)
    direction: Mapped[str] = mapped_column(String(4))
    timeframe: Mapped[str] = mapped_column(String(5), index=True)
    duration_sec: Mapped[int] = mapped_column(Integer)

    open_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    close_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    confidence: Mapped[float]
    indicator_score: Mapped[float]
    ml_confidence: Mapped[float]

    strategy: Mapped[str] = mapped_column(String(40), default='unknown', index=True)
    market_regime: Mapped[str] = mapped_column(String(20), default='unknown')
    data_source: Mapped[str] = mapped_column(String(40), default='pocketoption_otc')
    feature_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    result: Mapped[str] = mapped_column(String(10), default='PENDING')
    agent_id: Mapped[str] = mapped_column(String(32), default='otc_scanner')
    requested_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)

    trades: Mapped[list['SessionTrade']] = relationship(back_populates='signal')
