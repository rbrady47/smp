"""Diagnostic code registry and handlers.

Each handler is an async function that receives (args: dict, ps: PollerState)
and returns a dict of results. Handlers are registered in DIAG_HANDLERS
keyed by their code string (e.g. "poller:status").

See docs/DIAG_CODES.md for the full catalog.
"""

from __future__ import annotations

import asyncio
import os
import platform
import socket
import time
from datetime import datetime
from typing import TYPE_CHECKING

from app.db import async_engine
from app.redis_client import get_redis, redis_available

if TYPE_CHECKING:
    from app.poller_state import PollerState


_app_start_time = time.monotonic()


def _task_info(task: asyncio.Task | None) -> dict[str, object]:
    """Summarize an asyncio task's state."""
    if task is None:
        return {"state": "not_started"}
    if task.done():
        exc = task.exception() if not task.cancelled() else None
        return {
            "state": "cancelled" if task.cancelled() else "done",
            "error": str(exc) if exc else None,
        }
    return {"state": "running"}


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------

async def _diag_poller_status(args: dict, ps: PollerState) -> dict:
    """Show all poller task states and intervals."""
    from app.pollers.seeker import SEEKER_POLL_INTERVAL_SECONDS
    from app.pollers.charts import CHARTS_POLL_INTERVAL_SECONDS
    from app.pollers.services import SERVICE_POLL_INTERVAL_SECONDS
    from app.pollers.ping import PING_BURST_INTERVAL_SECONDS
    from app.pollers.dashboard import NODE_DASHBOARD_PROJECTION_REFRESH_SECONDS

    return {
        "pollers": {
            "ping_monitor": {
                **_task_info(ps.ping_monitor_task),
                "interval_s": PING_BURST_INTERVAL_SECONDS,
            },
            "seeker": {
                **_task_info(ps.seeker_poll_task),
                "interval_s": SEEKER_POLL_INTERVAL_SECONDS,
            },
            "site_name_resolution": _task_info(ps.site_name_resolution_task),
            "dn_seeker": _task_info(ps.dn_seeker_poll_task),
            "services": {
                **_task_info(ps.service_poll_task),
                "interval_s": SERVICE_POLL_INTERVAL_SECONDS,
            },
            "node_dashboard": {
                **_task_info(ps.node_dashboard_poll_task),
                "interval_s": NODE_DASHBOARD_PROJECTION_REFRESH_SECONDS,
            },
            "charts": {
                **_task_info(ps.charts_poll_task),
                "interval_s": CHARTS_POLL_INTERVAL_SECONDS,
            },
        },
    }


async def _diag_cache_stats(args: dict, ps: PollerState) -> dict:
    """Show sizes of all in-memory caches."""
    db_backend = ps.dashboard_backend
    db_cache_info = {}
    if db_backend:
        db_cache_info = {
            "anchor_cache_size": len(getattr(db_backend, "_anchor_cache", {})),
            "dn_cache_size": len(getattr(db_backend, "_dn_cache", {})),
        }

    return {
        "caches": {
            "seeker_detail_cache": len(ps.seeker_detail_cache),
            "service_status_cache": len(ps.service_status_cache),
            "ping_snapshot_by_node": len(ps.ping_snapshot_by_node),
            "ping_samples_by_node": len(ps.ping_samples_by_node),
            "dn_ping_snapshots": len(ps.dn_ping_snapshots),
            "dn_ping_samples": len(ps.dn_ping_samples),
            "charts_last_le": len(ps.charts_last_le),
            "charts_raw_last_le": len(ps.charts_raw_last_le),
            **db_cache_info,
        },
    }


async def _diag_cache_detail(args: dict, ps: PollerState) -> dict:
    """Show contents of a specific cache. Args: name (str)."""
    name = args.get("name", "")
    cache_map: dict[str, object] = {
        "seeker_detail_cache": ps.seeker_detail_cache,
        "service_status_cache": ps.service_status_cache,
        "ping_snapshot_by_node": ps.ping_snapshot_by_node,
        "dn_ping_snapshots": ps.dn_ping_snapshots,
        "charts_last_le": ps.charts_last_le,
    }
    if name not in cache_map:
        return {"error": f"Unknown cache: {name}", "available": list(cache_map.keys())}
    cache = cache_map[name]
    # Limit output to first 50 entries
    entries = dict(list(cache.items())[:50]) if isinstance(cache, dict) else str(cache)
    return {"cache": name, "size": len(cache), "entries": entries}


