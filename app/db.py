import os
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Fall back to async in-memory SQLite for tests; production must set the env var.
    if "unittest" in sys.modules:
        DATABASE_URL = "sqlite+aiosqlite:///:memory:"
    else:
        raise RuntimeError("DATABASE_URL environment variable is required.")


class Base(DeclarativeBase):
    pass


_engine_kwargs: dict = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    # SQLite doesn't support connection pool sizing
    pass
else:
    _engine_kwargs.update(pool_size=30, max_overflow=20, pool_timeout=10, pool_recycle=3600)

async_engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine, class_=AsyncSession, expire_on_commit=False,
)


async def get_db():
    """Async session dependency for FastAPI route handlers."""
    async with AsyncSessionLocal() as db:
        yield db
