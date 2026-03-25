import asyncio
from collections import deque
from datetime import datetime, timezone
import logging
import platform
import re
import socket
import subprocess
import time

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import Node
from app.seeker_api import (
    build_detail_payload,
    get_bwv_cfg,
    get_bwv_stats,
    normalize_bwv_stats,
    extract_learnt_routes_from_stats,
    extract_static_routes_from_cfg,
)
from app.schemas import NodeCreate, NodeUpdate

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
ping_samples_by_node: dict[int, deque[int]] = {}
ping_snapshot_by_node: dict[int, dict[str, object]] = {}
ping_monitor_task: asyncio.Task | None = None
seeker_detail_cache: dict[int, dict[str, object]] = {}
seeker_poll_task: asyncio.Task | None = None


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"page_title": "Seeker Management Platform"},
    )


@app.get("/nodes", response_class=HTMLResponse)
async def nodes_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="nodes.html",
        context={"page_title": "Node Inventory | Seeker Management Platform"},
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
        },
    )


@app.get("/node/{node_id}", response_class=HTMLResponse)
async def node_detail_alias(node_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return await node_detail_page(node_id, request, db)


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
        "host": node.host,
        "web_port": node.web_port,
        "ssh_port": node.ssh_port,
        "location": node.location,
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

    return build_detail_payload(
        node,
        node_health=health,
        cfg_result=cfg_result,
        stats_result=stats_result,
        learnt_routes_result=learnt_routes_result,
    )


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


async def summarize_dashboard_node(node: Node) -> dict[str, object]:
    health = await compute_node_status(node)
    apply_health_to_node(node, health)

    normalized = None
    telemetry_data: dict[str, object] = {}
    cached_detail = seeker_detail_cache.get(node.id)

    if cached_detail:
        normalized = normalize_bwv_stats(cached_detail.get("raw", {}).get("bwv_stats", {}) or {})
        raw_payload = cached_detail.get("raw", {}).get("bwv_stats")
        if isinstance(raw_payload, dict):
            telemetry_data = raw_payload
    elif node.enabled and node.api_username and node.api_password:
        try:
            telemetry_result = await asyncio.wait_for(
                request_node_telemetry(node, emit_logs=False),
                timeout=DASHBOARD_TELEMETRY_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            telemetry_result = {
                "status": "error",
                "rc": None,
                "message": "Telemetry timed out",
            }

        if telemetry_result.get("status") == "ok":
            normalized = dict(telemetry_result.get("normalized") or {})
            raw_payload = telemetry_result.get("telemetry")
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
        "status": build_dashboard_status(str(health["status"]), normalized),
        "web_ok": health["web_ok"],
        "ssh_ok": health["ssh_ok"],
        "ping_ok": health["ping_ok"],
        "ping_state": health["ping_state"],
        "ping_avg_ms": health["ping_avg_ms"],
        "latency_ms": health.get("latency_ms"),
        "tx_bps": (normalized or {}).get("tx_bps", 0),
        "rx_bps": (normalized or {}).get("rx_bps", 0),
        "sites_up": sites_up,
        "sites_total": sites_total,
        "wan_up": wan_up,
        "wan_total": wan_total,
        "last_seen": node.last_checked.isoformat() if node.last_checked else None,
    }


@app.on_event("startup")
async def startup_ping_monitor() -> None:
    global ping_monitor_task, seeker_poll_task

    if ping_monitor_task is None or ping_monitor_task.done():
        ping_monitor_task = asyncio.create_task(ping_monitor_loop())
    if seeker_poll_task is None or seeker_poll_task.done():
        seeker_poll_task = asyncio.create_task(seeker_polling_loop())


@app.on_event("shutdown")
async def shutdown_ping_monitor() -> None:
    global ping_monitor_task, seeker_poll_task

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
