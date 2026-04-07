"""Chart stats API routes — /api/nodes/{node_id}/chart-stats."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ChartSample, Node

router = APIRouter(prefix="/api")


@router.get("/nodes/{node_id}/chart-stats")
async def get_chart_stats(
    node_id: int,
    start: int | None = Query(default=None, description="Start epoch (inclusive)"),
    end: int | None = Query(default=None, description="End epoch (inclusive)"),
    limit: int = Query(default=3600, ge=1, le=604800, description="Max rows to return"),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Return stored chart samples for a node within an optional time range."""
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    stmt = select(ChartSample).where(ChartSample.node_id == node_id)
    if start is not None:
        stmt = stmt.where(ChartSample.timestamp >= start)
    if end is not None:
        stmt = stmt.where(ChartSample.timestamp <= end)
    stmt = stmt.order_by(ChartSample.timestamp).limit(limit)

    samples = db.scalars(stmt).all()

    return {
        "node_id": node_id,
        "node_name": node.name,
        "count": len(samples),
        "samples": [
            {
                "timestamp": s.timestamp,
                "user_tx_bytes": s.user_tx_bytes,
                "user_tx_pkts": s.user_tx_pkts,
                "user_rx_bytes": s.user_rx_bytes,
                "user_rx_pkts": s.user_rx_pkts,
                "channel_data": s.channel_data,
                "tunnel_data": s.tunnel_data,
            }
            for s in samples
        ],
    }
