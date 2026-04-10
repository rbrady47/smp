import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required.")


class Base(DeclarativeBase):
    pass


async_engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=30,
    max_overflow=20,
    pool_timeout=10,
    pool_recycle=3600,
)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine, class_=AsyncSession, expire_on_commit=False,
)


async def get_db():
    """Async session dependency for FastAPI route handlers."""
    async with AsyncSessionLocal() as db:
        yield db
