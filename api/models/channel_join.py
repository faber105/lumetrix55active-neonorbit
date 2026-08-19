from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from api.models.database import Base


class ChannelJoinRequest(Base):
    __tablename__ = "channel_join_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_by: Mapped[int | None]
    note: Mapped[str | None] = mapped_column(Text)

