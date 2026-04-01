import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
import json
import logging
import platform
import re
import socket
import subprocess
import time
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine, get_db
from app.bwvstats_ingest import collect_bwvstats_phase1, get_raw_bwvstats_snapshots
from app.models import (
    DiscoveredNode,
    DiscoveredNodeObservation,
    Node,
    NodeRelationship,
    OperationalMapObject,
    OperationalMapView,
    ServiceCheck,
    TopologyEditorState,
    TopologyLink,
)
from app.node_dashboard_backend import NodeDashboardBackend
from app.node_discovery_service import refresh_discovered_inventory
from app.node_watchlist_projection_service import build_node_watchlist_payload
from app.seeker_api import (
    build_detail_payload,
    get_bwv_cfg,
    get_bwv_stats,
    normalize_bwv_stats,
    extract_learnt_routes_from_stats,
    extract_static_routes_from_cfg,
    resolve_site_name_map,
)
from app.node_discovery_service import _tunnel_row_is_eligible
from app.schemas import (
    NodeCreate,
    NodeUpdate,
    OperationalMapLinkBindingCreate,
    OperationalMapLinkCreate,
    OperationalMapLinkUpdate,
    OperationalMapObjectBindingCreate,
    OperationalMapObjectCreate,
    OperationalMapObjectUpdate,
    OperationalMapViewCreate,
    OperationalMapViewUpdate,
    ServiceCheckCreate,
    TopologyEditorStateUpdate,
    TopologyLinkCreate,
    TopologyLinkUpdate,
)
from app.topology_editor_state_service import get_topology_editor_state_payload, upsert_topology_editor_state
from app.topology import build_mock_topology_payload, build_topology_discovery_payload, normalize_topology_location
import app.operational_map_service as operational_map_service

app = FastAPI(title="Seeker Management Platform", version="0.1.0")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

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
SEEKER_POLL_INTERVAL_SECONDS = 15.0
DN_SEEKER_POLL_INTERVAL_SECONDS = 30.0
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


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="main_dashboard.html",
        context={"page_title": "Seeker Management Platform"},
    )


@app.get("/nodes/dashboard", response_class=HTMLResponse)
async def node_dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"page_title": "Node Dashboard | Seeker Management Platform"},
    )


@app.get("/services/dashboard", response_class=HTMLResponse)
async def services_dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="services_dashboard.html",
        context={"page_title": "Services Dashboard | Seeker Management Platform"},
    )


@app.get("/topology", response_class=HTMLResponse)
async def topology_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="topology.html",
        context={"page_title": "Division C2 Information Network | Seeker Management Platform", "map_view_id": None},
    )


@app.get("/topology/maps/{map_view_id}", response_class=HTMLResponse)
async def topology_submap_page(request: Request, map_view_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    view = db.get(OperationalMapView, map_view_id)
    if not view:
        raise HTTPException(status_code=404, detail="Map view not found")
    page_title = f"{view.name} | Seeker Management Platform"
    return templates.TemplateResponse(
        request=request,
        name="topology.html",
        context={"page_title": page_title, "map_view_id": map_view_id, "map_view_name": view.name},
    )


@app.get("/nodes", response_class=HTMLResponse)
async def nodes_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="nodes.html",
        context={"page_title": "Node Inventory | Seeker Management Platform"},
    )


@app.get("/services", response_class=HTMLResponse)
async def services_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="services.html",
        context={"page_title": "Services | Seeker Management Platform"},
    )


