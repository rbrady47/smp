"""Node health service — serialize, refresh, and validate nodes."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Node
from app.pollers.ping import get_ping_snapshot
from app.pollers.seeker import compute_node_status, refresh_seeker_detail_for_node
from app.seeker_api import get_bwv_stats, normalize_bwv_stats
from app.topology import normalize_topology_location

if TYPE_CHECKING:
    from app.poller_state import PollerState

logger = logging.getLogger(__name__)

STATUS_PRIORITY = {
    "online": 0,
    "degraded": 1,
    "offline": 2,
    "disabled": 3,
}


def serialize_node(ps: PollerState, node: Node, health: dict[str, object]) -> dict[str, object]:
    detail = ps.seeker_detail_cache.get(node.id) or {}
    config_summary = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
    node_summary = detail.get("node") if isinstance(detail.get("node"), dict) else {}
    version = config_summary.get("version") or "--"
    return {
        "id": node.id,
        "name": node.name,
        "node_id": node.node_id,
        "version": version,
        "host": node.host,
        "web_port": node.web_port,
        "ssh_port": node.ssh_port,
        "location": node.location,
        "include_in_topology": node.include_in_topology,
        "topology_level": node.topology_level,
        "topology_unit": node.topology_unit,
        "topology_location": normalize_topology_location(node.location),
        "enabled": node.enabled,
        "notes": node.notes,
        "api_username": node.api_username,
        "api_password": node.api_password,
        "api_use_https": node.api_use_https,
        "ping_enabled": node.ping_enabled,
        "ping_interval_seconds": node.ping_interval_seconds,
        "charts_enabled": node.charts_enabled,
        "status": health["status"],
        "latency_ms": node.latency_ms,
        "last_checked": node.last_checked.isoformat() if node.last_checked else None,
        "last_telemetry_pull": node_summary.get("last_telemetry_pull"),
        "web_ok": health["web_ok"],
        "ssh_ok": health["ssh_ok"],
        "ping_ok": health["ping_ok"],
        "ping_state": health["ping_state"],
        "ping_avg_ms": health["ping_avg_ms"],
    }


def apply_health_to_node(node: Node, health: dict[str, object]) -> None:
    node.latency_ms = health["latency_ms"]
    node.last_checked = health["last_checked"]


async def refresh_node(ps: PollerState, node: Node) -> dict[str, object]:
    health = await compute_node_status(ps, node)
    apply_health_to_node(node, health)
    return serialize_node(ps, node, health)


async def refresh_nodes(ps: PollerState, nodes: list[Node], db: Session) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for node in nodes:
        if node.enabled and node.api_username and node.api_password:
            try:
                await refresh_seeker_detail_for_node(ps, node)
            except Exception:
                logger.exception("Seeker refresh failed for node %s", node.id)
        try:
            payloads.append(await refresh_node(ps, node))
        except Exception:
            logger.exception("Node health refresh failed for node %s", node.id)
            fallback_health = {
                "status": "offline",
                "latency_ms": None,
                "last_checked": datetime.now(timezone.utc),
                "web_ok": False,
                "ssh_ok": False,
                "ping_ok": False,
                "ping_state": "down",
                "ping_avg_ms": None,
            }
            apply_health_to_node(node, fallback_health)
            payloads.append(serialize_node(ps, node, fallback_health))
    try:
        await ps.dashboard_backend.refresh_cache(db, nodes)
    except Exception:
        logger.exception("Node dashboard cache refresh failed during node refresh")
        ps.dashboard_backend.mark_cache_refresh_failed()
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
