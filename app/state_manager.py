"""Dual-write state manager: in-memory caches + optional Redis pub/sub.

Publishes state changes to Redis channels so SSE endpoints can push
updates without polling.  If Redis is unavailable, all operations are no-ops
and the in-memory path continues unaffected.

Channels:
  smp:node-updates        — AN/DN state changes (status, RTT, bandwidth)
  smp:services            — service check result changes
  smp:discovery           — DN discovered/removed events
  smp:topology-structure  — structural changes (node/link/map CRUD)
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
_SVC_KEY_PREFIX = "smp:service:"
_SEEKER_KEY_PREFIX = "smp:seeker-cache:"

# Channel names
CHANNEL_NODE_STATES = "smp:node-updates"
CHANNEL_SERVICES = "smp:services"
CHANNEL_DISCOVERY = "smp:discovery"
CHANNEL_TOPOLOGY_STRUCTURE = "smp:topology-structure"

ALL_CHANNELS = [CHANNEL_NODE_STATES, CHANNEL_SERVICES, CHANNEL_DISCOVERY, CHANNEL_TOPOLOGY_STRUCTURE]

_DEFAULT_TTL_SECONDS = 30  # 2x the 15s poll interval
_SERVICE_TTL_SECONDS = 60  # 2x the 30s service check interval
_SEEKER_CACHE_TTL_SECONDS = 30  # 2x the 5s seeker poll interval — expires if poller stops


# ---------------------------------------------------------------------------
# Node state (AN + DN) — existing
# ---------------------------------------------------------------------------

async def update_node_state(node_id: int | str, state: dict[str, Any]) -> None:
    """Write AN state to Redis with TTL and publish a change event."""
    r = await get_redis()
    if r is None:
        return
    key = f"{_AN_KEY_PREFIX}{node_id}"
    payload = json.dumps(state, default=str)
    try:
        await r.set(key, payload, ex=_DEFAULT_TTL_SECONDS)
        await r.publish(CHANNEL_NODE_STATES, json.dumps({
            "type": "node_update",
            "id": str(node_id),
            "state": state,
        }, default=str))
    except BaseException:
        logger.warning("Redis write failed for %s", key, exc_info=True)


async def update_dn_state(site_id: str, state: dict[str, Any]) -> None:
    """Write DN state to Redis with TTL and publish a change event."""
    r = await get_redis()
    if r is None:
        return
    key = f"{_DN_KEY_PREFIX}{site_id}"
    payload = json.dumps(state, default=str)
    try:
        await r.set(key, payload, ex=_DEFAULT_TTL_SECONDS)
        await r.publish(CHANNEL_NODE_STATES, json.dumps({
            "type": "dn_update",
            "id": site_id,
            "state": state,
        }, default=str))
    except BaseException:
        logger.warning("Redis write failed for %s", key, exc_info=True)


async def publish_offline(node_type: str, node_id: str) -> None:
    """Publish an explicit offline event and remove the key."""
    r = await get_redis()
    if r is None:
        return
    prefix = _AN_KEY_PREFIX if node_type == "node" else _DN_KEY_PREFIX
    key = f"{prefix}{node_id}"
    try:
        await r.delete(key)
        await r.publish(CHANNEL_NODE_STATES, json.dumps({
            "type": "node_offline",
            "id": str(node_id),
            "node_type": node_type,
        }))
    except BaseException:
        logger.warning("Redis offline publish failed for %s", key, exc_info=True)


# ---------------------------------------------------------------------------
# Dashboard snapshot — batched publish (replaces per-node events)
# ---------------------------------------------------------------------------

async def publish_dashboard_snapshot(
    anchors: dict[str, Any],
    discovered: dict[str, Any],
) -> None:
    """Publish the full dashboard state as a single event.

    This replaces the per-node update_node_state/update_dn_state calls
    that were generating N+M events per second.
    """
    r = await get_redis()
    if r is None:
        return
    try:
        await r.publish(CHANNEL_NODE_STATES, json.dumps({
            "type": "snapshot",
            "anchors": anchors,
            "discovered": discovered,
        }, default=str))
    except BaseException:
        logger.warning("Redis dashboard snapshot publish failed", exc_info=True)


# ---------------------------------------------------------------------------
# Service state — new
# ---------------------------------------------------------------------------

async def publish_service_state(service_id: int, state: dict[str, Any]) -> None:
    """Write service check state to Redis and publish a change event."""
    r = await get_redis()
    if r is None:
        return
    key = f"{_SVC_KEY_PREFIX}{service_id}"
    payload = json.dumps(state, default=str)
    try:
        await r.set(key, payload, ex=_SERVICE_TTL_SECONDS)
        await r.publish(CHANNEL_SERVICES, json.dumps({
            "type": "service_update",
            "id": service_id,
            "state": state,
        }, default=str))
    except BaseException:
        logger.warning("Redis write failed for %s", key, exc_info=True)


async def get_all_service_states() -> dict[str, dict[str, Any]]:
    """Read all service states from Redis. Returns {service_id: state_dict}."""
    r = await get_redis()
    if r is None:
        return {}
    try:
        keys: list[str] = []
        async for key in r.scan_iter(match=f"{_SVC_KEY_PREFIX}*", count=500):
            keys.append(key)
        if not keys:
            return {}
        values = await r.mget(keys)
        result: dict[str, dict[str, Any]] = {}
        for key, val in zip(keys, values):
            if val is not None:
                svc_id = key.removeprefix(_SVC_KEY_PREFIX)
                result[svc_id] = json.loads(val)
        return result
    except BaseException:
        logger.warning("Redis scan failed for service states", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Seeker detail cache — persisted for warm restart
# ---------------------------------------------------------------------------

async def update_seeker_cache(node_id: int | str, detail: dict[str, Any]) -> None:
    """Persist seeker detail to Redis so it survives process restarts."""
    r = await get_redis()
    if r is None:
        return
    key = f"{_SEEKER_KEY_PREFIX}{node_id}"
    try:
        await r.set(key, json.dumps(detail, default=str), ex=_SEEKER_CACHE_TTL_SECONDS)
    except BaseException:
        logger.warning("Redis write failed for %s", key, exc_info=True)


async def get_all_seeker_cache() -> dict[str, dict[str, Any]]:
    """Read all seeker detail cache entries from Redis. Returns {node_id: detail_dict}."""
    r = await get_redis()
    if r is None:
        return {}
    try:
        keys: list[str] = []
        async for key in r.scan_iter(match=f"{_SEEKER_KEY_PREFIX}*", count=500):
            keys.append(key)
        if not keys:
            return {}
        values = await r.mget(keys)
        result: dict[str, dict[str, Any]] = {}
        for key, val in zip(keys, values):
            if val is not None:
                node_id = key.removeprefix(_SEEKER_KEY_PREFIX)
                result[node_id] = json.loads(val)
        return result
    except BaseException:
        logger.warning("Redis scan failed for seeker cache", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Discovery events — new
# ---------------------------------------------------------------------------

async def publish_discovery_event(
    event_type: str,
    site_id: str,
    **extra: Any,
) -> None:
    """Publish a discovery event (dn_discovered, dn_removed)."""
    r = await get_redis()
    if r is None:
        return
    try:
        await r.publish(CHANNEL_DISCOVERY, json.dumps({
            "type": event_type,
            "site_id": site_id,
            **extra,
        }, default=str))
    except BaseException:
        logger.warning("Redis discovery publish failed for %s %s", event_type, site_id, exc_info=True)


# ---------------------------------------------------------------------------
# Topology structure events — new
# ---------------------------------------------------------------------------

async def publish_topology_change(reason: str, **extra: Any) -> None:
    """Publish a topology structure change event."""
    r = await get_redis()
    if r is None:
        return
    try:
        await r.publish(CHANNEL_TOPOLOGY_STRUCTURE, json.dumps({
            "type": "structure_changed",
            "reason": reason,
            **extra,
        }, default=str))
    except BaseException:
        logger.warning("Redis topology publish failed for %s", reason, exc_info=True)


# ---------------------------------------------------------------------------
# Read helpers — existing
# ---------------------------------------------------------------------------

async def get_node_state(node_id: int | str) -> dict[str, Any] | None:
    r = await get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(f"{_AN_KEY_PREFIX}{node_id}")
        return json.loads(raw) if raw else None
    except BaseException:
        return None


async def get_dn_state(site_id: str) -> dict[str, Any] | None:
    r = await get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(f"{_DN_KEY_PREFIX}{site_id}")
        return json.loads(raw) if raw else None
    except BaseException:
        return None


async def get_all_node_states() -> dict[str, dict[str, Any]]:
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
    except BaseException:
        logger.warning("Redis scan failed for AN states", exc_info=True)
        return {}


async def get_all_dn_states() -> dict[str, dict[str, Any]]:
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
    except BaseException:
        logger.warning("Redis scan failed for DN states", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

async def subscribe_state_changes() -> AsyncIterator[dict[str, Any]]:
    """Yield state change events from the node-updates channel.

    Kept for backward compatibility with /api/stream/node-states.
    """
    async for event in subscribe_channels([CHANNEL_NODE_STATES]):
        yield event


async def subscribe_channels(
    channels: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield events from one or more Redis pub/sub channels.

    If channels is None, subscribes to ALL_CHANNELS.
    Each yielded dict includes the decoded JSON payload from the channel.

    Uses ``get_message(timeout=1.0)`` in a loop instead of the blocking
    ``async for message in pubsub.listen()`` iterator.  This ensures the
    generator yields control back to the event loop every ~1 s so that
    Starlette can cancel it promptly when the SSE client disconnects.
    Without this, a quiet pub/sub channel keeps the generator (and the
    underlying HTTP/1.1 connection) blocked indefinitely.
    """
    import asyncio

    r = await get_redis()
    if r is None:
        raise RuntimeError("Redis unavailable for pub/sub subscription")
    target_channels = channels or ALL_CHANNELS
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(*target_channels)
        ticks_since_data = 0
        while True:
            # Poll with a short timeout so CancelledError propagates
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0,
            )
            if message is None:
                ticks_since_data += 1
                # Yield keepalive sentinel every ~30s of silence
                if ticks_since_data >= 30:
                    ticks_since_data = 0
                    yield {"type": "__keepalive__"}
                await asyncio.sleep(0)
                continue
            if message["type"] != "message":
                continue
            ticks_since_data = 0
            try:
                yield json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
    finally:
        await pubsub.unsubscribe(*target_channels)
        await pubsub.aclose()
