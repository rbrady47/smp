import asyncio
from datetime import datetime, timezone
import socket
import time

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Node
from app.schemas import NodeCreate, NodeUpdate

# FastAPI app setup for the prototype SMP shell.
app = FastAPI(title="Seeker Management Platform", version="0.1.0")

# Template rendering is configured so route handlers can return HTML pages.
templates = Jinja2Templates(directory="templates")

# Static file mounting allows CSS and JavaScript files to be served by FastAPI.
app.mount("/static", StaticFiles(directory="static"), name="static")

HEALTH_CHECK_TIMEOUT_SECONDS = 1.0
STATUS_PRIORITY = {
    "online": 0,
    "degraded": 1,
    "offline": 2,
    "disabled": 3,
}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    # Route definition for the homepage that renders the main UI shell template.
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"page_title": "Seeker Management Platform"},
    )


@app.get("/nodes", response_class=HTMLResponse)
async def nodes_page(request: Request) -> HTMLResponse:
    # Route definition for the node inventory page that renders a separate template.
    return templates.TemplateResponse(
        request=request,
        name="nodes.html",
        context={"page_title": "Node Inventory | Seeker Management Platform"},
    )


@app.get("/api/status")
async def status_view() -> dict[str, str]:
    # Route definition for a simple status payload used by the prototype shell.
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
    # Health checks try the HTTPS and SSH ports separately so we can distinguish
    # between fully healthy nodes and partially reachable degraded nodes.
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
        "status": health["status"],
        "latency_ms": node.latency_ms,
        "last_checked": node.last_checked.isoformat() if node.last_checked else None,
        "web_ok": health["web_ok"],
        "ssh_ok": health["ssh_ok"],
    }


def apply_health_to_node(node: Node, health: dict[str, object]) -> None:
    node.latency_ms = health["latency_ms"]
    node.last_checked = health["last_checked"]


async def refresh_node(node: Node) -> dict[str, object]:
    health = await compute_node_status(node)
    apply_health_to_node(node, health)
    return serialize_node(node, health)


async def refresh_nodes(nodes: list[Node], db: Session) -> list[dict[str, object]]:
    # The API keeps health logic in-process for now: enabled nodes are checked
    # concurrently, the last-checked metadata is persisted, and the response
    # returns the same enriched payload the UI renders.
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


@app.get("/api/nodes")
async def list_nodes(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    # Session usage is handled through the FastAPI dependency so each request gets
    # its own SQLAlchemy session and the session is always closed afterward.
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    return await refresh_nodes(nodes, db)


@app.post("/api/nodes/refresh")
async def refresh_all_nodes(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    return await refresh_nodes(nodes, db)


@app.post("/api/nodes", status_code=status.HTTP_201_CREATED)
async def create_node(
    node_data: NodeCreate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    # Node CRUD routes keep the implementation simple: validate input, persist,
    # commit the transaction, then return the saved row with live health status.
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
