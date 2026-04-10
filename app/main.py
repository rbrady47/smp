"""SMP — Seeker Management Platform.

FastAPI application entry point. Creates the PollerState, initializes the
dashboard backend, starts/stops background polling loops via lifespan,
and mounts all route modules.

Non-route business logic lives in app/pollers/ and app/services/.
"""

import asyncio
import concurrent.futures
from contextlib import asynccontextmanager
import logging

# Configure app-level logging so INFO messages from pollers/services are visible
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(name)s - %(message)s",
)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from app.db import Base, async_engine
from app.node_dashboard_backend import NodeDashboardBackend
from app.poller_state import PollerState
from app.pollers.ping import check_tcp_port, ping_host
from app.pollers.seeker import refresh_seeker_detail_for_node
from app.pollers.dashboard import (
    NODE_DASHBOARD_PROJECTION_REFRESH_SECONDS,
    apply_windowed_detail_summary,
    normalize_node_dashboard_window,
    get_serialized_node_dashboard_cache,
    probe_discovered_node_detail,
    summarize_dashboard_node,
)
from app.pollers.services import (
    check_service,
    merge_service_payload,
    summarize_service_statuses,
)
from app.services.node_health import (
    STATUS_PRIORITY,
    get_node_or_404,
    refresh_nodes,
    request_node_telemetry,
    serialize_node,
)
from app.seeker_api import (
    build_detail_payload,
    get_bwv_cfg,
    get_bwv_stats,
    normalize_bwv_stats,
)
from app.redis_client import get_redis, close_redis
from app import state_manager

logger = logging.getLogger(__name__)

DASHBOARD_STATUS_PRIORITY = {
    "healthy": 0,
    "degraded": 1,
    "offline": 2,
}

# ---------------------------------------------------------------------------
# Singleton PollerState — created at module load, populated during lifespan
# ---------------------------------------------------------------------------
_ps = PollerState()


def _init_dashboard_backend() -> None:
    """Wire the NodeDashboardBackend with callbacks into the poller state."""
    from app.pollers.dashboard import summarize_dashboard_node as _summarize
    backend = NodeDashboardBackend(
        seeker_detail_cache=_ps.seeker_detail_cache,
        summarize_dashboard_node=lambda node: _summarize(_ps, node),
        ping_host=ping_host,
        check_tcp_port=check_tcp_port,
        get_bwv_cfg=get_bwv_cfg,
        get_bwv_stats=get_bwv_stats,
        normalize_bwv_stats=normalize_bwv_stats,
        build_detail_payload=build_detail_payload,
        logger=logger,
    )
    backend.projection_refresh_seconds = NODE_DASHBOARD_PROJECTION_REFRESH_SECONDS
    _ps.dashboard_backend = backend


_init_dashboard_backend()


# ---------------------------------------------------------------------------
# Backward-compatible module-level aliases for route modules
#
# Route modules do `from app.main import seeker_detail_cache` etc.
# These aliases point directly into the PollerState dicts so mutations
# are visible everywhere (they're the same dict objects).
# ---------------------------------------------------------------------------
seeker_detail_cache = _ps.seeker_detail_cache
ping_samples_by_node = _ps.ping_samples_by_node
ping_snapshot_by_node = _ps.ping_snapshot_by_node
consecutive_misses_by_node = _ps.consecutive_misses_by_node
dn_ping_snapshots = _ps.dn_ping_snapshots
service_status_cache = _ps.service_status_cache
node_dashboard_backend = _ps.dashboard_backend


# Wrapper functions that inject _ps into the poller/service functions
# so route modules can call them without knowing about PollerState.

def serialize_node(node, health):  # noqa: F811 — intentional redefinition
    from app.services.node_health import serialize_node as _impl
    return _impl(_ps, node, health)

async def get_node_or_404(node_id, db):  # noqa: F811
    from app.services.node_health import get_node_or_404 as _impl
    return await _impl(node_id, db)

async def refresh_nodes(nodes, db):  # noqa: F811
    from app.services.node_health import refresh_nodes as _impl
    return await _impl(_ps, nodes, db)

async def request_node_telemetry(node, emit_logs=True):  # noqa: F811
    from app.services.node_health import request_node_telemetry as _impl
    return await _impl(node, emit_logs=emit_logs)

async def refresh_seeker_detail_for_node(node):  # noqa: F811
    from app.pollers.seeker import refresh_seeker_detail_for_node as _impl
    return await _impl(_ps, node)

async def summarize_dashboard_node(node):  # noqa: F811
    from app.pollers.dashboard import summarize_dashboard_node as _impl
    return await _impl(_ps, node)

async def probe_discovered_node_detail(source_node, *, site_id, site_ip, level, surfaced_by_site_id, surfaced_by_name):  # noqa: F811
    from app.pollers.dashboard import probe_discovered_node_detail as _impl
    return await _impl(_ps, source_node, site_id=site_id, site_ip=site_ip, level=level, surfaced_by_site_id=surfaced_by_site_id, surfaced_by_name=surfaced_by_name)

def normalize_node_dashboard_window(window_seconds):  # noqa: F811
    from app.pollers.dashboard import normalize_node_dashboard_window as _impl
    return _impl(window_seconds)

def apply_windowed_detail_summary(detail, *, window_metrics):  # noqa: F811
    from app.pollers.dashboard import apply_windowed_detail_summary as _impl
    return _impl(detail, window_metrics=window_metrics)

def get_serialized_node_dashboard_cache(window_seconds=None):  # noqa: F811
    from app.pollers.dashboard import get_serialized_node_dashboard_cache as _impl
    return _impl(_ps, window_seconds)

