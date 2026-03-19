from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=max(1, settings.database_pool_size),
    max_overflow=max(0, settings.database_max_overflow),
    pool_timeout=max(1, settings.database_pool_timeout_seconds),
    pool_recycle=max(60, settings.database_pool_recycle_seconds),
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
