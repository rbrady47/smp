"""Chart stats API routes — /api/nodes/{node_id}/chart-stats."""

import json
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ChartSample, Node

router = APIRouter(prefix="/api")

BUCKET_SECONDS = 300  # 5-minute buckets for server-side downsampling


def _bucket_samples(samples: list, bucket_size: int) -> list[dict]:
    """Aggregate raw samples into time buckets.

    Returns 3 rows per bucket (min, max, avg) so the frontend can
    render envelope bands (min/max) with an average line through
    the middle — same visual as raw data but with far fewer rows.
    """
    if not samples:
        return []

    buckets: dict[int, dict] = {}

    for s in samples:
        bts = (s.timestamp // bucket_size) * bucket_size
        if bts not in buckets:
            buckets[bts] = {
                "timestamp": bts, "count": 0,
                "tx_sum": 0, "tx_min": float("inf"), "tx_max": 0,
                "rx_sum": 0, "rx_min": float("inf"), "rx_max": 0,
                "txp_sum": 0, "txp_min": float("inf"), "txp_max": 0,
                "rxp_sum": 0, "rxp_min": float("inf"), "rxp_max": 0,
                "tunnels": defaultdict(lambda: {
                    "tx_s": 0, "tx_mn": float("inf"), "tx_mx": 0,
                    "rx_s": 0, "rx_mn": float("inf"), "rx_mx": 0,
                    "dl_s": 0, "dl_mn": float("inf"), "dl_mx": 0, "n": 0,
                }),
                "channels": defaultdict(lambda: {
                    "tx_s": 0, "tx_mn": float("inf"), "tx_mx": 0,
                    "rx_s": 0, "rx_mn": float("inf"), "rx_mx": 0, "n": 0,
                }),
            }
        b = buckets[bts]
        b["count"] += 1

        v = s.user_tx_bytes or 0
        b["tx_sum"] += v; b["tx_min"] = min(b["tx_min"], v); b["tx_max"] = max(b["tx_max"], v)
        v = s.user_rx_bytes or 0
        b["rx_sum"] += v; b["rx_min"] = min(b["rx_min"], v); b["rx_max"] = max(b["rx_max"], v)
        v = s.user_tx_pkts or 0
        b["txp_sum"] += v; b["txp_min"] = min(b["txp_min"], v); b["txp_max"] = max(b["txp_max"], v)
        v = s.user_rx_pkts or 0
        b["rxp_sum"] += v; b["rxp_min"] = min(b["rxp_min"], v); b["rxp_max"] = max(b["rxp_max"], v)

        if s.tunnel_data:
            try:
                for t in json.loads(s.tunnel_data):
                    key = (t["site"], t["tunnel"])
                    a = b["tunnels"][key]
                    tv = t.get("tx", 0); a["tx_s"] += tv; a["tx_mn"] = min(a["tx_mn"], tv); a["tx_mx"] = max(a["tx_mx"], tv)
                    rv = t.get("rx", 0); a["rx_s"] += rv; a["rx_mn"] = min(a["rx_mn"], rv); a["rx_mx"] = max(a["rx_mx"], rv)
                    dv = t.get("delay_us", 0); a["dl_s"] += dv; a["dl_mn"] = min(a["dl_mn"], dv); a["dl_mx"] = max(a["dl_mx"], dv)
                    a["n"] += 1
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        if s.channel_data:
            try:
                for c in json.loads(s.channel_data):
                    a = b["channels"][c["ch"]]
                    tv = c.get("tx", 0); a["tx_s"] += tv; a["tx_mn"] = min(a["tx_mn"], tv); a["tx_mx"] = max(a["tx_mx"], tv)
                    rv = c.get("rx", 0); a["rx_s"] += rv; a["rx_mn"] = min(a["rx_mn"], rv); a["rx_mx"] = max(a["rx_mx"], rv)
                    a["n"] += 1
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    def _tun_json(b, mode):
        out = []
        for (site, tunnel), a in sorted(b["tunnels"].items()):
            if a["n"] == 0:
                continue
            if mode == "avg":
                out.append({"site": site, "tunnel": tunnel, "tx": a["tx_s"] // a["n"], "rx": a["rx_s"] // a["n"], "delay_us": a["dl_s"] // a["n"]})
            elif mode == "min":
                out.append({"site": site, "tunnel": tunnel, "tx": a["tx_mn"], "rx": a["rx_mn"], "delay_us": a["dl_mn"]})
            else:
                out.append({"site": site, "tunnel": tunnel, "tx": a["tx_mx"], "rx": a["rx_mx"], "delay_us": a["dl_mx"]})
        return json.dumps(out) if out else None

    def _ch_json(b, mode):
        out = []
        for ch in sorted(b["channels"]):
            a = b["channels"][ch]
            if a["n"] == 0:
                continue
            if mode == "avg":
                out.append({"ch": ch, "tx": a["tx_s"] // a["n"], "rx": a["rx_s"] // a["n"]})
            elif mode == "min":
                out.append({"ch": ch, "tx": a["tx_mn"], "rx": a["rx_mn"]})
            else:
                out.append({"ch": ch, "tx": a["tx_mx"], "rx": a["rx_mx"]})
        return json.dumps(out) if out else None

    result = []
    for bts in sorted(buckets):
        b = buckets[bts]
        n = b["count"]
        if n == 0:
            continue

        # Emit min, max, avg rows — frontend pairs min/max for envelope
        for stype, tx, rx, txp, rxp in [
            ("min", b["tx_min"], b["rx_min"], b["txp_min"], b["rxp_min"]),
            ("max", b["tx_max"], b["rx_max"], b["txp_max"], b["rxp_max"]),
            ("raw", b["tx_sum"] // n, b["rx_sum"] // n, b["txp_sum"] // n, b["rxp_sum"] // n),
        ]:
            result.append({
                "timestamp": bts,
                "sample_type": stype,
                "user_tx_bytes": int(tx) if tx != float("inf") else 0,
                "user_tx_pkts": int(txp) if txp != float("inf") else 0,
                "user_rx_bytes": int(rx) if rx != float("inf") else 0,
                "user_rx_pkts": int(rxp) if rxp != float("inf") else 0,
                "channel_data": _ch_json(b, stype if stype != "raw" else "avg"),
                "tunnel_data": _tun_json(b, stype if stype != "raw" else "avg"),
            })

    return result


@router.get("/nodes/{node_id}/chart-stats")
async def get_chart_stats(
    node_id: int,
    start: int | None = Query(default=None, description="Start epoch (inclusive)"),
    end: int | None = Query(default=None, description="End epoch (inclusive)"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Return chart samples for a node, bucketed into 5-minute averages."""
    node = await db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    stmt = select(ChartSample).where(ChartSample.node_id == node_id)
    if start is not None:
        stmt = stmt.where(ChartSample.timestamp >= start)
    if end is not None:
        stmt = stmt.where(ChartSample.timestamp <= end)
    stmt = stmt.order_by(ChartSample.timestamp)

    samples = (await db.scalars(stmt)).all()
    bucketed = _bucket_samples(samples, BUCKET_SECONDS)

    return {
        "node_id": node_id,
        "node_name": node.name,
        "count": len(bucketed),
        "raw_count": len(samples),
        "bucket_seconds": BUCKET_SECONDS,
        "samples": bucketed,
    }


@router.get("/nodes/{node_id}/chart-summary")
async def get_chart_summary(
    node_id: int,
    start: int = Query(..., description="Start epoch (inclusive)"),
    end: int = Query(..., description="End epoch (inclusive)"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Return aggregated chart stats for a node over a time range.

    Computes user throughput totals, per-tunnel averages (tx, rx, delay),
    and per-channel averages.  Tunnel site indexes are cross-referenced
    with the seeker detail cache to resolve mate site IDs.
    """
    node = await db.get(Node, node_id)
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
    samples = (await db.scalars(stmt)).all()

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