def merge_service_payload(service):  # noqa: F811
    from app.pollers.services import merge_service_payload as _impl
    return _impl(_ps, service)

def summarize_service_statuses(services):  # noqa: F811
    from app.pollers.services import summarize_service_statuses as _impl
    return _impl(services)

async def check_service(service):  # noqa: F811
    from app.pollers.services import check_service as _impl
    return await _impl(service)


# ---------------------------------------------------------------------------
# Lifespan — replaces @app.on_event("startup") / @app.on_event("shutdown")
# ---------------------------------------------------------------------------

async def _warm_caches_from_redis() -> None:
    """Populate in-memory caches from Redis on startup for instant data availability."""
    # Seeker detail cache — eliminates the 15s cold-start delay
    seeker_entries = await state_manager.get_all_seeker_cache()
    for node_id_str, detail in seeker_entries.items():
        try:
            _ps.seeker_detail_cache[int(node_id_str)] = detail
        except (ValueError, TypeError):
            pass
    if seeker_entries:
        logger.info("Warmed seeker cache from Redis: %d entries", len(seeker_entries))

    # Service status cache — services show status immediately instead of "Pending first check"
    service_entries = await state_manager.get_all_service_states()
    for svc_id_str, state in service_entries.items():
        try:
            _ps.service_status_cache[int(svc_id_str)] = state
        except (ValueError, TypeError):
            pass
    if service_entries:
        logger.info("Warmed service cache from Redis: %d entries", len(service_entries))

    # Node states — pre-populate dashboard backend so first projection has data
    node_states = await state_manager.get_all_node_states()
    dn_states = await state_manager.get_all_dn_states()
    if node_states or dn_states:
        logger.info(
            "Warmed node state from Redis: %d anchors, %d discovered",
            len(node_states), len(dn_states),
        )


async def _cancel_task(task: asyncio.Task | None) -> None:
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.pollers.ping import ping_monitor_loop
    from app.pollers.seeker import seeker_polling_loop, site_name_resolution_loop
    from app.pollers.dn_seeker import dn_seeker_polling_loop
    from app.pollers.services import service_polling_loop
    from app.pollers.dashboard import node_dashboard_polling_loop
    from app.pollers.charts import charts_polling_loop

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await get_redis()

    # Dedicated thread pool for blocking I/O (ping, TCP checks, nslookup)
    _blocking_io_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=40, thread_name_prefix="smp-blocking-io"
    )
    loop = asyncio.get_running_loop()
    loop.set_default_executor(_blocking_io_pool)

    # Warm caches from Redis so the dashboard has data immediately on restart
    await _warm_caches_from_redis()

    async def _start_after(delay_s: float, coro):
        """Start a polling coroutine after an initial delay to stagger load."""
        await asyncio.sleep(delay_s)
        await coro

    # Stagger starts to avoid thundering herd
    _ps.ping_monitor_task = asyncio.create_task(ping_monitor_loop(_ps))           # immediate — lightweight
    _ps.node_dashboard_poll_task = asyncio.create_task(
        _start_after(0.5, node_dashboard_polling_loop(_ps)))
    _ps.seeker_poll_task = asyncio.create_task(
        _start_after(1.0, seeker_polling_loop(_ps)))
    _ps.dn_seeker_poll_task = asyncio.create_task(
        _start_after(2.0, dn_seeker_polling_loop(_ps)))
    _ps.site_name_resolution_task = asyncio.create_task(
        _start_after(3.0, site_name_resolution_loop(_ps)))
    _ps.service_poll_task = asyncio.create_task(
        _start_after(4.0, service_polling_loop(_ps)))
    _ps.charts_poll_task = asyncio.create_task(
        _start_after(5.0, charts_polling_loop(_ps)))

    yield

    await _cancel_task(_ps.ping_monitor_task)
    await _cancel_task(_ps.seeker_poll_task)
    await _cancel_task(_ps.site_name_resolution_task)
    await _cancel_task(_ps.dn_seeker_poll_task)
    await _cancel_task(_ps.service_poll_task)
    await _cancel_task(_ps.node_dashboard_poll_task)
    await _cancel_task(_ps.charts_poll_task)
    await close_redis()
    _blocking_io_pool.shutdown(wait=False)


# ---------------------------------------------------------------------------
# App creation + router mounting
# ---------------------------------------------------------------------------


class StaticCacheMiddleware:
    """Add Cache-Control headers to /static/ responses.

    Templates use cache-busting query strings (?v=...) so a 24-hour cache
    is safe — browsers fetch fresh assets after each deploy.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/static/"):
            async def send_with_cache(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"cache-control", b"public, max-age=86400"))
                    message["headers"] = headers
                await send(message)
            await self.app(scope, receive, send_with_cache)
        else:
            await self.app(scope, receive, send)


app = FastAPI(title="Seeker Management Platform", version="0.1.0", lifespan=lifespan)
app.add_middleware(StaticCacheMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")

from app.routes.pages import router as pages_router
from app.routes.system import router as system_router
from app.routes.nodes import router as nodes_router
from app.routes.services import router as services_router
from app.routes.dashboard import router as dashboard_router
from app.routes.topology import router as topology_router
from app.routes.maps import router as maps_router
from app.routes.discovery import router as discovery_router
from app.routes.stream import router as stream_router
from app.routes.charts import router as charts_router

app.include_router(pages_router)
app.include_router(system_router)
app.include_router(nodes_router)
app.include_router(services_router)
app.include_router(dashboard_router)
app.include_router(topology_router)
app.include_router(discovery_router)
app.include_router(maps_router)
app.include_router(stream_router)
app.include_router(charts_router)
