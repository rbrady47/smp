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
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine, get_db
from app.bwvstats_ingest import collect_bwvstats_phase1, get_raw_bwvstats_snapshots
from app.models import Node, ServiceCheck
from app.node_dashboard_backend import NodeDashboardBackend
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
from app.schemas import NodeCreate, NodeUpdate, ServiceCheckCreate
from app.topology import build_mock_topology_payload, build_topology_discovery_payload, normalize_topology_location

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
PING_HISTORY_SECONDS = 60
PING_INTERVAL_SECONDS = 1.0
SEEKER_POLL_INTERVAL_SECONDS = 15.0
SERVICE_POLL_INTERVAL_SECONDS = 30.0
SERVICE_CHECK_TIMEOUT_SECONDS = 5.0
NODE_DASHBOARD_FAST_REFRESH_SECONDS = 1.0
NODE_DASHBOARD_PROJECTION_REFRESH_SECONDS = 5.0
ping_samples_by_node: dict[int, deque[int]] = {}
ping_snapshot_by_node: dict[int, dict[str, object]] = {}
ping_monitor_task: asyncio.Task | None = None
seeker_detail_cache: dict[int, dict[str, object]] = {}
seeker_poll_task: asyncio.Task | None = None
service_status_cache: dict[int, dict[str, object]] = {}
service_poll_task: asyncio.Task | None = None
node_dashboard_poll_task: asyncio.Task | None = None


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
        context={"page_title": "Division C2 Information Network | Seeker Management Platform"},
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
    return templates.TemplateResponse(
        request=request,
        name="node_detail.html",
        context={
            "page_title": f"{node.name} | Anchor Node Detail",
            "node_id": node.id,
            "node_name": node.name,
            "detail_endpoint": f"/api/nodes/{node.id}/detail",
            "detail_kind": "anchor",
        },
    )


