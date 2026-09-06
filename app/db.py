from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from app.config import settings


class Base(DeclarativeBase):
    pass


_engine_kwargs = {}
if settings.database_url.startswith("sqlite"):
    # Keep a single shared in-memory DB across the connection pool (used in tests).
    _engine_kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}

engine = create_async_engine(settings.database_url, echo=False, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
