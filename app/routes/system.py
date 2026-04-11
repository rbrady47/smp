"""System routes — /api/status, /api/health, /api/diag."""

import os
import socket
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.diag import DIAG_HANDLERS, parse_diag_input
from app.models import ChartSample, Node

router = APIRouter(prefix="/api")


class DiagRequest(BaseModel):
    """Request body for POST /api/diag."""
    input: str


def _read_proc_cpu() -> dict[str, object]:
    """Read CPU usage from /proc/stat (Linux only)."""
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().strip().split()
        load_1, load_5, load_15 = float(parts[0]), float(parts[1]), float(parts[2])
        cpu_count = os.cpu_count() or 1
        return {
            "load_1m": load_1,
            "load_5m": load_5,
            "load_15m": load_15,
            "cpu_count": cpu_count,
            "usage_pct": round(min(load_1 / cpu_count * 100, 100), 1),
        }
    except Exception:
        return {"load_1m": None, "load_5m": None, "load_15m": None, "cpu_count": None, "usage_pct": None}


def _read_proc_memory() -> dict[str, object]:
    """Read memory stats from /proc/meminfo (Linux only)."""
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    info[key] = int(parts[1]) * 1024  # kB -> bytes
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
        used = total - available
        swap_total = info.get("SwapTotal", 0)
        swap_free = info.get("SwapFree", 0)
        swap_used = swap_total - swap_free
        return {
            "total_bytes": total,
            "used_bytes": used,
            "available_bytes": available,
            "usage_pct": round(used / total * 100, 1) if total else 0,
            "swap_total_bytes": swap_total,
            "swap_used_bytes": swap_used,
        }
    except Exception:
        return {
            "total_bytes": None, "used_bytes": None, "available_bytes": None,
            "usage_pct": None, "swap_total_bytes": None, "swap_used_bytes": None,
        }


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
    """Platform health with chart storage, CPU, memory, and poller status."""

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

    # --- Poller status ---
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
        "cpu": _read_proc_cpu(),
        "memory": _read_proc_memory(),
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


@router.post("/diag")
async def run_diag(payload: DiagRequest) -> dict[str, object]:
    """Execute a diagnostic code and return results."""
    from app.main import _ps

    raw_input = payload.input.strip()
    if not raw_input:
        return {"ok": False, "error": "Empty input. Type 'help' for available codes."}

    code, args = parse_diag_input(raw_input)
    handler = DIAG_HANDLERS.get(code)
    if handler is None:
        available = list(DIAG_HANDLERS.keys())
        return {"ok": False, "error": f"Unknown code: {code}", "available_codes": available}

    try:
        result = await handler(args, _ps)
        return {"ok": True, "code": code, "result": result}
    except Exception as e:
        return {"ok": False, "code": code, "error": str(e)}