@app.get("/node/{node_id}", response_class=HTMLResponse)
async def node_detail_alias(node_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return await node_detail_page(node_id, request, db)


@app.get("/nodes/discovered/{site_id}", response_class=HTMLResponse)
async def discovered_node_detail_page(site_id: str, request: Request) -> HTMLResponse:
    cached = node_dashboard_backend.get_cached_discovered_node(site_id) or {}
    node_name = str(cached.get("site_name") or f"Discovered Site {site_id}")
    return templates.TemplateResponse(
        request=request,
        name="node_detail.html",
        context={
            "page_title": f"{node_name} | Discovered Node Detail",
            "node_id": site_id,
            "node_name": node_name,
            "detail_endpoint": f"/api/discovered-nodes/{site_id}/detail",
            "detail_kind": "discovered",
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

    return {"reachable": True, "latency_ms": None}


def build_ping_snapshot(node_id: int, ping_result: dict[str, int | bool | None]) -> dict[str, object]:
    samples = ping_samples_by_node.setdefault(node_id, deque(maxlen=PING_HISTORY_SECONDS))
    ping_ok = bool(ping_result["reachable"])
    latency_ms = ping_result["latency_ms"] if ping_ok else None

    if ping_ok and latency_ms is not None:
        samples.append(int(latency_ms))

    average_ms = round(sum(samples) / len(samples)) if samples else None

    if not ping_ok:
        state = "down"
    elif average_ms is None or latency_ms is None:
        state = "good"
    elif latency_ms <= average_ms * 1.5:
        state = "good"
    else:
        state = "warn"

    snapshot = {
        "ping_ok": ping_ok,
        "latency_ms": latency_ms,
        "avg_latency_ms": average_ms,
        "state": state,
        "updated_at": datetime.now(timezone.utc),
    }
    ping_snapshot_by_node[node_id] = snapshot
    return snapshot


def get_ping_snapshot(node: Node) -> dict[str, object]:
    snapshot = ping_snapshot_by_node.get(node.id)
    if snapshot is not None:
        return snapshot

    return build_ping_snapshot(node.id, ping_host(node.host))


async def ping_monitor_loop() -> None:
    while True:
        db = SessionLocal()
        try:
            nodes = db.scalars(select(Node).order_by(Node.id)).all()
        finally:
            db.close()

        enabled_nodes = [node for node in nodes if node.enabled]
        if enabled_nodes:
            ping_results = await asyncio.gather(
                *(asyncio.to_thread(ping_host, node.host) for node in enabled_nodes),
                return_exceptions=True,
            )
            for node, result in zip(enabled_nodes, ping_results):
                if isinstance(result, Exception):
                    build_ping_snapshot(node.id, {"reachable": False, "latency_ms": None})
                else:
                    build_ping_snapshot(node.id, result)

        await asyncio.sleep(PING_INTERVAL_SECONDS)


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
    return {
        "id": node.id,
        "name": node.name,
        "node_id": node.node_id,
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
        "status": health["status"],
        "latency_ms": node.latency_ms,
        "last_checked": node.last_checked.isoformat() if node.last_checked else None,
        "web_ok": health["web_ok"],
        "ssh_ok": health["ssh_ok"],
        "ping_ok": health["ping_ok"],
        "ping_state": health["ping_state"],
        "ping_avg_ms": health["ping_avg_ms"],
    }


def ensure_node_topology_columns() -> None:
    inspector = inspect(engine)
    existing_columns = {column["name"] for column in inspector.get_columns("nodes")}
    dialect = engine.dialect.name

    include_default = "FALSE" if dialect == "postgresql" else "0"
    statements: list[str] = []
    if "node_id" not in existing_columns:
        statements.append("ALTER TABLE nodes ADD COLUMN node_id VARCHAR(64)")
    if "include_in_topology" not in existing_columns:
        statements.append(f"ALTER TABLE nodes ADD COLUMN include_in_topology BOOLEAN NOT NULL DEFAULT {include_default}")
    if "topology_level" not in existing_columns:
        statements.append("ALTER TABLE nodes ADD COLUMN topology_level INTEGER")
    if "topology_unit" not in existing_columns:
        statements.append("ALTER TABLE nodes ADD COLUMN topology_unit VARCHAR(64)")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(
            text(
                "UPDATE nodes SET include_in_topology = "
                + ("FALSE" if dialect == "postgresql" else "0")
                + " WHERE include_in_topology IS NULL"
            )
        )


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
    payloads = await asyncio.gather(*(refresh_node(node) for node in nodes))
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

    if web_ok and ssh_ok:
        node_status = "online"
    elif web_ok or ssh_ok or ping_ok:
        node_status = "degraded"
    elif not ping_ok:
        node_status = "offline"

    if cached_detail:
        normalized = normalize_bwv_stats(cached_detail.get("raw", {}).get("bwv_stats", {}) or {})
        cfg_summary = dict(cached_detail.get("config_summary") or {})
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
        "ping_ok": ping_ok,
        "ping_state": ping_state,
        "ping_avg_ms": ping_avg_ms,
        "latency_ms": latency_ms,
        "tx_bps": (normalized or {}).get("tx_bps", 0),
        "rx_bps": (normalized or {}).get("rx_bps", 0),
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


def get_serialized_node_dashboard_cache() -> dict[str, object]:
    return node_dashboard_backend.get_serialized_cache()


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
    global ping_monitor_task, seeker_poll_task, service_poll_task, node_dashboard_poll_task
    Base.metadata.create_all(bind=engine)
    ensure_node_topology_columns()

    if ping_monitor_task is None or ping_monitor_task.done():
        ping_monitor_task = asyncio.create_task(ping_monitor_loop())
    if seeker_poll_task is None or seeker_poll_task.done():
        seeker_poll_task = asyncio.create_task(seeker_polling_loop())
    if service_poll_task is None or service_poll_task.done():
        service_poll_task = asyncio.create_task(service_polling_loop())
    if node_dashboard_poll_task is None or node_dashboard_poll_task.done():
        node_dashboard_poll_task = asyncio.create_task(node_dashboard_polling_loop())


@app.on_event("shutdown")
async def shutdown_ping_monitor() -> None:
    global ping_monitor_task, seeker_poll_task, service_poll_task, node_dashboard_poll_task

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


@app.get("/api/nodes")
async def list_nodes(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    return await refresh_nodes(nodes, db)


@app.post("/api/nodes/refresh")
async def refresh_all_nodes(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    return await refresh_nodes(nodes, db)


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
async def node_detail(node_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    node = get_node_or_404(node_id, db)
    detail = seeker_detail_cache.get(node.id)
    if not detail:
        detail = await refresh_seeker_detail_for_node(node)
    db.commit()
    return detail


@app.get("/api/discovered-nodes/{site_id}/detail")
async def discovered_node_detail(site_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    cached = node_dashboard_backend.ensure_discovered_node_cached(db, site_id)

    if not cached:
        raise HTTPException(status_code=404, detail="Discovered node not found")

    detail = cached.get("detail") if isinstance(cached.get("detail"), dict) else {}
    if detail:
        return detail

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
    return detail


@app.delete("/api/discovered-nodes/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_discovered_node(site_id: str, db: Session = Depends(get_db)) -> Response:
    node_dashboard_backend.delete_discovered_node(db, site_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
async def node_dashboard_payload(db: Session = Depends(get_db)) -> dict[str, list[dict[str, object]]]:
    db.commit()
    return node_dashboard_backend.get_cached_payload()


@app.get("/api/topology/discovery")
async def topology_discovery_payload(db: Session = Depends(get_db)) -> dict[str, object]:
    db.commit()
    return build_topology_discovery_payload(node_dashboard_backend.get_cached_payload())


@app.get("/api/node-dashboard/stream")
async def node_dashboard_stream() -> StreamingResponse:
    async def event_generator():
        last_sent: str | None = None
        try:
            while True:
                payload = get_serialized_node_dashboard_cache()
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
async def topology_payload(db: Session = Depends(get_db)) -> dict[str, object]:
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    health_payloads = await asyncio.gather(*(compute_node_status(node) for node in nodes))
    inventory_nodes = []
    for node, health in zip(nodes, health_payloads):
        apply_health_to_node(node, health)
        inventory_nodes.append(
            {
                "id": node.id,
                "name": node.name,
                "host": node.host,
                "location": node.location,
                "status": health["status"],
                "include_in_topology": node.include_in_topology,
                "topology_level": node.topology_level,
                "topology_unit": node.topology_unit,
            }
        )
    db.commit()
    return build_mock_topology_payload(inventory_nodes)


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
    payload = await refresh_node(node)
    db.commit()
    db.refresh(node)
    return payload


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
    payload = await refresh_node(node)
    db.commit()
    db.refresh(node)
    return payload


@app.delete("/api/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(node_id: int, db: Session = Depends(get_db)) -> Response:
    node = get_node_or_404(node_id, db)
    db.delete(node)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
