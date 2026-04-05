"""Dashboard API routes — /api/dashboard/nodes, /api/node-dashboard."""

import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Node
from app.node_watchlist_projection_service import build_node_watchlist_payload

router = APIRouter(prefix="/api")


@router.get("/dashboard/nodes")
async def dashboard_nodes(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    from app.main import DASHBOARD_STATUS_PRIORITY, summarize_dashboard_node
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


@router.get("/dashboard/nodes/watchlist")
async def dashboard_node_watchlist(
    pin_key: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    from app.main import node_dashboard_backend
    db.commit()
    return build_node_watchlist_payload(node_dashboard_backend.get_serialized_cache(), pin_key)


@router.get("/node-dashboard")
async def node_dashboard_payload(
    window_seconds: int = Query(default=60),
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, object]]]:
    from app.main import node_dashboard_backend, normalize_node_dashboard_window
    db.commit()
    return node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))