async def _diag_db_pool(args: dict, ps: PollerState) -> dict:
    """Show async engine connection pool stats."""
    pool = async_engine.pool
    return {
        "pool": {
            "size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "pool_class": type(pool).__name__,
        },
    }


async def _diag_redis_status(args: dict, ps: PollerState) -> dict:
    """Show Redis connection status."""
    available = await redis_available()
    info: dict[str, object] = {"available": available}
    if available:
        r = await get_redis()
        if r:
            try:
                server_info = await r.info("server")
                info["redis_version"] = server_info.get("redis_version")
                info["uptime_seconds"] = server_info.get("uptime_in_seconds")
                memory_info = await r.info("memory")
                info["used_memory_human"] = memory_info.get("used_memory_human")
                clients_info = await r.info("clients")
                info["connected_clients"] = clients_info.get("connected_clients")
            except Exception as e:
                info["error"] = str(e)
    return {"redis": info}


async def _diag_system_info(args: dict, ps: PollerState) -> dict:
    """Show Python version, uptime, platform info."""
    uptime_s = time.monotonic() - _app_start_time
    return {
        "system": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "uptime_seconds": round(uptime_s, 1),
            "uptime_human": _format_uptime(uptime_s),
            "time": datetime.now().isoformat(),
        },
    }


async def _diag_node_detail(args: dict, ps: PollerState) -> dict:
    """Show cached seeker detail for a node. Args: node_id (int)."""
    node_id_str = args.get("node_id", "")
    if not node_id_str:
        return {"error": "Required arg: node_id", "example": "node:detail node_id=42"}
    try:
        node_id = int(node_id_str)
    except ValueError:
        return {"error": f"Invalid node_id: {node_id_str}"}
    detail = ps.seeker_detail_cache.get(node_id)
    if detail is None:
        return {"error": f"No cached detail for node_id={node_id}", "cached_ids": list(ps.seeker_detail_cache.keys())}
    return {"node_id": node_id, "detail": detail}


async def _diag_ping_detail(args: dict, ps: PollerState) -> dict:
    """Show ping state for a node. Args: node_id (int)."""
    node_id_str = args.get("node_id", "")
    if not node_id_str:
        return {"error": "Required arg: node_id", "example": "ping:detail node_id=42"}
    try:
        node_id = int(node_id_str)
    except ValueError:
        return {"error": f"Invalid node_id: {node_id_str}"}
    snapshot = ps.ping_snapshot_by_node.get(node_id)
    samples = ps.ping_samples_by_node.get(node_id)
    misses = ps.consecutive_misses_by_node.get(node_id, 0)
    return {
        "node_id": node_id,
        "snapshot": snapshot or "no data",
        "recent_samples": list(samples) if samples else [],
        "consecutive_misses": misses,
    }


async def _diag_help(args: dict, ps: PollerState) -> dict:
    """List all available diag codes."""
    codes = []
    for code, handler in DIAG_HANDLERS.items():
        doc = (handler.__doc__ or "").split("\n")[0].strip()
        codes.append({"code": code, "description": doc})
    return {"available_codes": codes}


def _format_uptime(seconds: float) -> str:
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

DIAG_HANDLERS: dict[str, object] = {
    "help": _diag_help,
    "poller:status": _diag_poller_status,
    "cache:stats": _diag_cache_stats,
    "cache:detail": _diag_cache_detail,
    "db:pool": _diag_db_pool,
    "redis:status": _diag_redis_status,
    "system:info": _diag_system_info,
    "node:detail": _diag_node_detail,
    "ping:detail": _diag_ping_detail,
}


def parse_diag_input(raw: str) -> tuple[str, dict[str, str]]:
    """Parse 'code key=val key=val' into (code, {key: val})."""
    parts = raw.strip().split()
    if not parts:
        return "", {}
    code = parts[0]
    args: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, _, val = part.partition("=")
            args[key] = val
        else:
            # Positional arg treated as first unnamed key
            args.setdefault("_positional", part)
    return code, args
