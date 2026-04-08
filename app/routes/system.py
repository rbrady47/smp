"""System routes — /api/status, /api/health."""

import socket
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ChartSample, Node

router = APIRouter(prefix="/api")


@router.get("/status")
async def status_view() -> dict[str, str]:
    return {
        "app": "Seeker Management Platform",
        "version": "0.1.0",
        "hostname": socket.gethostname(),
        "time": datetime.now().isoformat(),
    }


@router.get("/health")
async def health_view(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    """Platform health with chart storage metrics and poller status."""

    # --- Chart storage stats ---
    total_rows = (await db.scalar(select(func.count(ChartSample.id)))) or 0
    oldest_ts = await db.scalar(select(func.min(ChartSample.timestamp)))
    newest_ts = await db.scalar(select(func.max(ChartSample.timestamp)))

    # Per-node breakdown
    per_node_stmt = (
        select(
            ChartSample.node_id,
            func.count(ChartSample.id).label("count"),
            func.min(ChartSample.timestamp).label("oldest"),
            func.max(ChartSample.timestamp).label("newest"),
        )
        .group_by(ChartSample.node_id)
        .order_by(func.count(ChartSample.id).desc())
    )
    per_node_rows = (await db.execute(per_node_stmt)).all()

    # Resolve node names
    node_ids = [r.node_id for r in per_node_rows]
    node_names: dict[int, str] = {}
    if node_ids:
        nodes = (await db.scalars(select(Node).where(Node.id.in_(node_ids)))).all()
        node_names = {n.id: n.name for n in nodes}

    per_node = [
        {
            "node_id": r.node_id,
            "node_name": node_names.get(r.node_id, f"Node {r.node_id}"),
            "sample_count": r.count,
            "oldest_timestamp": r.oldest,
            "newest_timestamp": r.newest,
        }
        for r in per_node_rows
    ]

    # Actual PG table size (data + indexes + toast)
    try:
        table_bytes = (await db.scalar(text(
            "SELECT pg_total_relation_size('chart_samples')"
        ))) or 0
    except Exception:
        # Fallback for non-PG databases (e.g. SQLite in tests)
        table_bytes = total_rows * 120

    # --- Node counts ---
    total_nodes = (await db.scalar(select(func.count(Node.id)))) or 0
    charts_enabled_nodes = (
        await db.scalar(select(func.count(Node.id)).where(Node.charts_enabled.is_(True)))
    ) or 0

    # --- Poller status (import cycle times from pollers) ---
    from app.pollers.seeker import SEEKER_POLL_INTERVAL_SECONDS
    from app.pollers.charts import CHARTS_POLL_INTERVAL_SECONDS
    from app.pollers.services import SERVICE_POLL_INTERVAL_SECONDS

    return {
        "status": "ok",
        "hostname": socket.gethostname(),
        "time": datetime.now().isoformat(),
        "nodes": {
            "total": total_nodes,
            "charts_enabled": charts_enabled_nodes,
        },
        "chart_storage": {
            "total_rows": total_rows,
            "table_bytes": table_bytes,
            "oldest_timestamp": oldest_ts,
            "newest_timestamp": newest_ts,
            "per_node": per_node,
        },
        "pollers": {
            "seeker_interval_s": SEEKER_POLL_INTERVAL_SECONDS,
            "charts_interval_s": CHARTS_POLL_INTERVAL_SECONDS,
            "services_interval_s": SERVICE_POLL_INTERVAL_SECONDS,
        },
    }
