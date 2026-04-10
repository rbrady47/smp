"""SSE streaming routes — unified event stream + legacy endpoints."""

import asyncio
import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.redis_client import redis_available
from app import state_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Valid channel names that clients can subscribe to
_VALID_CHANNELS = {
    "node-states": state_manager.CHANNEL_NODE_STATES,
    "services": state_manager.CHANNEL_SERVICES,
    "discovery": state_manager.CHANNEL_DISCOVERY,
    "topology-structure": state_manager.CHANNEL_TOPOLOGY_STRUCTURE,
}


@router.get("/stream/events")
async def stream_events(
    channels: str = Query(default=""),
) -> StreamingResponse:
    """Unified SSE endpoint for all real-time events.

    Query param `channels` is a comma-separated list of channel names:
      node-states, services, discovery, topology-structure

    If omitted or empty, subscribes to all channels.

    Redis mode: snapshot on connect, then pub/sub push for each change.
    Fallback mode: 1s poll loop for node state changes only.
    """
    # Parse requested channels
    if channels.strip():
        requested = [c.strip() for c in channels.split(",") if c.strip()]
        redis_channels = [_VALID_CHANNELS[c] for c in requested if c in _VALID_CHANNELS]
    else:
        redis_channels = list(_VALID_CHANNELS.values())

    use_redis = await redis_available()

    async def redis_event_generator():
        try:
            # Snapshot phase: emit current state
            an_states = await state_manager.get_all_node_states()
            dn_states = await state_manager.get_all_dn_states()
            snapshot = {"anchors": an_states, "discovered": dn_states}
            yield f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"

            # Also snapshot service states if subscribed
            if state_manager.CHANNEL_SERVICES in redis_channels:
                svc_states = await state_manager.get_all_service_states()
                if svc_states:
                    yield f"event: service_snapshot\ndata: {json.dumps(svc_states, default=str)}\n\n"

            # Live phase: subscribe and forward
            async for event in state_manager.subscribe_channels(redis_channels):
                event_type = event.get("type", "node_update")
                yield f"event: {event_type}\ndata: {json.dumps(event, default=str)}\n\n"
        except Exception:
            logger.debug("Redis SSE subscription ended, falling back to polling", exc_info=True)
            async for chunk in fallback_event_generator():
                yield chunk

    async def fallback_event_generator():
        from app.main import node_dashboard_backend
        last_sent: str | None = None
        try:
            while True:
                cache = node_dashboard_backend.node_dashboard_cache
                payload = {
                    "anchors": {str(a["id"]): a for a in (cache.get("anchors") or []) if isinstance(a, dict) and a.get("id")},
                    "discovered": {str(d["site_id"]): d for d in (cache.get("discovered") or []) if isinstance(d, dict) and d.get("site_id")},
                }
                serialized = json.dumps(payload, default=str)
                if serialized != last_sent:
                    yield f"event: snapshot\ndata: {serialized}\n\n"
                    last_sent = serialized
                    await asyncio.sleep(1.0)   # Check for changes every 1s
                else:
                    yield ": keep-alive\n\n"
                    await asyncio.sleep(15.0)  # No changes — slow keep-alive
        except asyncio.CancelledError:
            return

    generator = redis_event_generator() if use_redis else fallback_event_generator()
    return StreamingResponse(generator, media_type="text/event-stream")


@router.get("/node-dashboard/stream")
async def node_dashboard_stream(window_seconds: int = Query(default=60)) -> StreamingResponse:
    async def event_generator():
        from app.main import get_serialized_node_dashboard_cache, normalize_node_dashboard_window
        last_sent: str | None = None
        try:
            while True:
                payload = get_serialized_node_dashboard_cache(normalize_node_dashboard_window(window_seconds))
                serialized = json.dumps(payload)
                if serialized != last_sent:
                    yield f"event: snapshot\ndata: {serialized}\n\n"
                    last_sent = serialized
                    await asyncio.sleep(1.0)
                else:
                    yield ": keep-alive\n\n"
                    await asyncio.sleep(15.0)
        except asyncio.CancelledError:
            return

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/stream/node-states")
async def stream_node_states() -> StreamingResponse:
    """Legacy SSE endpoint — alias for /api/stream/events?channels=node-states."""
    use_redis = await redis_available()

    async def redis_event_generator():
        try:
            an_states = await state_manager.get_all_node_states()
            dn_states = await state_manager.get_all_dn_states()
            snapshot = {"anchors": an_states, "discovered": dn_states}
            yield f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"

            async for event in state_manager.subscribe_state_changes():
                event_type = event.get("type", "node_update")
                yield f"event: {event_type}\ndata: {json.dumps(event, default=str)}\n\n"
        except Exception:
            logger.debug("Redis SSE subscription ended, falling back to polling", exc_info=True)
            async for chunk in fallback_event_generator():
                yield chunk

    async def fallback_event_generator():
        from app.main import node_dashboard_backend
        last_sent: str | None = None
        try:
            while True:
                cache = node_dashboard_backend.node_dashboard_cache
                payload = {
                    "anchors": {str(a["id"]): a for a in (cache.get("anchors") or []) if isinstance(a, dict) and a.get("id")},
                    "discovered": {str(d["site_id"]): d for d in (cache.get("discovered") or []) if isinstance(d, dict) and d.get("site_id")},
                }
                serialized = json.dumps(payload, default=str)
                if serialized != last_sent:
                    yield f"event: snapshot\ndata: {serialized}\n\n"
                    last_sent = serialized
                    await asyncio.sleep(1.0)
                else:
                    yield ": keep-alive\n\n"
                    await asyncio.sleep(15.0)
        except asyncio.CancelledError:
            return

    generator = redis_event_generator() if use_redis else fallback_event_generator()
    return StreamingResponse(generator, media_type="text/event-stream")
