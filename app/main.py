import asyncio
from collections import deque
from datetime import datetime, timezone
import logging
import platform
import re
import socket
import subprocess
import time
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.models import DiscoveredNode, Node, ServiceCheck
from app.node_dashboard_backend import NodeDashboardBackend
from app.node_discovery_service import refresh_discovered_inventory
from app.seeker_api import (
    build_detail_payload,
    get_bwv_cfg,
    get_bwv_stats,
    normalize_bwv_stats,
    resolve_site_name_map,
)
from app.topology import normalize_topology_location
from app.redis_client import get_redis, close_redis
from app import state_manager

# --- Route modules ---
from app.routes.pages import router as pages_router
from app.routes.system import router as system_router
from app.routes.nodes import router as nodes_router
from app.routes.services import router as services_router
from app.routes.dashboard import router as dashboard_router
from app.routes.topology import router as topology_router
from app.routes.maps import router as maps_router
from app.routes.discovery import router as discovery_router
from app.routes.stream import router as stream_router

app = FastAPI(title="Seeker Management Platform", version="0.1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include all route modules
app.include_router(pages_router)
app.include_router(system_router)
app.include_router(nodes_router)
app.include_router(services_router)
app.include_router(dashboard_router)
app.include_router(topology_router)
app.include_router(maps_router)
app.include_router(discovery_router)
app.include_router(stream_router)

logger = logging.getLogger(__name__)
HEALTH_CHECK_TIMEOUT_SECONDS = 1.0
STATUS_PRIORITY = {
    "online": 0,
    "degraded": 1,
    "offline": 2,
    "disabled": 3,
}
DASHBOARD_STATUS_PRIORITY = {
    "healthy": 0,
    "degraded": 1,
    "offline": 2,
}
DASHBOARD_TELEMETRY_TIMEOUT_SECONDS = 3.0
PING_HISTORY_SAMPLES = 24          # 24 bursts × 15 s = 6 min rolling window
PING_PROBES_PER_BURST = 3
PING_INTERVAL_SECONDS = 5.0
SEEKER_POLL_INTERVAL_SECONDS = 5.0
DN_SEEKER_POLL_INTERVAL_SECONDS = 5.0
SERVICE_POLL_INTERVAL_SECONDS = 30.0
SERVICE_CHECK_TIMEOUT_SECONDS = 5.0
NODE_DASHBOARD_FAST_REFRESH_SECONDS = 1.0
NODE_DASHBOARD_PROJECTION_REFRESH_SECONDS = 5.0
NODE_DASHBOARD_WINDOW_OPTIONS = {10, 30, 60, 300, 1800, 3600}
ping_samples_by_node: dict[int, deque[int]] = {}
ping_snapshot_by_node: dict[int, dict[str, object]] = {}
consecutive_misses_by_node: dict[int, int] = {}
next_ping_at_by_node: dict[int, float] = {}
ping_monitor_task: asyncio.Task | None = None
# DN ping state — keyed by site_id string
dn_ping_samples: dict[str, deque[int]] = {}
dn_ping_snapshots: dict[str, dict[str, object]] = {}
dn_consecutive_misses: dict[str, int] = {}
dn_next_ping_at: dict[str, float] = {}
seeker_detail_cache: dict[int, dict[str, object]] = {}
seeker_poll_task: asyncio.Task | None = None
dn_seeker_poll_task: asyncio.Task | None = None
service_status_cache: dict[int, dict[str, object]] = {}
service_poll_task: asyncio.Task | None = None
node_dashboard_poll_task: asyncio.Task | None = None


def normalize_node_dashboard_window(window_seconds: int | None) -> int:
    try:
        normalized = int(window_seconds or 60)
    except (TypeError, ValueError):
        return 60
    return normalized if normalized in NODE_DASHBOARD_WINDOW_OPTIONS else 60


def apply_windowed_detail_summary(
    detail: dict[str, object],
    *,
    window_metrics: dict[str, object],
) -> dict[str, object]:
    node_summary = dict(detail.get("node_summary") or {})
    avg_latency_ms = window_metrics.get("avg_latency_ms")
    avg_tx_bps = window_metrics.get("avg_tx_bps")
    avg_rx_bps = window_metrics.get("avg_rx_bps")
    rtt_state = str(window_metrics.get("rtt_state") or "")

    if avg_latency_ms is not None:
        node_summary["latency_ms"] = avg_latency_ms
    if avg_tx_bps is not None:
        node_summary["tx_bps"] = avg_tx_bps
    if avg_rx_bps is not None:
        node_summary["rx_bps"] = avg_rx_bps

    node_summary["avg_latency_ms"] = window_metrics.get("avg_latency_ms")
    node_summary["latest_latency_ms"] = window_metrics.get("latest_latency_ms")
    node_summary["rtt_baseline_ms"] = window_metrics.get("rtt_baseline_ms")
    node_summary["rtt_deviation_pct"] = window_metrics.get("rtt_deviation_pct")
    node_summary["rtt_state"] = rtt_state or node_summary.get("rtt_state") or "good"
    node_summary["refresh_window_seconds"] = window_metrics.get("refresh_window_seconds")

    health_by_rtt_state = {
        "good": "Healthy",
        "warn": "Degraded",
        "down": "Critical",
    }
    if rtt_state:
        node_summary["health_state"] = health_by_rtt_state.get(rtt_state, node_summary.get("health_state"))

    return {
        **detail,
        "node_summary": node_summary,
    }




def check_tcp_port(host: str, port: int) -> dict[str, int | bool | None]:
    start_time = time.perf_counter()

    try:
        with socket.create_connection((host, port), timeout=HEALTH_CHECK_TIMEOUT_SECONDS):
            return {
                "reachable": True,
                "latency_ms": round((time.perf_counter() - start_time) * 1000),
            }
    except OSError:
        return {"reachable": False, "latency_ms": None}


def ping_host(host: str) -> dict[str, int | bool | None]:
    system_name = platform.system().lower()
    if system_name == "windows":
        command = ["ping", "-n", "1", "-w", "1000", host]
    else:
        command = ["ping", "-c", "1", "-W", "1", host]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return {"reachable": False, "latency_ms": None}

    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        return {"reachable": False, "latency_ms": None}

    patterns = [
        r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms",
        r"Average = (\d+)\s*ms",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            try:
                return {"reachable": True, "latency_ms": round(float(match.group(1)))}
            except ValueError:
                break

    # Exit code 0 but no latency found — likely "Destination host unreachable" or similar
    return {"reachable": False, "latency_ms": None}


def build_ping_snapshot(node_id: int, ping_result: dict[str, int | bool | None]) -> dict[str, object]:
    samples = ping_samples_by_node.setdefault(node_id, deque(maxlen=PING_HISTORY_SAMPLES))
    ping_ok = bool(ping_result["reachable"])
    latency_ms = ping_result["latency_ms"] if ping_ok else None

    if ping_ok and latency_ms is not None:
        samples.append(int(latency_ms))

    average_ms = round(sum(samples) / len(samples)) if samples else None

    # --- consecutive-miss state model ---
    if ping_ok:
        consecutive_misses_by_node[node_id] = 0
    else:
        consecutive_misses_by_node[node_id] = consecutive_misses_by_node.get(node_id, 0) + 1

    misses = consecutive_misses_by_node[node_id]

    if misses >= 5:
        state = "down"
    elif misses >= 3:
        state = "warn"
    else:
        state = "good"

    snapshot = {
        "ping_ok": ping_ok,
        "latency_ms": latency_ms,
        "avg_latency_ms": average_ms,
        "state": state,
        "consecutive_misses": misses,
        "updated_at": datetime.now(timezone.utc),
    }
    ping_snapshot_by_node[node_id] = snapshot
    return snapshot


def get_ping_snapshot(node: Node) -> dict[str, object]:
    snapshot = ping_snapshot_by_node.get(node.id)
    if snapshot is not None:
        return snapshot

    return build_ping_snapshot(node.id, ping_host(node.host))


def build_dn_ping_snapshot(site_id: str, ping_result: dict[str, int | bool | None]) -> dict[str, object]:
    """Build ping snapshot for a discovered node, keyed by site_id."""
    samples = dn_ping_samples.setdefault(site_id, deque(maxlen=PING_HISTORY_SAMPLES))
    ping_ok = bool(ping_result["reachable"])
    latency_ms = ping_result["latency_ms"] if ping_ok else None

    if ping_ok and latency_ms is not None:
        samples.append(int(latency_ms))

    average_ms = round(sum(samples) / len(samples)) if samples else None

    if ping_ok:
        dn_consecutive_misses[site_id] = 0
    else:
        dn_consecutive_misses[site_id] = dn_consecutive_misses.get(site_id, 0) + 1

    misses = dn_consecutive_misses[site_id]

    if misses >= 5:
        state = "down"
    elif misses >= 3:
        state = "warn"
    else:
        state = "good"

    snapshot = {
        "ping_ok": ping_ok,
        "latency_ms": latency_ms,
        "avg_latency_ms": average_ms,
        "state": state,
        "consecutive_misses": misses,
        "updated_at": datetime.now(timezone.utc),
    }
    dn_ping_snapshots[site_id] = snapshot
    return snapshot


async def ping_node_single(host: str) -> dict[str, int | bool | None]:
    """Send a single ICMP probe and return latency."""
    result = await asyncio.to_thread(ping_host, host)
    return {"reachable": bool(result.get("reachable")), "latency_ms": result.get("latency_ms")}


async def ping_monitor_loop() -> None:
    """Per-node ping scheduler — ticks every second, pings only nodes that are due."""
    import time as _time

    while True:
        now = _time.monotonic()
        db = SessionLocal()
        try:
            nodes = db.scalars(select(Node).order_by(Node.id)).all()
        finally:
            db.close()

        # Only consider nodes that are enabled AND have ping_enabled
        pingable = [n for n in nodes if n.enabled and n.ping_enabled]

        # Determine which nodes are due for a ping
        due_nodes = []
        for node in pingable:
            deadline = next_ping_at_by_node.get(node.id, 0.0)
            if now >= deadline:
                due_nodes.append(node)

        if due_nodes:
            burst_results = await asyncio.gather(
                *(ping_node_single(node.host) for node in due_nodes),
                return_exceptions=True,
            )
            tick = _time.monotonic()
            for node, result in zip(due_nodes, burst_results):
                interval = max(node.ping_interval_seconds, 1)
                next_ping_at_by_node[node.id] = tick + interval
                if isinstance(result, Exception):
                    build_ping_snapshot(node.id, {"reachable": False, "latency_ms": None})
                else:
                    build_ping_snapshot(node.id, result)

        # --- Discovered Node pings ---
        db2 = SessionLocal()
        try:
            dns = db2.scalars(
                select(DiscoveredNode).where(
                    DiscoveredNode.host.isnot(None),
                    DiscoveredNode.map_view_id.isnot(None),
                )
            ).all()
            dn_due: list[tuple[str, str]] = []  # (site_id, host)
            for dn in dns:
                if not dn.host:
                    continue
                deadline = dn_next_ping_at.get(dn.site_id, 0.0)
                if now >= deadline:
                    dn_due.append((dn.site_id, dn.host))
        finally:
            db2.close()

        if dn_due:
            dn_results = await asyncio.gather(
                *(ping_node_single(host) for _, host in dn_due),
                return_exceptions=True,
            )
            tick = _time.monotonic()
            for (site_id, _host), result in zip(dn_due, dn_results):
                dn_next_ping_at[site_id] = tick + PING_INTERVAL_SECONDS
                if isinstance(result, Exception):
                    build_dn_ping_snapshot(site_id, {"reachable": False, "latency_ms": None})
                else:
                    build_dn_ping_snapshot(site_id, result)

        await asyncio.sleep(1.0)


async def compute_node_status(node: Node) -> dict[str, object]:
    if not node.enabled:
        return {
            "status": "disabled",
            "latency_ms": None,
            "last_checked": node.last_checked,
            "web_ok": False,
            "ssh_ok": False,
            "ping_ok": False,
            "ping_state": "down",
            "ping_avg_ms": None,
        }

    checked_at = datetime.now(timezone.utc)
    ping_snapshot = get_ping_snapshot(node)
    web_check, ssh_check = await asyncio.gather(
        asyncio.to_thread(check_tcp_port, node.host, node.web_port),
        asyncio.to_thread(check_tcp_port, node.host, node.ssh_port),
    )
    reachable_ports = [result for result in (web_check, ssh_check) if result["reachable"]]

    if len(reachable_ports) == 2:
        status_value = "online"
    elif len(reachable_ports) == 1:
        status_value = "degraded"
    else:
        status_value = "offline"

    latency_ms = ping_snapshot["latency_ms"] if ping_snapshot["ping_ok"] else None

    return {
        "status": status_value,
        "latency_ms": latency_ms,
        "last_checked": checked_at,
        "web_ok": bool(web_check["reachable"]),
        "ssh_ok": bool(ssh_check["reachable"]),
        "ping_ok": bool(ping_snapshot["ping_ok"]),
        "ping_state": str(ping_snapshot["state"]),
        "ping_avg_ms": ping_snapshot["avg_latency_ms"],
    }


def serialize_node(node: Node, health: dict[str, object]) -> dict[str, object]:
    detail = seeker_detail_cache.get(node.id) or {}
    config_summary = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
    node_summary = detail.get("node") if isinstance(detail.get("node"), dict) else {}
    version = config_summary.get("version") or "--"
    return {
        "id": node.id,
        "name": node.name,
        "node_id": node.node_id,
        "version": version,
        "host": node.host,
        "web_port": node.web_port,
        "ssh_port": node.ssh_port,
        "location": node.location,
        "include_in_topology": node.include_in_topology,
        "topology_level": node.topology_level,
        "topology_unit": node.topology_unit,
        "topology_location": normalize_topology_location(node.location),
        "enabled": node.enabled,
        "notes": node.notes,
        "api_username": node.api_username,
        "api_password": node.api_password,
        "api_use_https": node.api_use_https,
        "ping_enabled": node.ping_enabled,
        "ping_interval_seconds": node.ping_interval_seconds,
        "status": health["status"],
        "latency_ms": node.latency_ms,
        "last_checked": node.last_checked.isoformat() if node.last_checked else None,
        "last_telemetry_pull": node_summary.get("last_telemetry_pull"),
        "web_ok": health["web_ok"],
        "ssh_ok": health["ssh_ok"],
        "ping_ok": health["ping_ok"],
        "ping_state": health["ping_state"],
        "ping_avg_ms": health["ping_avg_ms"],
    }



def apply_health_to_node(node: Node, health: dict[str, object]) -> None:
    node.latency_ms = health["latency_ms"]
    node.last_checked = health["last_checked"]


def build_dashboard_status(node_status: str, normalized: dict[str, object] | None) -> str:
    if node_status in {"offline", "disabled"}:
        return "offline"

    if normalized and normalized.get("is_active"):
        return "healthy" if node_status == "online" else "degraded"

    return "degraded"


def count_active_sites(locks: object) -> tuple[int, int]:
    if not isinstance(locks, list):
        return (0, 0)

    total = len(locks)
    up = 0

    for value in locks:
        try:
            site_mask = int(value)
        except (TypeError, ValueError):
            site_mask = 0

        if site_mask > 0:
            up += 1

    return (up, total)


def count_wan_links(values: object) -> tuple[int, int]:
    if not isinstance(values, list):
        return (0, 0)

    total = len(values)
    up = sum(1 for value in values if str(value) == "1")
    return (up, total)


async def refresh_node(node: Node) -> dict[str, object]:
    health = await compute_node_status(node)
    apply_health_to_node(node, health)
    return serialize_node(node, health)


async def refresh_nodes(nodes: list[Node], db: Session) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for node in nodes:
        if node.enabled and node.api_username and node.api_password:
            try:
                await refresh_seeker_detail_for_node(node)
            except Exception:
                logger.exception("Seeker refresh failed for node %s", node.id)
        try:
            payloads.append(await refresh_node(node))
        except Exception:
            logger.exception("Node health refresh failed for node %s", node.id)
            fallback_health = {
                "status": "offline",
                "latency_ms": None,
                "last_checked": datetime.now(timezone.utc),
                "web_ok": False,
                "ssh_ok": False,
                "ping_ok": False,
                "ping_state": "down",
                "ping_avg_ms": None,
            }
            apply_health_to_node(node, fallback_health)
            payloads.append(serialize_node(node, fallback_health))
    try:
        await node_dashboard_backend.refresh_cache(db, nodes)
    except Exception:
        logger.exception("Node dashboard cache refresh failed during node refresh")
        node_dashboard_backend.mark_cache_refresh_failed()
    db.commit()
    return sorted(
        payloads,
        key=lambda node: (STATUS_PRIORITY.get(str(node["status"]), 99), str(node["name"]).lower()),
    )


def get_node_or_404(node_id: int, db: Session) -> Node:
    node = db.get(Node, node_id)

    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")

    return node


async def request_node_telemetry(node: Node, emit_logs: bool = True) -> dict[str, object]:
    stats_result = await get_bwv_stats(node, emit_logs=emit_logs)
    if stats_result.get("status") != "ok":
        return {
            "status": "error",
            "rc": stats_result.get("rc"),
            "message": stats_result.get("message", "Unable to retrieve telemetry"),
        }

    telemetry_data = stats_result.get("raw") or {}
    return {
        "status": "ok",
        "rc": 0,
        "normalized": normalize_bwv_stats(telemetry_data),
        "telemetry": telemetry_data,
        "node_id": node.id,
        "node_name": node.name,
    }


async def load_node_detail(node: Node) -> dict[str, object]:
    health = await compute_node_status(node)
    apply_health_to_node(node, health)

    cfg_result, stats_result, learnt_routes_result = await asyncio.gather(
        get_bwv_cfg(node),
        get_bwv_stats(node),
        get_bwv_stats(node, extra_args={"learntRoutes": "1"}),
    )

    detail = build_detail_payload(
        node,
        node_health=health,
        cfg_result=cfg_result,
        stats_result=stats_result,
        learnt_routes_result=learnt_routes_result,
    )

    known_site_names: dict[str, str] = {}
    for cached_detail in seeker_detail_cache.values():
        cached_cfg = cached_detail.get("config_summary") if isinstance(cached_detail, dict) else {}
        if not isinstance(cached_cfg, dict):
            continue
        cached_site_id = str(cached_cfg.get("site_id") or "").strip()
        cached_site_name = str(cached_cfg.get("site_name") or "").strip()
        if cached_site_id and cached_site_id != "--" and cached_site_name and cached_site_name != "--":
            known_site_names[cached_site_id] = cached_site_name

    config_summary = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
    current_site_id = str(config_summary.get("site_id") or "").strip()
    current_site_name = str(config_summary.get("site_name") or "").strip()
    if current_site_id and current_site_id != "--" and current_site_name and current_site_name != "--":
        known_site_names[current_site_id] = current_site_name

    site_name_map = await resolve_site_name_map(node, detail.get("tunnels") or [], known_site_names)

    for row in detail.get("tunnels") or []:
        site_id = str(row.get("mate_site_id") or "").strip()
        if site_id in site_name_map:
            row["site_name"] = site_name_map[site_id]

    for row in detail.get("active_sites") or []:
        site_id = str(row.get("site_id") or "").strip()
        if site_id in site_name_map:
            row["site_name"] = site_name_map[site_id]

    tunnels = detail.get("tunnels")
    if isinstance(tunnels, list):
        tunnels.sort(
            key=lambda row: (
                0
                if str(row.get("ping") or "").strip().lower() == "up"
                and str(row.get("mate_index") or "").strip() == "0"
                else 1,
                0 if str(row.get("ping") or "").strip().lower() == "up" else 1,
                int(row.get("mate_index")) if str(row.get("mate_index") or "").isdigit() else 999999,
            )
        )

    return detail


async def refresh_seeker_detail_for_node(node: Node) -> dict[str, object]:
    detail = await load_node_detail(node)
    cached = seeker_detail_cache.get(node.id) if isinstance(seeker_detail_cache.get(node.id), dict) else {}
    errors = detail.get("errors") if isinstance(detail.get("errors"), dict) else {}
    raw = detail.get("raw") if isinstance(detail.get("raw"), dict) else {}
    cached_raw = cached.get("raw") if isinstance(cached.get("raw"), dict) else {}

    if errors.get("config") and isinstance(cached.get("config_summary"), dict) and cached.get("config_summary"):
        detail["config_summary"] = dict(cached.get("config_summary") or {})
        raw["bwv_cfg"] = cached_raw.get("bwv_cfg") or raw.get("bwv_cfg") or {}

    if errors.get("stats"):
        for key in ("active_sites", "tunnels", "channels"):
            if isinstance(cached.get(key), list) and cached.get(key):
                detail[key] = list(cached.get(key) or [])
        cached_summary = cached.get("node_summary") if isinstance(cached.get("node_summary"), dict) else {}
        current_summary = detail.get("node_summary") if isinstance(detail.get("node_summary"), dict) else {}
        if cached_summary:
            for key in ("tx_bps", "rx_bps", "site_count", "mate_count", "active_site_count", "wan_count", "cpu_avg"):
                if current_summary.get(key) in (None, 0):
                    current_summary[key] = cached_summary.get(key)
            detail["node_summary"] = current_summary
        raw["bwv_stats"] = cached_raw.get("bwv_stats") or raw.get("bwv_stats") or {}

    if errors.get("routes") and isinstance(cached.get("learnt_routes"), list) and cached.get("learnt_routes"):
        detail["learnt_routes"] = list(cached.get("learnt_routes") or [])
        raw["bwv_stats_learnt_routes"] = cached_raw.get("bwv_stats_learnt_routes") or raw.get("bwv_stats_learnt_routes") or {}

    cached_node = cached.get("node") if isinstance(cached.get("node"), dict) else {}
    current_node = detail.get("node") if isinstance(detail.get("node"), dict) else {}
    if cached_node.get("last_telemetry_pull") and not current_node.get("last_telemetry_pull"):
        current_node["last_telemetry_pull"] = cached_node.get("last_telemetry_pull")
    detail["node"] = current_node
    detail["raw"] = raw
    detail["cached_at"] = datetime.now(timezone.utc).isoformat()
    seeker_detail_cache[node.id] = detail
    return detail


async def seeker_polling_loop() -> None:
    while True:
        db = SessionLocal()
        try:
            nodes = db.scalars(select(Node).order_by(Node.id)).all()
        finally:
            db.close()

        enabled_nodes = [node for node in nodes if node.enabled and node.api_username and node.api_password]
        if enabled_nodes:
            results = await asyncio.gather(
                *(refresh_seeker_detail_for_node(node) for node in enabled_nodes),
                return_exceptions=True,
            )
            for node, result in zip(enabled_nodes, results):
                if isinstance(result, Exception):
                    cached = seeker_detail_cache.get(node.id, {})
                    seeker_detail_cache[node.id] = {
                        **cached,
                        "node": {
                            "id": node.id,
                            "name": node.name,
                            "host": node.host,
                            "location": node.location,
                            "status": "offline",
                            "web_ok": False,
                            "ssh_ok": False,
                            "last_refresh": node.last_checked.isoformat() if node.last_checked else None,
                            "last_telemetry_pull": None,
                        },
                        "node_summary": cached.get("node_summary", {}),
                        "config_summary": cached.get("config_summary", {}),
                        "mates": cached.get("mates", []),
                        "tunnels": cached.get("tunnels", []),
                        "channels": cached.get("channels", []),
                        "static_routes": cached.get("static_routes", []),
                        "learnt_routes": cached.get("learnt_routes", []),
                        "errors": {
                            "config": "Polling failed",
                            "stats": "Polling failed",
                            "routes": "Polling failed",
                        },
                        "raw": cached.get("raw", {}),
                        "cached_at": datetime.now(timezone.utc).isoformat(),
                    }

            # Backfill node_id from Seeker config_summary.site_id for nodes
            # that don't have it set — makes the inventory exclusion filter
            # work reliably even on cold starts before the cache populates.
            backfill_needed = []
            for node in enabled_nodes:
                if node.node_id:
                    continue
                detail = seeker_detail_cache.get(node.id) or {}
                cfg = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
                cfg_site_id = str(cfg.get("site_id") or "").strip()
                if cfg_site_id and cfg_site_id != "--":
                    backfill_needed.append((node.id, cfg_site_id))

            if backfill_needed:
                bdb = SessionLocal()
                try:
                    for node_id_pk, site_id_val in backfill_needed:
                        db_node = bdb.get(Node, node_id_pk)
                        if db_node and not db_node.node_id:
                            db_node.node_id = site_id_val
                            logger.info("Backfilled node_id=%s for Node.id=%d", site_id_val, node_id_pk)
                    bdb.commit()
                except Exception:
                    logger.exception("Failed to backfill node_id values")
                    bdb.rollback()
                finally:
                    bdb.close()

        await asyncio.sleep(SEEKER_POLL_INTERVAL_SECONDS)


async def dn_seeker_polling_loop() -> None:
    """Background loop that periodically probes DN Seeker APIs.

    Uses credentials inherited from the owning anchor node (source_anchor_node_id).
    Polls both DB-persisted DNs and in-memory cached DNs so that DNs discovered
    via tunnel analysis (but not yet persisted by a submap view) still get probed.
    """
    await asyncio.sleep(10.0)  # initial delay to let AN polling populate first
    while True:
        db = SessionLocal()
        try:
            dns = db.scalars(
                select(DiscoveredNode).where(
                    DiscoveredNode.source_anchor_node_id.isnot(None),
                    DiscoveredNode.host.isnot(None),
                )
            ).all()
            anchor_ids = {dn.source_anchor_node_id for dn in dns if dn.source_anchor_node_id}

            # Also gather anchor IDs referenced by in-memory cached DNs
            for _sid, cached_row in node_dashboard_backend.discovered_node_cache.items():
                if isinstance(cached_row, dict) and cached_row.get("source_anchor_id"):
                    try:
                        anchor_ids.add(int(cached_row["source_anchor_id"]))
                    except (ValueError, TypeError):
                        pass

            anchors_by_id: dict[int, Node] = {}
            if anchor_ids:
                anchor_nodes = db.scalars(select(Node).where(Node.id.in_(anchor_ids))).all()
                anchors_by_id = {n.id: n for n in anchor_nodes}
        finally:
            db.close()

        # Build probe targets from DB DNs
        probe_targets: dict[str, tuple] = {}  # site_id -> (source_node, host, level, parent_sid, parent_name)
        for dn in dns:
            source_node = anchors_by_id.get(dn.source_anchor_node_id) if dn.source_anchor_node_id else None
            if not source_node or not source_node.api_username or not source_node.api_password:
                continue
            host = str(dn.host or "").strip()
            if not host or host == "--":
                continue
            probe_targets[dn.site_id] = (
                source_node, host,
                dn.discovered_level or 2,
                dn.discovered_parent_site_id,
                dn.discovered_parent_name,
            )

        # Add in-memory cached DNs not already covered by DB
        for cached_sid, cached_row in node_dashboard_backend.discovered_node_cache.items():
            if cached_sid in probe_targets:
                continue
            if not isinstance(cached_row, dict):
                continue
            host = str(cached_row.get("host") or "").strip()
            if not host or host == "--":
                continue
            anchor_id = None
            try:
                anchor_id = int(cached_row.get("source_anchor_id") or 0) or None
            except (ValueError, TypeError):
                pass
            if not anchor_id:
                continue
            source_node = anchors_by_id.get(anchor_id)
            if not source_node or not source_node.api_username or not source_node.api_password:
                continue
            probe_targets[cached_sid] = (
                source_node, host,
                int(cached_row.get("level") or 2),
                str(cached_row.get("surfaced_by_site_id") or "") or None,
                str(cached_row.get("surfaced_by_name") or "") or None,
            )

        tasks = []
        for site_id, (source_node, host, level, parent_sid, parent_name) in probe_targets.items():
            tasks.append(
                probe_discovered_node_detail(
                    source_node,
                    site_id=site_id,
                    site_ip=host,
                    level=level,
                    surfaced_by_site_id=parent_sid,
                    surfaced_by_name=parent_name,
                )
            )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await asyncio.sleep(DN_SEEKER_POLL_INTERVAL_SECONDS)


def build_service_snapshot(
    service: ServiceCheck,
    *,
    status: str,
    message: str,
    latency_ms: int | None = None,
    http_status: int | None = None,
    resolved_addresses: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": service.id,
        "name": service.name,
        "service_type": service.service_type,
        "target": service.target,
        "enabled": service.enabled,
        "status": status,
        "message": message,
        "latency_ms": latency_ms,
        "http_status": http_status,
        "resolved_addresses": resolved_addresses or [],
        "last_checked": datetime.now(timezone.utc).isoformat(),
    }


async def check_url_service(service: ServiceCheck) -> dict[str, object]:
    target = service.target.strip()
    if not target:
        return build_service_snapshot(service, status="failed", message="Missing URL target")

    parsed = urlparse(target)
    if not parsed.scheme or not parsed.netloc:
        return build_service_snapshot(service, status="failed", message="Invalid URL")

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            verify=False,
            timeout=SERVICE_CHECK_TIMEOUT_SECONDS,
        ) as client:
            response = await client.get(target)
    except httpx.TimeoutException:
        return build_service_snapshot(service, status="failed", message="Timed out")
    except httpx.HTTPError as exc:
        return build_service_snapshot(service, status="failed", message=str(exc))

    latency_ms = round((time.perf_counter() - start) * 1000)
    if response.status_code < 400 or response.status_code == 403:
        status_value = "healthy"
        message = f"HTTP {response.status_code}"
    elif response.status_code < 500:
        status_value = "degraded"
        message = f"HTTP {response.status_code}"
    else:
        status_value = "failed"
        message = f"HTTP {response.status_code}"

    return build_service_snapshot(
        service,
        status=status_value,
        message=message,
        latency_ms=latency_ms,
        http_status=response.status_code,
    )


async def check_dns_service(service: ServiceCheck) -> dict[str, object]:
    target = service.target.strip()
    if not target:
        return build_service_snapshot(service, status="failed", message="Missing DNS target")

    if "|" in target:
        dns_server, probe_name = [part.strip() for part in target.split("|", 1)]
    else:
        parts = target.split()
        dns_server = parts[0]
        probe_name = " ".join(parts[1:]).strip()

    if not dns_server:
        return build_service_snapshot(service, status="failed", message="Missing DNS server")

    # Default to a root-zone NS lookup so a bare server IP still exercises the resolver directly.
    lookup_name = probe_name or "."

    start = time.perf_counter()
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            ["nslookup", "-type=ns", lookup_name, dns_server],
            capture_output=True,
            text=True,
            timeout=SERVICE_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return build_service_snapshot(service, status="failed", message=str(exc))

    output = f"{completed.stdout}\n{completed.stderr}".strip()
    latency_ms = round((time.perf_counter() - start) * 1000)

    if completed.returncode != 0:
        return build_service_snapshot(
            service,
            status="failed",
            message=output or "nslookup failed",
            latency_ms=latency_ms,
        )

    failure_markers = ("timed out", "non-existent domain", "server failed", "refused")
    lowered_output = output.lower()
    if any(marker in lowered_output for marker in failure_markers):
        return build_service_snapshot(
            service,
            status="failed",
            message=output or "Resolver did not answer successfully",
            latency_ms=latency_ms,
        )

    resolved_lines = [line.strip() for line in output.splitlines() if line.strip().lower().startswith(("address:", "addresses:", "internet address"))]
    resolved_addresses = []
    for line in resolved_lines:
        _, _, raw_value = line.partition(":")
        if raw_value:
            resolved_addresses.extend([value.strip() for value in raw_value.split(",") if value.strip()])

    message = f"Resolved {lookup_name} via {dns_server}"
    if resolved_addresses:
        message = f"{message} ({', '.join(resolved_addresses[:3])})"

    return build_service_snapshot(
        service,
        status="healthy",
        message=message,
        latency_ms=latency_ms,
        resolved_addresses=resolved_addresses,
    )


async def check_service(service: ServiceCheck) -> dict[str, object]:
    if not service.enabled:
        return build_service_snapshot(service, status="disabled", message="Check disabled")

    if service.service_type == "dns":
        return await check_dns_service(service)

    return await check_url_service(service)


async def service_polling_loop() -> None:
    while True:
        db = SessionLocal()
        try:
            services = db.scalars(select(ServiceCheck).order_by(ServiceCheck.service_type, ServiceCheck.name, ServiceCheck.id)).all()
        finally:
            db.close()

        if services:
            results = await asyncio.gather(*(check_service(service) for service in services), return_exceptions=True)
            for service, result in zip(services, results):
                if isinstance(result, Exception):
                    service_status_cache[service.id] = build_service_snapshot(
                        service,
                        status="failed",
                        message="Service check failed",
                    )
                else:
                    service_status_cache[service.id] = result

        await asyncio.sleep(SERVICE_POLL_INTERVAL_SECONDS)


def merge_service_payload(service: ServiceCheck) -> dict[str, object]:
    cached = service_status_cache.get(service.id)
    payload = {
        "id": service.id,
        "name": service.name,
        "service_type": service.service_type,
        "target": service.target,
        "enabled": service.enabled,
        "notes": service.notes,
        "created_at": service.created_at.isoformat() if service.created_at else None,
    }
    if cached:
        payload.update(cached)
    else:
        payload.update(
            {
                "status": "disabled" if not service.enabled else "unknown",
                "message": "Pending first check" if service.enabled else "Check disabled",
                "latency_ms": None,
                "http_status": None,
                "resolved_addresses": [],
                "last_checked": None,
            }
        )
    return payload


def summarize_service_statuses(services: list[dict[str, object]]) -> dict[str, int]:
    summary = {
        "total": len(services),
        "healthy": 0,
        "degraded": 0,
        "failed": 0,
        "disabled": 0,
        "unknown": 0,
    }
    for service in services:
        status_value = str(service.get("status") or "unknown")
        summary[status_value] = summary.get(status_value, 0) + 1
    return summary


async def summarize_dashboard_node(node: Node) -> dict[str, object]:
    cached_detail = seeker_detail_cache.get(node.id)
    ping_snapshot = get_ping_snapshot(node)
    normalized = None
    telemetry_data: dict[str, object] = {}
    cfg_summary: dict[str, object] = {}
    cached_node = cached_detail.get("node") if isinstance(cached_detail, dict) and isinstance(cached_detail.get("node"), dict) else {}

    ping_ok = bool(ping_snapshot.get("ping_ok"))
    latency_ms = ping_snapshot.get("latency_ms") if ping_ok else None
    ping_state = str(ping_snapshot.get("state") or "down")
    ping_avg_ms = ping_snapshot.get("avg_latency_ms")
    web_ok = bool(cached_node.get("web_ok")) if cached_node else False
    ssh_ok = bool(cached_node.get("ssh_ok")) if cached_node else False
    node_status = str(cached_node.get("status") or ("online" if ping_ok else "offline"))

    if not ping_ok:
        web_ok = False
        ssh_ok = False
        node_status = "offline"

    if web_ok and ssh_ok:
        node_status = "online"
    elif web_ok or ssh_ok or ping_ok:
        node_status = "degraded"
    elif not ping_ok:
        node_status = "offline"

    if cached_detail:
        cfg_summary = dict(cached_detail.get("config_summary") or {})
        if ping_ok:
            normalized = normalize_bwv_stats(cached_detail.get("raw", {}).get("bwv_stats", {}) or {})
            raw_payload = cached_detail.get("raw", {}).get("bwv_stats")
            if isinstance(raw_payload, dict):
                telemetry_data = raw_payload

    sites_up, sites_total = count_active_sites(telemetry_data.get("rxTunnelLock"))
    wan_up, wan_total = count_wan_links(telemetry_data.get("wanUp"))

    return {
        "id": node.id,
        "name": node.name,
        "host": node.host,
        "web_port": node.web_port,
        "ssh_port": node.ssh_port,
        "web_scheme": "https" if node.api_use_https else "http",
        "ssh_username": node.api_username,
        "site": node.location,
        "status": build_dashboard_status(node_status, normalized),
        "web_ok": web_ok,
        "ssh_ok": ssh_ok,
        "ping_enabled": node.ping_enabled,
        "ping_ok": ping_ok,
        "ping_state": ping_state if node.ping_enabled else "disabled",
        "ping_avg_ms": ping_avg_ms,
        "consecutive_misses": consecutive_misses_by_node.get(node.id, 0),
        "latency_ms": latency_ms,
        "tx_bps": (normalized or {}).get("tx_bps", 0),
        "rx_bps": (normalized or {}).get("rx_bps", 0),
        "wan_tx_bps": (normalized or {}).get("wan_tx_bps", 0),
        "wan_rx_bps": (normalized or {}).get("wan_rx_bps", 0),
        "lan_tx_bps": (normalized or {}).get("lan_tx_bps", 0),
        "lan_rx_bps": (normalized or {}).get("lan_rx_bps", 0),
        "lan_tx_total": (normalized or {}).get("lan_tx_total", "--"),
        "lan_rx_total": (normalized or {}).get("lan_rx_total", "--"),
        "wan_tx_total": (normalized or {}).get("wan_tx_total", "--"),
        "wan_rx_total": (normalized or {}).get("wan_rx_total", "--"),
        "cpu_avg": (normalized or {}).get("cpu_avg"),
        "version": cfg_summary.get("version", "--"),
        "sites_up": sites_up,
        "sites_total": sites_total,
        "wan_up": wan_up,
        "wan_total": wan_total,
        "last_seen": node.last_checked.isoformat() if node.last_checked else None,
    }


node_dashboard_backend = NodeDashboardBackend(
    seeker_detail_cache=seeker_detail_cache,
    summarize_dashboard_node=summarize_dashboard_node,
    ping_host=ping_host,
    check_tcp_port=check_tcp_port,
    get_bwv_cfg=get_bwv_cfg,
    get_bwv_stats=get_bwv_stats,
    normalize_bwv_stats=normalize_bwv_stats,
    build_detail_payload=build_detail_payload,
    logger=logger,
)
node_dashboard_backend.projection_refresh_seconds = NODE_DASHBOARD_PROJECTION_REFRESH_SECONDS
discovered_node_cache = node_dashboard_backend.discovered_node_cache
discovered_node_tombstones = node_dashboard_backend.discovered_node_tombstones
node_dashboard_cache = node_dashboard_backend.node_dashboard_cache


async def probe_discovered_node_detail(
    source_node: Node,
    *,
    site_id: str,
    site_ip: str,
    level: int,
    surfaced_by_site_id: str | None,
    surfaced_by_name: str | None,
) -> dict[str, object] | None:
    return await node_dashboard_backend.probe_discovered_node_detail(
        source_node,
        site_id=site_id,
        site_ip=site_ip,
        level=level,
        surfaced_by_site_id=surfaced_by_site_id,
        surfaced_by_name=surfaced_by_name,
    )


async def build_node_dashboard_payload(db: Session, nodes: list[Node]) -> dict[str, list[dict[str, object]]]:
    return await node_dashboard_backend.build_payload(db, nodes)


async def refresh_node_dashboard_cache_once() -> None:
    db = SessionLocal()
    try:
        nodes = db.scalars(select(Node).order_by(Node.name)).all()
        await node_dashboard_backend.refresh_cache(db, nodes)
    finally:
        db.close()


async def _publish_dashboard_to_redis() -> None:
    """Push per-node and per-DN states from the dashboard cache to Redis."""
    cache = node_dashboard_backend.node_dashboard_cache
    for anchor in cache.get("anchors") or []:
        if isinstance(anchor, dict) and anchor.get("id"):
            await state_manager.update_node_state(anchor["id"], anchor)
    for dn in cache.get("discovered") or []:
        if isinstance(dn, dict) and dn.get("site_id"):
            await state_manager.update_dn_state(dn["site_id"], dn)


def get_serialized_node_dashboard_cache(window_seconds: int | None = None) -> dict[str, object]:
    return node_dashboard_backend.get_serialized_cache(normalize_node_dashboard_window(window_seconds))


async def node_dashboard_polling_loop() -> None:
    while True:
        try:
            await refresh_node_dashboard_cache_once()
            await _publish_dashboard_to_redis()
        except Exception:
            logger.exception("Node dashboard cache refresh failed")
            node_dashboard_backend.mark_cache_refresh_failed()
        await asyncio.sleep(NODE_DASHBOARD_FAST_REFRESH_SECONDS)


@app.on_event("startup")
async def startup_ping_monitor() -> None:
    global ping_monitor_task, seeker_poll_task, dn_seeker_poll_task, service_poll_task, node_dashboard_poll_task
    Base.metadata.create_all(bind=engine)
    await get_redis()  # initialize Redis pool (logs warning if unavailable)

    if ping_monitor_task is None or ping_monitor_task.done():
        ping_monitor_task = asyncio.create_task(ping_monitor_loop())
    if seeker_poll_task is None or seeker_poll_task.done():
        seeker_poll_task = asyncio.create_task(seeker_polling_loop())
    if dn_seeker_poll_task is None or dn_seeker_poll_task.done():
        dn_seeker_poll_task = asyncio.create_task(dn_seeker_polling_loop())
    if service_poll_task is None or service_poll_task.done():
        service_poll_task = asyncio.create_task(service_polling_loop())
    if node_dashboard_poll_task is None or node_dashboard_poll_task.done():
        node_dashboard_poll_task = asyncio.create_task(node_dashboard_polling_loop())


@app.on_event("shutdown")
async def shutdown_ping_monitor() -> None:
    global ping_monitor_task, seeker_poll_task, dn_seeker_poll_task, service_poll_task, node_dashboard_poll_task

    if ping_monitor_task is not None:
        ping_monitor_task.cancel()
        try:
            await ping_monitor_task
        except asyncio.CancelledError:
            pass
        ping_monitor_task = None
    if seeker_poll_task is not None:
        seeker_poll_task.cancel()
        try:
            await seeker_poll_task
        except asyncio.CancelledError:
            pass
        seeker_poll_task = None
    if dn_seeker_poll_task is not None:
        dn_seeker_poll_task.cancel()
        try:
            await dn_seeker_poll_task
        except asyncio.CancelledError:
            pass
        dn_seeker_poll_task = None
    if service_poll_task is not None:
        service_poll_task.cancel()
        try:
            await service_poll_task
        except asyncio.CancelledError:
            pass
        service_poll_task = None
    if node_dashboard_poll_task is not None:
        node_dashboard_poll_task.cancel()
        try:
            await node_dashboard_poll_task
        except asyncio.CancelledError:
            pass
        node_dashboard_poll_task = None

    await close_redis()