@app.get("/nodes/{node_id}", response_class=HTMLResponse)
async def node_detail_page(node_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    node = get_node_or_404(node_id, db)
    embedded = request.query_params.get("embedded") == "1"
    return templates.TemplateResponse(
        request=request,
        name="node_detail.html",
        context={
            "page_title": f"{node.name} | Anchor Node Detail",
            "node_id": node.id,
            "node_name": node.name,
            "detail_endpoint": f"/api/nodes/{node.id}/detail",
            "detail_kind": "anchor",
            "embedded": embedded,
        },
    )


@app.get("/node/{node_id}", response_class=HTMLResponse)
async def node_detail_alias(node_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return await node_detail_page(node_id, request, db)


@app.get("/nodes/discovered/{site_id}", response_class=HTMLResponse)
async def discovered_node_detail_page(site_id: str, request: Request) -> HTMLResponse:
    cached = node_dashboard_backend.get_cached_discovered_node(site_id) or {}
    node_name = str(cached.get("site_name") or f"Discovered Site {site_id}")
    embedded = request.query_params.get("embedded") == "1"
    return templates.TemplateResponse(
        request=request,
        name="node_detail.html",
        context={
            "page_title": f"{node_name} | Discovered Node Detail",
            "node_id": site_id,
            "node_name": node_name,
            "detail_endpoint": f"/api/discovered-nodes/{site_id}/detail",
            "detail_kind": "discovered",
            "embedded": embedded,
        },
    )


@app.get("/api/status")
async def status_view() -> dict[str, str]:
    return {
        "app": "Seeker Management Platform",
        "version": "0.1.0",
        "hostname": socket.gethostname(),
        "time": datetime.now().isoformat(),
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

        await asyncio.sleep(SEEKER_POLL_INTERVAL_SECONDS)


async def dn_seeker_polling_loop() -> None:
    """Background loop that periodically probes DN Seeker APIs.

    Uses credentials inherited from the owning anchor node (source_anchor_node_id).
    The probe_discovered_node_detail function respects its internal TTL (5 min)
    so we can poll frequently without hammering the Seeker APIs.
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
            anchors_by_id: dict[int, Node] = {}
            if anchor_ids:
                anchor_nodes = db.scalars(select(Node).where(Node.id.in_(anchor_ids))).all()
                anchors_by_id = {n.id: n for n in anchor_nodes}
        finally:
            db.close()

        tasks = []
        for dn in dns:
            source_node = anchors_by_id.get(dn.source_anchor_node_id) if dn.source_anchor_node_id else None
            if not source_node or not source_node.api_username or not source_node.api_password:
                continue
            host = str(dn.host or "").strip()
            if not host or host == "--":
                continue
            tasks.append(
                probe_discovered_node_detail(
                    source_node,
                    site_id=dn.site_id,
                    site_ip=host,
                    level=dn.discovered_level or 2,
                    surfaced_by_site_id=dn.discovered_parent_site_id,
                    surfaced_by_name=dn.discovered_parent_name,
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


def get_serialized_node_dashboard_cache(window_seconds: int | None = None) -> dict[str, object]:
    return node_dashboard_backend.get_serialized_cache(normalize_node_dashboard_window(window_seconds))


async def node_dashboard_polling_loop() -> None:
    while True:
        try:
            await refresh_node_dashboard_cache_once()
        except Exception:
            logger.exception("Node dashboard cache refresh failed")
            node_dashboard_backend.mark_cache_refresh_failed()
        await asyncio.sleep(NODE_DASHBOARD_FAST_REFRESH_SECONDS)


@app.on_event("startup")
async def startup_ping_monitor() -> None:
    global ping_monitor_task, seeker_poll_task, dn_seeker_poll_task, service_poll_task, node_dashboard_poll_task
    Base.metadata.create_all(bind=engine)

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


@app.get("/api/nodes/ping-status")
async def nodes_ping_status(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    """Lightweight endpoint — reads only from in-memory ping cache, no Seeker calls."""
    nodes = db.scalars(select(Node).order_by(Node.id)).all()
    result = []
    for node in nodes:
        snap = ping_snapshot_by_node.get(node.id, {})
        result.append({
            "id": node.id,
            "ping_enabled": node.ping_enabled,
            "ping_state": snap.get("state", "unknown") if node.ping_enabled else "disabled",
            "latency_ms": snap.get("latency_ms"),
            "avg_latency_ms": snap.get("avg_latency_ms"),
            "ping_ok": bool(snap.get("ping_ok", False)),
            "consecutive_misses": snap.get("consecutive_misses", 0),
        })
    return result


@app.get("/api/nodes")
async def list_nodes(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    cached = node_dashboard_backend.get_cached_payload(60)
    anchor_rows_by_id = {
        int(row["id"]): row
        for row in (cached.get("anchors") or [])
        if isinstance(row, dict) and row.get("id") is not None
    }
    result = []
    for node in nodes:
        cached_row = anchor_rows_by_id.get(node.id, {})
        health = {
            "status": cached_row.get("status", "unknown"),
            "latency_ms": cached_row.get("latency_ms"),
            "last_checked": node.last_checked,
            "web_ok": bool(cached_row.get("web_ok", False)),
            "ssh_ok": bool(cached_row.get("ssh_ok", False)),
            "ping_ok": bool(cached_row.get("ping_ok", False)),
            "ping_state": cached_row.get("ping_state", "unknown"),
            "ping_avg_ms": cached_row.get("ping_avg_ms"),
        }
        result.append(serialize_node(node, health))
    return result


@app.post("/api/nodes/refresh")
async def refresh_all_nodes(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    try:
        nodes = db.scalars(select(Node).order_by(Node.name)).all()
        return await refresh_nodes(nodes, db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"/api/nodes/refresh failed: {exc}") from exc


@app.post("/api/nodes/{node_id}/telemetry")
async def node_telemetry(node_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    node = get_node_or_404(node_id, db)
    return await request_node_telemetry(node)


@app.get("/api/nodes/{node_id}/config")
async def node_config(node_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    node = get_node_or_404(node_id, db)
    detail = seeker_detail_cache.get(node.id)
    if not detail:
        return {
            "status": "error",
            "message": "Config not available in cache yet",
            "config_summary": {},
            "mates": [],
            "static_routes": [],
        }
    return {
        "status": "ok",
        "config_summary": detail.get("config_summary", {}),
        "mates": detail.get("mates", []),
        "static_routes": detail.get("static_routes", []),
    }


@app.get("/api/nodes/{node_id}/stats")
async def node_stats(node_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    node = get_node_or_404(node_id, db)
    detail = seeker_detail_cache.get(node.id)
    if not detail:
        return {
            "status": "error",
            "message": "Stats not available in cache yet",
            "normalized": {},
            "tunnels": [],
            "channels": [],
        }
    return {
        "status": "ok",
        "normalized": normalize_bwv_stats(detail.get("raw", {}).get("bwv_stats", {}) or {}),
        "active_sites": detail.get("active_sites", []),
        "tunnels": detail.get("tunnels", []),
        "channels": detail.get("channels", []),
    }


@app.get("/api/nodes/{node_id}/routes")
async def node_routes(node_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    node = get_node_or_404(node_id, db)
    detail = seeker_detail_cache.get(node.id)
    if not detail:
        return {
            "status": "error",
            "static_routes": [],
            "learnt_routes": [],
            "errors": {
                "config": "Routes not available in cache yet",
                "routes": "Routes not available in cache yet",
            },
        }
    return {
        "status": "ok",
        "static_routes": detail.get("static_routes", []),
        "learnt_routes": detail.get("learnt_routes", []),
        "errors": detail.get("errors", {}),
    }


@app.get("/api/nodes/{node_id}/detail")
async def node_detail(
    node_id: int,
    window_seconds: int = Query(default=60),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    node = get_node_or_404(node_id, db)
    detail = seeker_detail_cache.get(node.id)
    if not detail:
        detail = await refresh_seeker_detail_for_node(node)
    db.commit()
    detail_dict = dict(detail)
    detail_site_id = (
        detail_dict.get("config_summary", {}).get("site_id")
        if isinstance(detail_dict.get("config_summary"), dict)
        else None
    )
    window_metrics = node_dashboard_backend.get_row_window_metrics(
        "anchor",
        node_dashboard_backend._anchor_pin_key(node.id),
        normalize_node_dashboard_window(window_seconds),
    )
    if detail_site_id:
        window_metrics["site_id"] = detail_site_id
    return apply_windowed_detail_summary(detail_dict, window_metrics=window_metrics)


@app.get("/api/discovered-nodes/{site_id}/detail")
async def discovered_node_detail(
    site_id: str,
    window_seconds: int = Query(default=60),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    cached = node_dashboard_backend.ensure_discovered_node_cached(db, site_id)

    if not cached:
        raise HTTPException(status_code=404, detail="Discovered node not found")

    detail = cached.get("detail") if isinstance(cached.get("detail"), dict) else {}
    if detail:
        return apply_windowed_detail_summary(
            dict(detail),
            window_metrics=node_dashboard_backend.get_row_window_metrics(
                "discovered",
                site_id,
                normalize_node_dashboard_window(window_seconds),
            ),
        )

    site_ip = str(cached.get("host") or "").strip()
    if not site_ip or site_ip == "--":
        raise HTTPException(status_code=404, detail="Discovered node detail not available")

    parent_site_id = str(cached.get("discovered_parent_site_id") or cached.get("surfaced_by_site_id") or "").strip()
    source_node = node_dashboard_backend.find_source_node_for_discovered_detail(cached, nodes)
    if source_node is None:
        raise HTTPException(status_code=404, detail="Discovered node detail not available")

    probed = await probe_discovered_node_detail(
        source_node,
        site_id=str(site_id),
        site_ip=site_ip,
        level=int(cached.get("discovered_level") or 2),
        surfaced_by_site_id=parent_site_id or None,
        surfaced_by_name=str(cached.get("discovered_parent_name") or "").strip() or None,
    )
    if not probed:
        raise HTTPException(status_code=404, detail="Discovered node detail not available")

    detail = probed.get("detail") if isinstance(probed.get("detail"), dict) else {}
    if not detail:
        raise HTTPException(status_code=404, detail="Discovered node detail not available")
    return apply_windowed_detail_summary(
        dict(detail),
        window_metrics=node_dashboard_backend.get_row_window_metrics(
            "discovered",
            site_id,
            normalize_node_dashboard_window(window_seconds),
        ),
    )


@app.delete("/api/discovered-nodes/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_discovered_node(site_id: str, db: Session = Depends(get_db)) -> Response:
    node_dashboard_backend.delete_discovered_node(db, site_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/discovered-nodes/flush-unreachable")
async def flush_unreachable_discovered_nodes(
    window_seconds: int = Query(default=60),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    payload = node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))
    unreachable_site_ids = [
        str(row.get("site_id", "")).strip()
        for row in payload.get("discovered", [])
        if str(row.get("site_id", "")).strip()
        and str(row.get("ping") or "").strip().lower() != "up"
    ]
    deleted_site_ids = node_dashboard_backend.delete_discovered_nodes(db, unreachable_site_ids)
    return {
        "deleted_site_ids": deleted_site_ids,
        "deleted_count": len(deleted_site_ids),
    }


@app.post("/api/discovered-nodes/flush-discovery")
async def flush_discovery(
    window_seconds: int = Query(default=60),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    existing_payload = node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))
    previous_site_ids = [
        str(row.get("site_id") or "").strip()
        for row in existing_payload.get("discovered", [])
        if str(row.get("site_id") or "").strip()
    ]
    node_dashboard_backend.clear_discovery(db)
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    await refresh_discovered_inventory(node_dashboard_backend, db, nodes)
    refreshed_payload = node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))
    refreshed_site_ids = [
        str(row.get("site_id") or "").strip()
        for row in refreshed_payload.get("discovered", [])
        if str(row.get("site_id") or "").strip()
    ]
    return {
        "deleted_site_ids": previous_site_ids,
        "deleted_count": len(previous_site_ids),
        "rediscovered_site_ids": refreshed_site_ids,
        "rediscovered_count": len(refreshed_site_ids),
    }


@app.post("/api/nodes/flush-all")
async def flush_all_nodes(db: Session = Depends(get_db)) -> dict[str, object]:
    """Wipe all node inventory, discovery data, relationships, and topology editor state."""
    node_count = len(db.scalars(select(Node)).all())
    discovered_count = len(db.scalars(select(DiscoveredNode)).all())

    db.query(DiscoveredNodeObservation).delete()
    db.query(NodeRelationship).delete()
    db.query(DiscoveredNode).delete()
    db.query(Node).delete()
    db.query(TopologyEditorState).delete()
    db.commit()

    ping_samples_by_node.clear()
    ping_snapshot_by_node.clear()
    seeker_detail_cache.clear()
    node_dashboard_backend.discovered_node_cache.clear()
    node_dashboard_backend.discovered_ping_cache.clear()
    node_dashboard_backend.anchor_metric_history.clear()
    node_dashboard_backend.discovered_metric_history.clear()
    node_dashboard_backend.node_dashboard_cache.update({"anchors": [], "discovered": [], "summary": {}})
    node_dashboard_backend.mark_projection_dirty()

    return {
        "deleted_nodes": node_count,
        "deleted_discovered_nodes": discovered_count,
        "status": "ok",
    }


@app.get("/api/nodes/{node_id}/bwvstats/phase1")
async def node_bwvstats_phase1(node_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    node = get_node_or_404(node_id, db)
    return await collect_bwvstats_phase1(node, emit_logs=True)


@app.get("/api/nodes/{node_id}/bwvstats/phase1/raw")
async def node_bwvstats_phase1_raw(node_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    node = get_node_or_404(node_id, db)
    return {
        "status": "ok",
        "node_id": node.id,
        "snapshots": get_raw_bwvstats_snapshots(node.id),
    }


@app.get("/api/dashboard/nodes")
async def dashboard_nodes(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    payloads = await asyncio.gather(*(summarize_dashboard_node(node) for node in nodes))
    db.commit()
    return sorted(
        payloads,
        key=lambda node: (
            DASHBOARD_STATUS_PRIORITY.get(str(node["status"]), 99),
            str(node["name"]).lower(),
        ),
    )


@app.get("/api/dashboard/nodes/watchlist")
async def dashboard_node_watchlist(
    pin_key: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    db.commit()
    return build_node_watchlist_payload(node_dashboard_backend.get_serialized_cache(), pin_key)


@app.get("/api/node-dashboard")
async def node_dashboard_payload(
    window_seconds: int = Query(default=60),
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, object]]]:
    db.commit()
    return node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))


@app.get("/api/topology/discovery")
async def topology_discovery_payload(
    window_seconds: int = Query(default=60),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    db.commit()
    return build_topology_discovery_payload(
        node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds)),
        node_dashboard_backend.get_topology_relationships(db),
    )


@app.get("/api/topology/editor-state")
async def topology_editor_state_payload(db: Session = Depends(get_db)) -> dict[str, object]:
    return get_topology_editor_state_payload(db)


@app.put("/api/topology/editor-state")
async def update_topology_editor_state(
    payload: TopologyEditorStateUpdate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return upsert_topology_editor_state(payload, db)


@app.get("/api/topology/links")
async def list_topology_links(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    links = db.scalars(select(TopologyLink).order_by(TopologyLink.id)).all()
    return [
        {
            "id": link.id,
            "source_entity_id": link.source_entity_id,
            "target_entity_id": link.target_entity_id,
            "source_anchor": link.source_anchor,
            "target_anchor": link.target_anchor,
            "link_type": link.link_type,
            "status_node_id": link.status_node_id,
        }
        for link in links
    ]


@app.post("/api/topology/links", status_code=status.HTTP_201_CREATED)
async def create_topology_link(
    payload: TopologyLinkCreate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    link = TopologyLink(
        source_entity_id=payload.source_entity_id,
        target_entity_id=payload.target_entity_id,
        source_anchor=payload.source_anchor,
        target_anchor=payload.target_anchor,
        link_type=payload.link_type,
        status_node_id=payload.status_node_id,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return {
        "id": link.id,
        "source_entity_id": link.source_entity_id,
        "target_entity_id": link.target_entity_id,
        "source_anchor": link.source_anchor,
        "target_anchor": link.target_anchor,
        "link_type": link.link_type,
        "status_node_id": link.status_node_id,
    }


@app.put("/api/topology/links/{link_id}")
async def update_topology_link(
    link_id: int,
    payload: TopologyLinkUpdate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    link = db.get(TopologyLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(link, key, value)
    db.commit()
    db.refresh(link)
    return {
        "id": link.id,
        "source_entity_id": link.source_entity_id,
        "target_entity_id": link.target_entity_id,
        "source_anchor": link.source_anchor,
        "target_anchor": link.target_anchor,
        "link_type": link.link_type,
        "status_node_id": link.status_node_id,
    }


@app.delete("/api/topology/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topology_link(
    link_id: int,
    db: Session = Depends(get_db),
) -> Response:
    link = db.get(TopologyLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    db.delete(link)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Operational Map (submap) routes — /api/topology/maps
# ---------------------------------------------------------------------------


@app.get("/api/topology/maps")
async def list_map_views(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    return operational_map_service.list_map_views(db)


@app.post("/api/topology/maps", status_code=status.HTTP_201_CREATED)
async def create_map_view(
    payload: OperationalMapViewCreate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return operational_map_service.create_map_view(payload, db)


@app.get("/api/topology/maps/{map_view_id}")
async def get_map_view_detail(
    map_view_id: int,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return operational_map_service.get_map_view_detail(map_view_id, db)


def _resolve_dn_owner_anchor_id(
    candidate_anchor_id: int,
    existing_anchor_id: int | None,
    anchor_site_id_map: dict[int, str],
) -> int:
    """Ownership: AN with lowest site_id wins AN-vs-AN conflicts."""
    if existing_anchor_id is None:
        return candidate_anchor_id
    candidate_sid = anchor_site_id_map.get(candidate_anchor_id, str(candidate_anchor_id))
    existing_sid = anchor_site_id_map.get(existing_anchor_id, str(existing_anchor_id))
    return candidate_anchor_id if candidate_sid < existing_sid else existing_anchor_id


@app.get("/api/topology/maps/{map_view_id}/discovery")
async def get_submap_discovery(
    map_view_id: int,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Return tunnel peers for anchor nodes placed on this submap."""
    map_view = db.get(OperationalMapView, map_view_id)
    if not map_view:
        raise HTTPException(status_code=404, detail="Map view not found")

    placed_objects = db.scalars(
        select(OperationalMapObject).where(
            OperationalMapObject.map_view_id == map_view_id,
            OperationalMapObject.object_type == "node",
        )
    ).all()

    placed_anchor_ids: set[int] = set()
    for obj in placed_objects:
        binding_key = obj.binding_key or ""
        if binding_key.startswith("anchor:"):
            try:
                placed_anchor_ids.add(int(binding_key.split(":")[1]))
            except (ValueError, IndexError):
                pass

    anchor_nodes = db.scalars(
        select(Node).where(Node.id.in_(placed_anchor_ids))
    ).all() if placed_anchor_ids else []

    # Build anchor site_id lookup for ownership resolution
    anchor_site_id_map: dict[int, str] = {}
    for node in anchor_nodes:
        detail = seeker_detail_cache.get(node.id) or {}
        cfg = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
        anchor_site_id_map[node.id] = str(cfg.get("site_id") or node.node_id or node.id).strip()

    # Exclude ALL inventory nodes from discovery, not just placed ones
    all_inventory_nodes = db.scalars(select(Node)).all()
    inventory_site_ids: set[str] = set()
    for inv_node in all_inventory_nodes:
        if inv_node.node_id:
            inventory_site_ids.add(inv_node.node_id)
            inventory_site_ids.add(inv_node.node_id.lower())
        inventory_site_ids.add(str(inv_node.id))
        inv_detail = seeker_detail_cache.get(inv_node.id) or {}
        inv_config = inv_detail.get("config_summary") if isinstance(inv_detail.get("config_summary"), dict) else {}
        cfg_site_id = str(inv_config.get("site_id") or "").strip()
        if cfg_site_id:
            inventory_site_ids.add(cfg_site_id)

    discovered_peers: list[dict[str, object]] = []
    seen_site_ids: dict[str, int] = {}  # site_id -> owning anchor node.id
    discovery_links: list[dict[str, object]] = []

    for node in anchor_nodes:
        detail = seeker_detail_cache.get(node.id) or {}
        config_summary = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
        source_site_id = str(config_summary.get("site_id") or node.node_id or "").strip() or str(node.id)
        source_name = str(config_summary.get("site_name") or node.name or "").strip() or node.name
        tunnels = [row for row in (detail.get("tunnels") or []) if isinstance(row, dict)]

        for row in tunnels:
            if not _tunnel_row_is_eligible(row):
                continue
            mate_site_id = str(row.get("mate_site_id") or "").strip()
            if not mate_site_id or mate_site_id in inventory_site_ids or mate_site_id.lower() in inventory_site_ids:
                continue
            mate_ip = str(row.get("mate_ip") or "").strip()
            mate_name = str(row.get("site_name") or row.get("mate_site_name") or "").strip() or mate_site_id
            if not mate_ip or mate_ip == "--":
                continue
            ping_status = str(row.get("ping") or "").strip()
            tx_rate = str(row.get("tx_rate") or "").strip()
            rx_rate = str(row.get("rx_rate") or "").strip()

            # Add entity only once; resolve owner via lowest site_id rule
            if mate_site_id not in seen_site_ids:
                seen_site_ids[mate_site_id] = node.id
                discovered_peers.append({
                    "site_id": mate_site_id,
                    "name": mate_name,
                    "host": mate_ip,
                    "source_anchor_id": node.id,
                    "source_site_id": source_site_id,
                    "source_name": source_name,
                    "ping": ping_status,
                    "tx_rate": tx_rate,
                    "rx_rate": rx_rate,
                })
            else:
                # Update owner if this anchor has a lower site_id
                new_owner = _resolve_dn_owner_anchor_id(
                    node.id, seen_site_ids[mate_site_id], anchor_site_id_map,
                )
                if new_owner != seen_site_ids[mate_site_id]:
                    seen_site_ids[mate_site_id] = new_owner
                    for peer in discovered_peers:
                        if peer["site_id"] == mate_site_id:
                            peer["source_anchor_id"] = new_owner
                            peer["source_site_id"] = source_site_id
                            peer["source_name"] = source_name
                            break

            # Record every AN↔DN tunnel relationship as a link
            discovery_links.append({
                "source_anchor_id": node.id,
                "target_site_id": mate_site_id,
                "status": "healthy" if ping_status.lower() == "up" else "down",
                "tx_rate": tx_rate,
                "rx_rate": rx_rate,
            })

    # --- Second-hop: DN-to-DN discovery from cached DN tunnel data ---
    # For each first-hop DN, check its cached detail for tunnels to other DNs
    first_hop_site_ids = set(seen_site_ids.keys())
    for first_hop_peer in list(discovered_peers):
        fh_site_id = str(first_hop_peer["site_id"])
        cached_dn = node_dashboard_backend.get_cached_discovered_node(fh_site_id)
        if not cached_dn:
            logger.debug("DN-to-DN: no cache for %s", fh_site_id)
            continue
        dn_detail = cached_dn.get("detail") if isinstance(cached_dn.get("detail"), dict) else {}
        dn_tunnels = [row for row in (dn_detail.get("tunnels") or []) if isinstance(row, dict)]
        if not dn_tunnels:
            logger.debug("DN-to-DN: no tunnels for %s (has_detail=%s)", fh_site_id, bool(dn_detail))
            continue
        logger.debug("DN-to-DN: %s has %d tunnels", fh_site_id, len(dn_tunnels))

        for row in dn_tunnels:
            mate_sid_dbg = str(row.get("mate_site_id") or "").strip()
            eligible = _tunnel_row_is_eligible(row)
            is_inventory = mate_sid_dbg in inventory_site_ids or mate_sid_dbg.lower() in inventory_site_ids
            logger.debug(
                "DN-to-DN: %s tunnel mate=%s eligible=%s is_inventory=%s in_seen=%s",
                fh_site_id, mate_sid_dbg, eligible, is_inventory, mate_sid_dbg in seen_site_ids,
            )
            if not eligible:
                continue
            mate_site_id = mate_sid_dbg
            if not mate_site_id or mate_site_id in inventory_site_ids or mate_site_id.lower() in inventory_site_ids:
                continue
            mate_ip = str(row.get("mate_ip") or "").strip()
            mate_name = str(row.get("site_name") or row.get("mate_site_name") or "").strip() or mate_site_id
            if not mate_ip or mate_ip == "--":
                continue
            ping_status = str(row.get("ping") or "").strip()
            tx_rate = str(row.get("tx_rate") or "").strip()
            rx_rate = str(row.get("rx_rate") or "").strip()

            # Discover new second-hop peer if not already seen
            if mate_site_id not in seen_site_ids:
                owner_anchor_id = seen_site_ids.get(fh_site_id)
                if owner_anchor_id is not None:
                    seen_site_ids[mate_site_id] = owner_anchor_id
                    discovered_peers.append({
                        "site_id": mate_site_id,
                        "name": mate_name,
                        "host": mate_ip,
                        "source_anchor_id": owner_anchor_id,
                        "source_site_id": fh_site_id,
                        "source_name": str(first_hop_peer.get("name") or fh_site_id),
                        "ping": ping_status,
                        "tx_rate": tx_rate,
                        "rx_rate": rx_rate,
                    })

            # Create DN↔DN link if both endpoints are discovered
            if mate_site_id in seen_site_ids:
                discovery_links.append({
                    "source_anchor_id": seen_site_ids.get(fh_site_id),
                    "source_dn_site_id": fh_site_id,
                    "target_site_id": mate_site_id,
                    "kind": "dn-dn",
                    "status": "healthy" if ping_status.lower() == "up" else "down",
                    "tx_rate": tx_rate,
                    "rx_rate": rx_rate,
                })

    # Persist discovered nodes to database
    now = datetime.now(timezone.utc)
    for peer in discovered_peers:
        site_id = str(peer["site_id"])
        existing = db.get(DiscoveredNode, site_id)
        owner_anchor_id = seen_site_ids.get(site_id)
        if existing:
            # Update fields but preserve map position and map_view_id
            existing.host = str(peer.get("host") or existing.host or "")
            existing.site_name = str(peer.get("name") or existing.site_name or site_id)
            existing.source_anchor_node_id = owner_anchor_id
            if existing.map_view_id is None:
                existing.map_view_id = map_view_id
            existing.updated_at = now
        else:
            dn = DiscoveredNode(
                site_id=site_id,
                site_name=str(peer.get("name") or site_id),
                host=str(peer.get("host") or ""),
                map_view_id=map_view_id,
                source_anchor_node_id=owner_anchor_id,
                created_at=now,
                updated_at=now,
            )
            db.add(dn)

        # Upsert observation
        obs = db.get(DiscoveredNodeObservation, site_id)
        ping_up = str(peer.get("ping") or "").strip().lower() == "up"
        if obs:
            obs.ping = "Up" if ping_up else "Down"
            obs.last_seen = now
            if ping_up:
                obs.last_ping_up = now
                obs.ping_down_since = None
            elif obs.ping_down_since is None:
                obs.ping_down_since = now
            obs.observed_at = now
        else:
            obs = DiscoveredNodeObservation(
                site_id=site_id,
                ping="Up" if ping_up else "Down",
                last_seen=now,
                last_ping_up=now if ping_up else None,
                ping_down_since=None if ping_up else now,
                observed_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(obs)

    db.commit()

    # Build response with saved positions
    persisted_dns = db.scalars(
        select(DiscoveredNode).where(DiscoveredNode.map_view_id == map_view_id)
    ).all()
    saved_positions: dict[str, dict[str, int | None]] = {}
    for dn in persisted_dns:
        if dn.map_x is not None and dn.map_y is not None:
            saved_positions[dn.site_id] = {"x": dn.map_x, "y": dn.map_y}

    # Enrich peers and links with live ping data
    live_status_by_site: dict[str, str] = {}
    for peer in discovered_peers:
        sid = str(peer["site_id"])
        snap = dn_ping_snapshots.get(sid)
        if snap:
            peer["ping_state"] = snap.get("state", "down")
            peer["latency_ms"] = snap.get("latency_ms")
            peer["avg_latency_ms"] = snap.get("avg_latency_ms")
            peer["ping_ok"] = snap.get("ping_ok", False)
            peer["ping"] = "up" if snap.get("ping_ok") else "down"
            live_status_by_site[sid] = "healthy" if snap.get("ping_ok") else "down"

    for link in discovery_links:
        live = live_status_by_site.get(str(link["target_site_id"]))
        if live:
            link["status"] = live

    return {
        "map_view_id": map_view_id,
        "anchor_count": len(anchor_nodes),
        "discovered_peers": discovered_peers,
        "discovery_links": discovery_links,
        "saved_positions": saved_positions,
    }


@app.put("/api/topology/maps/discovered-nodes/{site_id}/position")
async def save_dn_position(
    site_id: str,
    payload: dict[str, int | None],
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Save a discovered node's map position."""
    dn = db.get(DiscoveredNode, site_id)
    if not dn:
        raise HTTPException(status_code=404, detail="Discovered node not found")
    dn.map_x = payload.get("x")
    dn.map_y = payload.get("y")
    dn.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok"}


@app.put("/api/topology/maps/{map_view_id}")
async def update_map_view(
    map_view_id: int,
    payload: OperationalMapViewUpdate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return operational_map_service.update_map_view(map_view_id, payload, db)


@app.delete("/api/topology/maps/{map_view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_map_view(
    map_view_id: int,
    db: Session = Depends(get_db),
) -> Response:
    operational_map_service.delete_map_view(map_view_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Map objects ---


@app.post("/api/topology/maps/{map_view_id}/objects", status_code=status.HTTP_201_CREATED)
async def create_map_object(
    map_view_id: int,
    payload: OperationalMapObjectCreate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if payload.map_view_id != map_view_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="map_view_id in body must match URL")
    return operational_map_service.create_map_object(payload, db)


@app.put("/api/topology/maps/objects/{object_id}")
async def update_map_object(
    object_id: int,
    payload: OperationalMapObjectUpdate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return operational_map_service.update_map_object(object_id, payload, db)


@app.delete("/api/topology/maps/objects/{object_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_map_object(
    object_id: int,
    db: Session = Depends(get_db),
) -> Response:
    operational_map_service.delete_map_object(object_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Object bindings ---


@app.post("/api/topology/maps/objects/{object_id}/bindings", status_code=status.HTTP_201_CREATED)
async def create_map_object_binding(
    object_id: int,
    payload: OperationalMapObjectBindingCreate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if payload.object_id != object_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="object_id in body must match URL")
    return operational_map_service.create_map_object_binding(payload, db)


@app.delete("/api/topology/maps/objects/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_map_object_binding(
    binding_id: int,
    db: Session = Depends(get_db),
) -> Response:
    operational_map_service.delete_map_object_binding(binding_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Map links ---


@app.post("/api/topology/maps/{map_view_id}/links", status_code=status.HTTP_201_CREATED)
async def create_map_link(
    map_view_id: int,
    payload: OperationalMapLinkCreate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if payload.map_view_id != map_view_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="map_view_id in body must match URL")
    return operational_map_service.create_map_link(payload, db)


@app.put("/api/topology/maps/links/{link_id}")
async def update_map_link(
    link_id: int,
    payload: OperationalMapLinkUpdate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return operational_map_service.update_map_link(link_id, payload, db)


@app.delete("/api/topology/maps/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_map_link(
    link_id: int,
    db: Session = Depends(get_db),
) -> Response:
    operational_map_service.delete_map_link(link_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Link bindings ---


@app.post("/api/topology/maps/links/{link_id}/bindings", status_code=status.HTTP_201_CREATED)
async def create_map_link_binding(
    link_id: int,
    payload: OperationalMapLinkBindingCreate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if payload.link_id != link_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="link_id in body must match URL")
    return operational_map_service.create_map_link_binding(payload, db)


@app.delete("/api/topology/maps/links/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_map_link_binding(
    binding_id: int,
    db: Session = Depends(get_db),
) -> Response:
    operational_map_service.delete_map_link_binding(binding_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/node-dashboard/stream")
async def node_dashboard_stream(window_seconds: int = Query(default=60)) -> StreamingResponse:
    async def event_generator():
        last_sent: str | None = None
        try:
            while True:
                payload = get_serialized_node_dashboard_cache(normalize_node_dashboard_window(window_seconds))
                serialized = json.dumps(payload)
                if serialized != last_sent:
                    yield f"event: snapshot\ndata: {serialized}\n\n"
                    last_sent = serialized
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/topology")
async def topology_payload(
    window_seconds: int = Query(default=60),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    dashboard_payload = node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))
    anchor_rows_by_id = {
        int(row.get("id")): row
        for row in dashboard_payload.get("anchors") or []
        if isinstance(row, dict) and row.get("id") is not None
    }
    inventory_nodes = []
    for node in nodes:
        anchor = anchor_rows_by_id.get(node.id, {})
        inventory_nodes.append(
            {
                "id": node.id,
                "name": node.name,
                "host": node.host,
                "location": anchor.get("site") or node.location,
                "status": anchor.get("status"),
                "include_in_topology": anchor.get("include_in_topology", node.include_in_topology),
                "topology_level": anchor.get("topology_level", node.topology_level),
                "topology_unit": anchor.get("unit") or node.topology_unit,
                "site_id": anchor.get("site_id") or node.node_id,
                "latency_ms": anchor.get("latency_ms"),
                "rtt_state": anchor.get("rtt_state"),
                "tx_bps": anchor.get("tx_bps"),
                "rx_bps": anchor.get("rx_bps"),
                "tx_display": anchor.get("tx_display"),
                "rx_display": anchor.get("rx_display"),
                "cpu_avg": anchor.get("cpu_avg"),
                "version": anchor.get("version"),
                "web_port": anchor.get("web_port", node.web_port),
                "web_scheme": anchor.get("web_scheme") or ("https" if node.api_use_https else "http"),
            }
        )
    db_links = db.scalars(select(TopologyLink).order_by(TopologyLink.id)).all()
    authored_links = [
        {
            "id": f"topo-link-{link.id}",
            "db_id": link.id,
            "from": link.source_entity_id,
            "to": link.target_entity_id,
            "source_anchor": link.source_anchor,
            "target_anchor": link.target_anchor,
            "link_type": link.link_type,
            "status_node_id": link.status_node_id,
            "kind": "authored",
            "status": "neutral",
        }
        for link in db_links
    ]
    submap_views = db.scalars(
        select(OperationalMapView).where(OperationalMapView.parent_map_id.is_(None)).order_by(OperationalMapView.name)
    ).all()
    submap_objects = db.scalars(
        select(OperationalMapObject).where(
            OperationalMapObject.object_type == "submap",
            OperationalMapObject.child_map_view_id.isnot(None),
        ).order_by(OperationalMapObject.id)
    ).all()
    submap_object_by_child_id = {obj.child_map_view_id: obj for obj in submap_objects}
    submaps = []
    for view in submap_views:
        obj = submap_object_by_child_id.get(view.id)
        submaps.append({
            "id": f"submap-{view.id}",
            "map_view_id": view.id,
            "name": view.name,
            "slug": view.slug,
            "kind": "submap",
            "level": 0,
            "x": obj.x if obj else 100,
            "y": obj.y if obj else 100,
            "width": obj.width if obj else 160,
            "height": obj.height if obj else 96,
        })
    db.commit()
    result = build_mock_topology_payload(inventory_nodes)
    result["links"] = authored_links
    result["submaps"] = submaps
    return result


@app.get("/api/services")
async def list_services(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    services = db.scalars(select(ServiceCheck).order_by(ServiceCheck.service_type, ServiceCheck.name, ServiceCheck.id)).all()
    return [merge_service_payload(service) for service in services]


@app.get("/api/dashboard/services")
async def dashboard_services(db: Session = Depends(get_db)) -> dict[str, object]:
    services = db.scalars(select(ServiceCheck).order_by(ServiceCheck.service_type, ServiceCheck.name, ServiceCheck.id)).all()
    payload = [merge_service_payload(service) for service in services]
    return {
        "summary": summarize_service_statuses(payload),
        "services": payload,
    }


@app.post("/api/services", status_code=status.HTTP_201_CREATED)
async def create_service(service_data: ServiceCheckCreate, db: Session = Depends(get_db)) -> dict[str, object]:
    service = ServiceCheck(**service_data.model_dump())
    db.add(service)
    db.commit()
    db.refresh(service)
    service_status_cache[service.id] = await check_service(service)
    return merge_service_payload(service)


@app.delete("/api/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(service_id: int, db: Session = Depends(get_db)) -> Response:
    service = db.get(ServiceCheck, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service check not found")
    db.delete(service)
    db.commit()
    service_status_cache.pop(service_id, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/nodes", status_code=status.HTTP_201_CREATED)
async def create_node(node_data: NodeCreate, db: Session = Depends(get_db)) -> dict[str, object]:
    node = Node(**node_data.model_dump())
    db.add(node)
    db.commit()
    db.refresh(node)
    pending_health = {
        "status": "unknown",
        "latency_ms": None,
        "last_checked": None,
        "web_ok": False,
        "ssh_ok": False,
        "ping_ok": False,
        "ping_state": "unknown",
        "ping_avg_ms": None,
    }
    return serialize_node(node, pending_health)


@app.put("/api/nodes/{node_id}")
async def update_node(
    node_id: int,
    node_data: NodeUpdate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    node = get_node_or_404(node_id, db)

    for field, value in node_data.model_dump().items():
        setattr(node, field, value)

    db.commit()
    db.refresh(node)
    cached = node_dashboard_backend.get_cached_payload(60)
    cached_row = next(
        (r for r in (cached.get("anchors") or []) if isinstance(r, dict) and r.get("id") == node.id),
        {},
    )
    health = {
        "status": cached_row.get("status", "unknown"),
        "latency_ms": cached_row.get("latency_ms"),
        "last_checked": node.last_checked,
        "web_ok": bool(cached_row.get("web_ok", False)),
        "ssh_ok": bool(cached_row.get("ssh_ok", False)),
        "ping_ok": bool(cached_row.get("ping_ok", False)),
        "ping_state": cached_row.get("ping_state", "unknown"),
        "ping_avg_ms": cached_row.get("ping_avg_ms"),
    }
    return serialize_node(node, health)


@app.delete("/api/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(node_id: int, db: Session = Depends(get_db)) -> Response:
    node = get_node_or_404(node_id, db)
    deleted_node_id = node.id
    db.delete(node)
    db.commit()
    seeker_detail_cache.pop(deleted_node_id, None)
    ping_samples_by_node.pop(deleted_node_id, None)
    ping_snapshot_by_node.pop(deleted_node_id, None)
    remaining_nodes = db.scalars(select(Node).order_by(Node.name)).all()
    await node_dashboard_backend.refresh_cache(db, remaining_nodes)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

