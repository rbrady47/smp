"""Chart stats API routes — /api/nodes/{node_id}/chart-stats."""

import json
from collections import defaultdict

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
                "sample_type": s.sample_type,
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


@router.get("/nodes/{node_id}/chart-summary")
async def get_chart_summary(
    node_id: int,
    start: int = Query(..., description="Start epoch (inclusive)"),
    end: int = Query(..., description="End epoch (inclusive)"),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Return aggregated chart stats for a node over a time range.

    Computes user throughput totals, per-tunnel averages (tx, rx, delay),
    and per-channel averages.  Tunnel site indexes are cross-referenced
    with the seeker detail cache to resolve mate site IDs.
    """
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    # Use raw samples for accurate averages (not min/max midpoints)
    stmt = (
        select(ChartSample)
        .where(ChartSample.node_id == node_id)
        .where(ChartSample.timestamp >= start)
        .where(ChartSample.timestamp <= end)
        .where(ChartSample.sample_type == "raw")
        .order_by(ChartSample.timestamp)
    )
    samples = db.scalars(stmt).all()

    if not samples:
        return {
            "node_id": node_id,
            "node_name": node.name,
            "sample_count": 0,
            "time_range": {"start": start, "end": end},
            "user_summary": {},
            "tunnel_summary": [],
            "channel_summary": [],
        }

    # --- User throughput totals ---
    total_tx_bytes = 0
    total_rx_bytes = 0
    total_tx_pkts = 0
    total_rx_pkts = 0
    user_count = 0

    # --- Per-tunnel accumulators: (site, tunnel) → {tx_sum, rx_sum, delay_sum, count} ---
    tunnel_acc: dict[tuple[int, int], dict[str, int | float]] = defaultdict(
        lambda: {"tx_sum": 0, "rx_sum": 0, "delay_sum": 0, "count": 0}
    )

    # --- Per-channel accumulators: ch → {tx_sum, rx_sum, count} ---
    channel_acc: dict[int, dict[str, int | float]] = defaultdict(
        lambda: {"tx_sum": 0, "rx_sum": 0, "count": 0}
    )

    for s in samples:
        if s.user_tx_bytes is not None:
            total_tx_bytes += s.user_tx_bytes
            user_count += 1
        if s.user_rx_bytes is not None:
            total_rx_bytes += s.user_rx_bytes
        if s.user_tx_pkts is not None:
            total_tx_pkts += s.user_tx_pkts
        if s.user_rx_pkts is not None:
            total_rx_pkts += s.user_rx_pkts

        if s.tunnel_data:
            try:
                tunnels = json.loads(s.tunnel_data)
                for t in tunnels:
                    key = (t["site"], t["tunnel"])
                    tunnel_acc[key]["tx_sum"] += t.get("tx", 0)
                    tunnel_acc[key]["rx_sum"] += t.get("rx", 0)
                    tunnel_acc[key]["delay_sum"] += t.get("delay_us", 0)
                    tunnel_acc[key]["count"] += 1
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        if s.channel_data:
            try:
                channels = json.loads(s.channel_data)
                for c in channels:
                    ch = c["ch"]
                    channel_acc[ch]["tx_sum"] += c.get("tx", 0)
                    channel_acc[ch]["rx_sum"] += c.get("rx", 0)
                    channel_acc[ch]["count"] += 1
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    sample_count = len(samples)

    # --- Resolve mate site IDs from seeker detail cache ---
    from app.main import seeker_detail_cache
    mate_map: dict[int, dict[str, str]] = {}
    detail = seeker_detail_cache.get(node_id)
    if isinstance(detail, dict):
        for mate in detail.get("mates") or []:
            idx = mate.get("mate_index")
            if idx is not None:
                mate_map[int(idx)] = {
                    "mate_site_id": str(mate.get("mate_site_id") or f"Site {idx}"),
                    "mate_ip": str(mate.get("mate_ip") or "--"),
                    "site_name": str(mate.get("site_name") or ""),
                }

    # --- Build tunnel summary rows ---
    tunnel_summary = []
    for (site_idx, tunnel_idx), acc in sorted(tunnel_acc.items()):
        count = acc["count"]
        if count == 0:
            continue
        mate_info = mate_map.get(site_idx, {
            "mate_site_id": f"Site {site_idx}",
            "mate_ip": "--",
            "site_name": "",
        })
        tunnel_summary.append({
            "site_index": site_idx,
            "mate_site_id": mate_info["mate_site_id"],
            "mate_ip": mate_info["mate_ip"],
            "site_name": mate_info["site_name"],
            "tunnel": tunnel_idx,
            "avg_tx": acc["tx_sum"] / count,
            "avg_rx": acc["rx_sum"] / count,
            "avg_delay_ms": (acc["delay_sum"] / count) / 1000.0,
            "sample_count": count,
        })

    # --- Build channel summary rows ---
    channel_summary = []
    for ch_idx in sorted(channel_acc.keys()):
        acc = channel_acc[ch_idx]
        count = acc["count"]
        if count == 0:
            continue
        channel_summary.append({
            "channel": ch_idx,
            "avg_tx": acc["tx_sum"] / count,
            "avg_rx": acc["rx_sum"] / count,
        })

    return {
        "node_id": node_id,
        "node_name": node.name,
        "sample_count": sample_count,
        "time_range": {"start": start, "end": end},
        "user_summary": {
            "total_tx_bytes": total_tx_bytes,
            "total_rx_bytes": total_rx_bytes,
            "total_tx_pkts": total_tx_pkts,
            "total_rx_pkts": total_rx_pkts,
            "avg_tx_bytes_per_sec": total_tx_bytes / sample_count if sample_count else 0,
            "avg_rx_bytes_per_sec": total_rx_bytes / sample_count if sample_count else 0,
        },
        "tunnel_summary": tunnel_summary,
        "channel_summary": channel_summary,
    }
