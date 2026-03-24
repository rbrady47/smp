import asyncio
from datetime import datetime
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


def compute_node_status(node: Node) -> dict[str, object]:
    # Health status is computed at request time so the UI reflects current reachability.
    # Disabled nodes skip network checks and are reported separately.
    if not node.enabled:
        return {"status": "disabled", "latency_ms": None}

    for port in (node.web_port, node.ssh_port):
        start_time = time.perf_counter()

        try:
            with socket.create_connection(
                (node.host, port),
                timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
            ):
                latency_ms = round((time.perf_counter() - start_time) * 1000)
                return {"status": "online", "latency_ms": latency_ms}
        except OSError:
            continue

    return {"status": "offline", "latency_ms": None}


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
        "latency_ms": health["latency_ms"],
    }


async def build_node_payload(node: Node) -> dict[str, object]:
    health = await asyncio.to_thread(compute_node_status, node)
    return serialize_node(node, health)


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
    payloads = [build_node_payload(node) for node in nodes]
    return await asyncio.gather(*payloads)


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
    return await build_node_payload(node)


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
    return await build_node_payload(node)


@app.delete("/api/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(node_id: int, db: Session = Depends(get_db)) -> Response:
    node = get_node_or_404(node_id, db)
    db.delete(node)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
