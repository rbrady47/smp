"""Async Redis connection with lazy initialization and graceful fallback.

Redis is optional — if unavailable, the app continues with in-memory caches.
"""

from __future__ import annotations

import logging
import os

import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

logger = logging.getLogger(__name__)

_pool: redis.Redis | None = None
_unavailable: bool = False


async def get_redis() -> redis.Redis | None:
    """Return the shared async Redis connection, or None if unavailable."""
    global _pool, _unavailable
    if _unavailable:
        return None
    if _pool is not None:
        return _pool
    try:
        _pool = redis.from_url(REDIS_URL, decode_responses=True)
        await _pool.ping()
        logger.info("Redis connected at %s", REDIS_URL)
        return _pool
    except Exception:
        logger.warning("Redis unavailable at %s — falling back to in-memory only", REDIS_URL)
        _unavailable = True
        _pool = None
        return None


async def close_redis() -> None:
    """Shut down the Redis connection pool."""
    global _pool, _unavailable
    if _pool is not None:
        try:
            await _pool.aclose()
        except Exception:
            pass
        _pool = None
    _unavailable = False


async def redis_available() -> bool:
    """Check if Redis is reachable right now."""
    r = await get_redis()
    if r is None:
        return False
    try:
        await r.ping()
        return True
    except Exception:
        return False
