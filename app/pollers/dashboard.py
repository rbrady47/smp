"""Dashboard poller — projection builder + Redis publisher."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Node
from app.pollers.ping import get_ping_snapshot
from app.seeker_api import normalize_bwv_stats
from app import state_manager

if TYPE_CHECKING:
    from app.poller_state import PollerState

logger = logging.getLogger(__name__)

NODE_DASHBOARD_FAST_REFRESH_SECONDS = 1.0
NODE_DASHBOARD_PROJECTION_REFRESH_SECONDS = 5.0
NODE_DASHBOARD_WINDOW_OPTIONS = {10, 30, 60, 300, 1800, 3600}


def normalize_node_dashboard_window(window_seconds: int | None) -> int:
    try:
        normalized = int(window_seconds or 60)
    except (TypeError, ValueError):
        return 60
    return normalized if normalized in NODE_DASHBOARD_WINDOW_OPTIONS else 60


def apply_windowed_detail_summary(
    detail: dict[str, object],
    *,
    window_metrics: dict[str, object],
) -> dict[str, object]:
    node_summary = dict(detail.get("node_summary") or {})
    avg_latency_ms = window_metrics.get("avg_latency_ms")
    avg_tx_bps = window_metrics.get("avg_tx_bps")
    avg_rx_bps = window_metrics.get("avg_rx_bps")
    rtt_state = str(window_metrics.get("rtt_state") or "")

    if avg_latency_ms is not None:
        node_summary["latency_ms"] = avg_latency_ms
    if avg_tx_bps is not None:
        node_summary["tx_bps"] = avg_tx_bps
    if avg_rx_bps is not None:
        node_summary["rx_bps"] = avg_rx_bps

    node_summary["avg_latency_ms"] = window_metrics.get("avg_latency_ms")
    node_summary["latest_latency_ms"] = window_metrics.get("latest_latency_ms")
    node_summary["rtt_baseline_ms"] = window_metrics.get("rtt_baseline_ms")
    node_summary["rtt_deviation_pct"] = window_metrics.get("rtt_deviation_pct")
    node_summary["rtt_state"] = rtt_state or node_summary.get("rtt_state") or "good"
    node_summary["refresh_window_seconds"] = window_metrics.get("refresh_window_seconds")

    health_by_rtt_state = {
        "good": "Healthy",
        "warn": "Degraded",
        "down": "Critical",
    }
    if rtt_state:
        node_summary["health_state"] = health_by_rtt_state.get(rtt_state, node_summary.get("health_state"))

    return {
        **detail,
        "node_summary": node_summary,
    }


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


async def summarize_dashboard_node(ps: PollerState, node: Node) -> dict[str, object]:
    cached_detail = ps.seeker_detail_cache.get(node.id)
    ping_snapshot = get_ping_snapshot(ps, node)
    normalized = None
    telemetry_data: dict[str, object] = {}
    cfg_summary: dict[str, object] = {}
    cached_node = cached_detail.get("node") if isinstance(cached_detail, dict) and isinstance(cached_detail.get("node"), dict) else {}

    ping_ok = bool(ping_snapshot.get("ping_ok"))
    latency_ms = ping_snapshot.get("latency_ms") if ping_ok else None
    ping_state = str(ping_snapshot.get("state") or "down")
    ping_avg_ms = ping_snapshot.get("avg_latency_ms")
    web_ok = bool(cached_node.get("web_ok")) if cached_node else False
    ssh_ok = bool(cached_node.get("ssh_ok")) if cached_node else False
    node_status = str(cached_node.get("status") or ("online" if ping_ok else "offline"))

    if not ping_ok:
        web_ok = False
        ssh_ok = False
        node_status = "offline"

    if web_ok and ssh_ok:
        node_status = "online"
    elif web_ok or ssh_ok or ping_ok:
        node_status = "degraded"
    elif not ping_ok:
        node_status = "offline"

    if cached_detail:
        cfg_summary = dict(cached_detail.get("config_summary") or {})
        if ping_ok:
            normalized = normalize_bwv_stats(cached_detail.get("raw", {}).get("bwv_stats", {}) or {})
            raw_payload = cached_detail.get("raw", {}).get("bwv_stats")
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
        "ssh_username": node.api_username,
        "site": node.location,
        "status": build_dashboard_status(node_status, normalized),
        "web_ok": web_ok,
        "ssh_ok": ssh_ok,
        "ping_enabled": node.ping_enabled,
        "ping_ok": ping_ok,
        "ping_state": ping_state if node.ping_enabled else "disabled",
        "ping_avg_ms": ping_avg_ms,
        "consecutive_misses": ps.consecutive_misses_by_node.get(node.id, 0),
        "latency_ms": latency_ms,
        "tx_bps": (normalized or {}).get("tx_bps", 0),
        "rx_bps": (normalized or {}).get("rx_bps", 0),
        "wan_tx_bps": (normalized or {}).get("wan_tx_bps", 0),
        "wan_rx_bps": (normalized or {}).get("wan_rx_bps", 0),
        "lan_tx_bps": (normalized or {}).get("lan_tx_bps", 0),
        "lan_rx_bps": (normalized or {}).get("lan_rx_bps", 0),
        "lan_tx_total": (normalized or {}).get("lan_tx_total", "--"),
        "lan_rx_total": (normalized or {}).get("lan_rx_total", "--"),
        "wan_tx_total": (normalized or {}).get("wan_tx_total", "--"),
        "wan_rx_total": (normalized or {}).get("wan_rx_total", "--"),
        "cpu_avg": (normalized or {}).get("cpu_avg"),
        "version": cfg_summary.get("version", "--"),
        "sites_up": sites_up,
        "sites_total": sites_total,
        "wan_up": wan_up,
        "wan_total": wan_total,
        "last_seen": node.last_checked.isoformat() if node.last_checked else None,
    }


async def probe_discovered_node_detail(
    ps: PollerState,
    source_node: Node,
    *,
    site_id: str,
    site_ip: str,
    level: int,
    surfaced_by_site_id: str | None,
    surfaced_by_name: str | None,
) -> dict[str, object] | None:
    return await ps.dashboard_backend.probe_discovered_node_detail(
        source_node,
        site_id=site_id,
        site_ip=site_ip,
        level=level,
        surfaced_by_site_id=surfaced_by_site_id,
        surfaced_by_name=surfaced_by_name,
    )


async def refresh_node_dashboard_cache_once(ps: PollerState) -> None:
    db = SessionLocal()
    try:
        nodes = db.scalars(select(Node).order_by(Node.name)).all()
        await ps.dashboard_backend.refresh_cache(db, nodes)
    finally:
        db.close()


async def _publish_dashboard_to_redis(ps: PollerState) -> None:
    """Publish windowed dashboard state to Redis for SSE delivery.

    Publishes a single batched snapshot instead of per-node events.
    With N anchors + M discovered nodes, per-node publishing generated
    N+M SSE events every second — overwhelming the browser.  A single
    snapshot event keeps the SSE stream lean regardless of node count.
    """
    payload = ps.dashboard_backend.get_cached_payload()
    anchors = {
        str(a["id"]): a for a in (payload.get("anchors") or [])
        if isinstance(a, dict) and a.get("id")
    }
    discovered = {
        str(d["site_id"]): d for d in (payload.get("discovered") or [])
        if isinstance(d, dict) and d.get("site_id")
    }
    await state_manager.publish_dashboard_snapshot(anchors, discovered)


def get_serialized_node_dashboard_cache(ps: PollerState, window_seconds: int | None = None) -> dict[str, object]:
    return ps.dashboard_backend.get_serialized_cache(normalize_node_dashboard_window(window_seconds))


async def node_dashboard_polling_loop(ps: PollerState) -> None:
    while True:
        try:
            await refresh_node_dashboard_cache_once(ps)
            await _publish_dashboard_to_redis(ps)
        except Exception:
            logger.exception("Node dashboard cache refresh failed")
            ps.dashboard_backend.mark_cache_refresh_failed()
        await asyncio.sleep(NODE_DASHBOARD_FAST_REFRESH_SECONDS)
