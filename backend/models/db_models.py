from __future__ import annotations

import enum
import os
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _database_url():
    raw = os.getenv('DATABASE_URL', 'postgresql://alpha:alphapulse123@localhost:5432/alphapulse')
    if raw.startswith('postgresql://'):
        raw = 'postgresql+asyncpg://' + raw[len('postgresql://'):]
    url = make_url(raw)
    query = dict(url.query)
    query.pop('sslmode', None)
    query.pop('channel_binding', None)
    return url.set(drivername='postgresql+asyncpg', query=query)


_db_url = _database_url()
_connect_args = {}
if _db_url.host and 'neon.tech' in _db_url.host:
    _connect_args = {'ssl': True, 'statement_cache_size': 0}

engine = create_async_engine(
    _db_url,
    echo=False,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args=_connect_args,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(AsyncAttrs, DeclarativeBase):
    pass


class SignalDirection(str, enum.Enum):
    BUY = 'BUY'
    SELL = 'SELL'


class SignalResult(str, enum.Enum):
    WIN = 'WIN'
    LOSS = 'LOSS'
    DRAW = 'DRAW'
    PENDING = 'PENDING'


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String(64), nullable=True)
    full_name = Column(String(128), nullable=True)
    status = Column(String(20), default='NEW', nullable=False, index=True)
    click_time = Column(DateTime, nullable=True)
    pending_time = Column(DateTime, nullable=True)
    verified_time = Column(DateTime, nullable=True)
    attempts_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class UserSettings(Base):
    __tablename__ = 'user_settings'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    vip_enabled = Column(Boolean, default=True, nullable=False)
    notification_frequency = Column(String(16), default='standard', nullable=False)
    signal_mode = Column(String(16), default='vip', nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Signal(Base):
    __tablename__ = 'signals'
    id = Column(Integer, primary_key=True, index=True)
    pair = Column(String(40), nullable=False, index=True)
    asset = Column(String(40), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False, index=True)
    strategy = Column(String(32), nullable=False, index=True)
    direction = Column(Enum(SignalDirection), nullable=False)
    confidence = Column(Float, nullable=False)
    model_probability = Column(Float, nullable=True)
    is_vip = Column(Boolean, default=False, nullable=False, index=True)
    rsi = Column(Float, nullable=True)
    ema_signal = Column(String(32), nullable=True)
    macd_signal = Column(String(32), nullable=True)
    trend_strength = Column(Float, nullable=True)
    reason = Column(Text, nullable=False)
    features_json = Column(Text, nullable=False)
    analysis_price = Column(Float, nullable=True)
    entry_price = Column(Float, nullable=True)
    close_price = Column(Float, nullable=True)
    entry_time = Column(DateTime, nullable=False, index=True)
    expiry_time = Column(DateTime, nullable=False, index=True)
    result = Column(Enum(SignalResult), default=SignalResult.PENDING, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    closed_at = Column(DateTime, nullable=True)
    trained_at = Column(DateTime, nullable=True)


class StrategyPerformance(Base):
    __tablename__ = 'strategy_performance'
    id = Column(Integer, primary_key=True)
    strategy = Column(String(32), unique=True, nullable=False, index=True)
    samples = Column(Integer, default=0, nullable=False)
    wins = Column(Integer, default=0, nullable=False)
    losses = Column(Integer, default=0, nullable=False)
    draws = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    def __init__(self, **kwargs):
        # SQLAlchemy Column defaults are normally materialized at INSERT/flush.
        # Reconciliation updates counters immediately on a newly constructed row,
        # so initialize them in Python as well to avoid None += 1 on the first result.
        super().__init__(**kwargs)
        if self.samples is None:
            self.samples = 0
        if self.wins is None:
            self.wins = 0
        if self.losses is None:
            self.losses = 0
        if self.draws is None:
            self.draws = 0


class MLState(Base):
    __tablename__ = 'ml_state'
    strategy = Column(String(32), primary_key=True)
    payload = Column(Text, nullable=False)
    samples = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
