import asyncio
from datetime import datetime, timezone
import logging
import socket
import time
import urllib.parse

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Node
from app.schemas import NodeCreate, NodeUpdate
from app.telemetry import normalize_bwv_stats

app = FastAPI(title="Seeker Management Platform", version="0.1.0")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

logger = logging.getLogger(__name__)
HEALTH_CHECK_TIMEOUT_SECONDS = 1.0
# Lab-only prototype behavior: Seeker systems often present self-signed TLS
# certificates, so verification is disabled for these direct API requests.
SEEKER_API_VERIFY_TLS = False
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


async def compute_node_status(node: Node) -> dict[str, object]:
    if not node.enabled:
        return {
            "status": "disabled",
            "latency_ms": None,
            "last_checked": node.last_checked,
            "web_ok": False,
            "ssh_ok": False,
        }

    checked_at = datetime.now(timezone.utc)
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

    latency_ms = None
    if reachable_ports:
        latency_ms = min(
            int(result["latency_ms"])
            for result in reachable_ports
            if result["latency_ms"] is not None
        )

    return {
        "status": status_value,
        "latency_ms": latency_ms,
        "last_checked": checked_at,
        "web_ok": bool(web_check["reachable"]),
        "ssh_ok": bool(ssh_check["reachable"]),
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
    if not node.api_username or not node.api_password:
        return {
            "status": "error",
            "rc": None,
            "message": "Node API credentials are not configured",
        }

    scheme = "https" if node.api_use_https else "http"
    base_url = f"{scheme}://{node.host}:{node.web_port}"
    login_path = "/acct/login/"
    telemetry_path = "/acct/"
    login_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }
    telemetry_headers = {
        "User-Agent": "curl/7.55.1",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
    }

    def log_warning(message: str, *args: object) -> None:
        if emit_logs:
            logger.warning(message, *args)

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=10.0,
        verify=SEEKER_API_VERIFY_TLS,
    ) as client:
        try:
            log_warning("BV login URL for node %s: %s%s", node.name, base_url, login_path)
            login_body = (
                f"userName={urllib.parse.quote_plus(str(node.api_username))}"
                f"&pass={urllib.parse.quote_plus(str(node.api_password))}"
                f"&isAjaxReq=1"
            )
            login_response = await client.post(
                login_path,
                headers=login_headers,
                content=login_body,
            )
            login_response.raise_for_status()
            log_warning("BV login response body for node %s: %s", node.name, login_response.text)
            login_data = login_response.json()
            log_warning(
                "BV login response for node %s: %s",
                node.name,
                {
                    "transId": login_data.get("transId"),
                    "transIdRefresh": login_data.get("transIdRefresh"),
                    "rc": login_data.get("rc"),
                },
            )
            log_warning("BV cookies after login for node %s: %s", node.name, dict(client.cookies))

            trans_id = login_data.get("transId")
            trans_id_refresh = login_data.get("transIdRefresh")

            if not trans_id or not trans_id_refresh:
                log_warning("Telemetry login missing token fields for node %s", node.name)
                return {
                    "status": "error",
                    "rc": login_data.get("rc"),
                    "message": "Unable to retrieve telemetry",
                }

            telemetry_json = '{"reqType":"bwvStats","getTotTunnelUsage":"1","tunnelFragRatio":"1"}'
            telemetry_body = (
                "reqType=bwv"
                f"&data0={urllib.parse.quote_plus(telemetry_json)}"
                f"&transId={urllib.parse.quote_plus(str(trans_id))}"
                f"&transIdRefresh={urllib.parse.quote_plus(str(trans_id_refresh))}"
                f"&userName={urllib.parse.quote_plus(str(node.api_username))}"
                "&isAjaxReq=1"
            )
            log_warning("BV telemetry URL for node %s: %s%s", node.name, base_url, telemetry_path)
            log_warning("BV telemetry request headers for node %s: %s", node.name, telemetry_headers)
            log_warning("BV telemetry request body for node %s: %s", node.name, telemetry_body)

            telemetry_response = await client.post(
                telemetry_path,
                headers=telemetry_headers,
                content=telemetry_body,
            )
            telemetry_response.raise_for_status()
            telemetry_response_text = telemetry_response.text
            log_warning(
                "BV telemetry final URL for node %s: %s history=%s",
                node.name,
                telemetry_response.url,
                [str(item.status_code) + " " + str(item.url) for item in telemetry_response.history],
            )
            log_warning("BV telemetry response body for node %s: %s", node.name, telemetry_response_text)

            try:
                telemetry_data = telemetry_response.json()
            except ValueError:
                log_warning(
                    "BV telemetry returned non-JSON for node %s content-type=%s body=%s",
                    node.name,
                    telemetry_response.headers.get("content-type"),
                    telemetry_response_text,
                )
                return {
                    "status": "error",
                    "rc": None,
                    "message": "Unable to retrieve telemetry",
                }

            response_code = telemetry_data.get("rc")
            if response_code not in (None, 0, "0"):
                log_warning(
                    "BV telemetry failure for node %s returned body: %s",
                    node.name,
                    telemetry_response_text,
                )
                return {
                    "status": "error",
                    "rc": int(response_code) if str(response_code).lstrip("-").isdigit() else response_code,
                    "message": f"Telemetry request failed (rc={response_code})",
                }
        except httpx.HTTPError as exc:
            log_warning("Telemetry request failed for node %s: %s", node.name, exc)
            return {
                "status": "error",
                "rc": None,
                "message": "Unable to retrieve telemetry",
            }

    return {
        "status": "ok",
        "rc": 0,
        "normalized": normalize_bwv_stats(telemetry_data),
        "telemetry": telemetry_data,
        "node_id": node.id,
        "node_name": node.name,
    }


async def summarize_dashboard_node(node: Node) -> dict[str, object]:
    health = await compute_node_status(node)
    apply_health_to_node(node, health)

    normalized = None
    telemetry_data: dict[str, object] = {}

    if node.enabled and node.api_username and node.api_password:
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
        "site": node.location,
        "status": build_dashboard_status(str(health["status"]), normalized),
        "web_ok": health["web_ok"],
        "ssh_ok": health["ssh_ok"],
        "latency_ms": (normalized or {}).get("latency_ms", health.get("latency_ms")),
        "tx_bps": (normalized or {}).get("tx_bps", 0),
        "rx_bps": (normalized or {}).get("rx_bps", 0),
        "sites_up": sites_up,
        "sites_total": sites_total,
        "wan_up": wan_up,
        "wan_total": wan_total,
        "last_seen": node.last_checked.isoformat() if node.last_checked else None,
    }


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
