import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Database setup is intentionally small: one engine, one declarative base,
# and a session factory shared by the FastAPI routes and Alembic.
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required.")


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    # Session usage is wrapped in a generator dependency so each request gets
    # a fresh session that is closed automatically when the request ends.
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()
