from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config.settings import get_settings


settings = get_settings()

async_engine = create_async_engine(
    settings.sqlalchemy_database_url,
    echo=settings.database_echo,
    connect_args=settings.sqlalchemy_connect_args,
    pool_pre_ping=True,
    pool_recycle=1800,
)

AsyncSessionFactory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """为每个 FastAPI 请求提供独立 AsyncSession。"""

    async with AsyncSessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def dispose_database_engine() -> None:
    await async_engine.dispose()
