"""Dual-write state manager: in-memory caches + optional Redis pub/sub.

Publishes node state changes to a Redis channel so SSE endpoints can push
updates without polling.  If Redis is unavailable, all operations are no-ops
and the in-memory path continues unaffected.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from app.redis_client import get_redis

logger = logging.getLogger(__name__)

# Redis key patterns and configuration
_AN_KEY_PREFIX = "smp:node:"
_DN_KEY_PREFIX = "smp:dn:"
_CHANNEL = "smp:node-updates"
_DEFAULT_TTL_SECONDS = 30  # 2x the 15s poll interval


async def update_node_state(node_id: int | str, state: dict[str, Any]) -> None:
    """Write AN state to Redis with TTL and publish a change event."""
    r = await get_redis()
    if r is None:
        return
    key = f"{_AN_KEY_PREFIX}{node_id}"
    payload = json.dumps(state, default=str)
    try:
        await r.set(key, payload, ex=_DEFAULT_TTL_SECONDS)
        await r.publish(_CHANNEL, json.dumps({
            "type": "node_update",
            "id": str(node_id),
            "state": state,
        }, default=str))
    except Exception:
        logger.debug("Redis write failed for %s", key, exc_info=True)


async def update_dn_state(site_id: str, state: dict[str, Any]) -> None:
    """Write DN state to Redis with TTL and publish a change event."""
    r = await get_redis()
    if r is None:
        return
    key = f"{_DN_KEY_PREFIX}{site_id}"
    payload = json.dumps(state, default=str)
    try:
        await r.set(key, payload, ex=_DEFAULT_TTL_SECONDS)
        await r.publish(_CHANNEL, json.dumps({
            "type": "dn_update",
            "id": site_id,
            "state": state,
        }, default=str))
    except Exception:
        logger.debug("Redis write failed for %s", key, exc_info=True)


async def publish_offline(node_type: str, node_id: str) -> None:
    """Publish an explicit offline event and remove the key."""
    r = await get_redis()
    if r is None:
        return
    prefix = _AN_KEY_PREFIX if node_type == "node" else _DN_KEY_PREFIX
    key = f"{prefix}{node_id}"
    try:
        await r.delete(key)
        await r.publish(_CHANNEL, json.dumps({
            "type": "node_offline",
            "id": str(node_id),
            "node_type": node_type,
        }))
    except Exception:
        logger.debug("Redis offline publish failed for %s", key, exc_info=True)


async def get_node_state(node_id: int | str) -> dict[str, Any] | None:
    """Read a single AN state from Redis."""
    r = await get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(f"{_AN_KEY_PREFIX}{node_id}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def get_dn_state(site_id: str) -> dict[str, Any] | None:
    """Read a single DN state from Redis."""
    r = await get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(f"{_DN_KEY_PREFIX}{site_id}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def get_all_node_states() -> dict[str, dict[str, Any]]:
    """Read all AN states from Redis.  Returns {node_id: state_dict}."""
    r = await get_redis()
    if r is None:
        return {}
    try:
        keys: list[str] = []
        async for key in r.scan_iter(match=f"{_AN_KEY_PREFIX}*", count=500):
            keys.append(key)
        if not keys:
            return {}
        values = await r.mget(keys)
        result: dict[str, dict[str, Any]] = {}
        for key, val in zip(keys, values):
            if val is not None:
                node_id = key.removeprefix(_AN_KEY_PREFIX)
                result[node_id] = json.loads(val)
        return result
    except Exception:
        logger.debug("Redis scan failed for AN states", exc_info=True)
        return {}


async def get_all_dn_states() -> dict[str, dict[str, Any]]:
    """Read all DN states from Redis.  Returns {site_id: state_dict}."""
    r = await get_redis()
    if r is None:
        return {}
    try:
        keys: list[str] = []
        async for key in r.scan_iter(match=f"{_DN_KEY_PREFIX}*", count=500):
            keys.append(key)
        if not keys:
            return {}
        values = await r.mget(keys)
        result: dict[str, dict[str, Any]] = {}
        for key, val in zip(keys, values):
            if val is not None:
                site_id = key.removeprefix(_DN_KEY_PREFIX)
                result[site_id] = json.loads(val)
        return result
    except Exception:
        logger.debug("Redis scan failed for DN states", exc_info=True)
        return {}


async def subscribe_state_changes() -> AsyncIterator[dict[str, Any]]:
    """Yield state change events from Redis pub/sub.

    Each yielded dict has keys: type, id, state (or node_type for offline).
    Raises if Redis is unavailable.
    """
    r = await get_redis()
    if r is None:
        raise RuntimeError("Redis unavailable for pub/sub subscription")
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(_CHANNEL)
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                yield json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
    finally:
        await pubsub.unsubscribe(_CHANNEL)
        await pubsub.aclose()
