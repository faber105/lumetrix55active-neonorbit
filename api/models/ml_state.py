from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from api.models.database import Base


class MLState(Base):
    __tablename__ = 'ml_state'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    samples: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
