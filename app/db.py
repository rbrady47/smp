import os

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Database setup is intentionally small: one engine, one declarative base,
# and a session factory shared by the FastAPI routes and Alembic.
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required.")


class Base(DeclarativeBase):
    pass


# Sync engine — kept for Alembic migrations and startup create_all
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Async engine — used by the application (routes, pollers, services)
async_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine, class_=AsyncSession, expire_on_commit=False,
)


async def get_db():
    """Async session dependency for FastAPI route handlers."""
    async with AsyncSessionLocal() as db:
        yield db
