from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase

from config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

engine_options = {"pool_pre_ping": True}
if not settings.async_database_url.startswith("sqlite"):
    # Serverless functions should not keep a process-local SQLAlchemy pool alive.
    # Neon/PgBouncer handles pooling outside the function.
    import os
    if os.getenv("VERCEL"):
        engine_options["poolclass"] = NullPool
    else:
        engine_options.update({"pool_size": 10, "max_overflow": 20})

engine = create_async_engine(
    settings.async_database_url,
    **engine_options,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    # Import models so SQLAlchemy registers all tables before create_all.
    import api.models.app_setting  # noqa: F401
    import api.models.channel_join  # noqa: F401
    import api.models.payment  # noqa: F401
    import api.models.ml_state  # noqa: F401
    import api.models.session  # noqa: F401
    import api.models.signal  # noqa: F401
    import api.models.subscription  # noqa: F401
    import api.models.user  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
