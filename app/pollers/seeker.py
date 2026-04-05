"""Seeker poller — polls anchor node Seeker APIs for config/stats/routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Node
from app.pollers.ping import check_tcp_port, get_ping_snapshot, ping_host
from app.seeker_api import (
    build_detail_payload,
    get_bwv_cfg,
    get_bwv_stats,
    resolve_site_name_map,
)
from app import state_manager

if TYPE_CHECKING:
    from app.poller_state import PollerState

logger = logging.getLogger(__name__)

SEEKER_POLL_INTERVAL_SECONDS = 5.0


async def compute_node_status(ps: PollerState, node: Node) -> dict[str, object]:
    if not node.enabled:
        return {
            "status": "disabled",
            "latency_ms": None,
            "last_checked": node.last_checked,
            "web_ok": False,
            "ssh_ok": False,
            "ping_ok": False,
            "ping_state": "down",
            "ping_avg_ms": None,
        }

    checked_at = datetime.now(timezone.utc)
    ping_snapshot = get_ping_snapshot(ps, node)
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

    latency_ms = ping_snapshot["latency_ms"] if ping_snapshot["ping_ok"] else None

    return {
        "status": status_value,
        "latency_ms": latency_ms,
        "last_checked": checked_at,
        "web_ok": bool(web_check["reachable"]),
        "ssh_ok": bool(ssh_check["reachable"]),
        "ping_ok": bool(ping_snapshot["ping_ok"]),
        "ping_state": str(ping_snapshot["state"]),
        "ping_avg_ms": ping_snapshot["avg_latency_ms"],
    }


async def load_node_detail(ps: PollerState, node: Node) -> dict[str, object]:
    health = await compute_node_status(ps, node)
    node.latency_ms = health["latency_ms"]
    node.last_checked = health["last_checked"]

    cfg_result, stats_result, learnt_routes_result = await asyncio.gather(
        get_bwv_cfg(node),
        get_bwv_stats(node),
        get_bwv_stats(node, extra_args={"learntRoutes": "1"}),
    )

    detail = build_detail_payload(
        node,
        node_health=health,
        cfg_result=cfg_result,
        stats_result=stats_result,
        learnt_routes_result=learnt_routes_result,
    )

    known_site_names: dict[str, str] = {}
    for cached_detail in ps.seeker_detail_cache.values():
        cached_cfg = cached_detail.get("config_summary") if isinstance(cached_detail, dict) else {}
        if not isinstance(cached_cfg, dict):
            continue
        cached_site_id = str(cached_cfg.get("site_id") or "").strip()
        cached_site_name = str(cached_cfg.get("site_name") or "").strip()
        if cached_site_id and cached_site_id != "--" and cached_site_name and cached_site_name != "--":
            known_site_names[cached_site_id] = cached_site_name

    config_summary = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
    current_site_id = str(config_summary.get("site_id") or "").strip()
    current_site_name = str(config_summary.get("site_name") or "").strip()
    if current_site_id and current_site_id != "--" and current_site_name and current_site_name != "--":
        known_site_names[current_site_id] = current_site_name

    site_name_map = await resolve_site_name_map(node, detail.get("tunnels") or [], known_site_names)

    for row in detail.get("tunnels") or []:
        site_id = str(row.get("mate_site_id") or "").strip()
        if site_id in site_name_map:
            row["site_name"] = site_name_map[site_id]

    for row in detail.get("active_sites") or []:
        site_id = str(row.get("site_id") or "").strip()
        if site_id in site_name_map:
            row["site_name"] = site_name_map[site_id]

    tunnels = detail.get("tunnels")
    if isinstance(tunnels, list):
        tunnels.sort(
            key=lambda row: (
                0
                if str(row.get("ping") or "").strip().lower() == "up"
                and str(row.get("mate_index") or "").strip() == "0"
                else 1,
                0 if str(row.get("ping") or "").strip().lower() == "up" else 1,
                int(row.get("mate_index")) if str(row.get("mate_index") or "").isdigit() else 999999,
            )
        )

    return detail


async def refresh_seeker_detail_for_node(ps: PollerState, node: Node) -> dict[str, object]:
    detail = await load_node_detail(ps, node)
    cached = ps.seeker_detail_cache.get(node.id) if isinstance(ps.seeker_detail_cache.get(node.id), dict) else {}
    errors = detail.get("errors") if isinstance(detail.get("errors"), dict) else {}
    raw = detail.get("raw") if isinstance(detail.get("raw"), dict) else {}
    cached_raw = cached.get("raw") if isinstance(cached.get("raw"), dict) else {}

    if errors.get("config") and isinstance(cached.get("config_summary"), dict) and cached.get("config_summary"):
        detail["config_summary"] = dict(cached.get("config_summary") or {})
        raw["bwv_cfg"] = cached_raw.get("bwv_cfg") or raw.get("bwv_cfg") or {}

    if errors.get("stats"):
        for key in ("active_sites", "tunnels", "channels"):
            if isinstance(cached.get(key), list) and cached.get(key):
                detail[key] = list(cached.get(key) or [])
        cached_summary = cached.get("node_summary") if isinstance(cached.get("node_summary"), dict) else {}
        current_summary = detail.get("node_summary") if isinstance(detail.get("node_summary"), dict) else {}
        if cached_summary:
            for key in ("tx_bps", "rx_bps", "site_count", "mate_count", "active_site_count", "wan_count", "cpu_avg"):
                if current_summary.get(key) in (None, 0):
                    current_summary[key] = cached_summary.get(key)
            detail["node_summary"] = current_summary
        raw["bwv_stats"] = cached_raw.get("bwv_stats") or raw.get("bwv_stats") or {}

    if errors.get("routes") and isinstance(cached.get("learnt_routes"), list) and cached.get("learnt_routes"):
        detail["learnt_routes"] = list(cached.get("learnt_routes") or [])
        raw["bwv_stats_learnt_routes"] = cached_raw.get("bwv_stats_learnt_routes") or raw.get("bwv_stats_learnt_routes") or {}

    cached_node = cached.get("node") if isinstance(cached.get("node"), dict) else {}
    current_node = detail.get("node") if isinstance(detail.get("node"), dict) else {}
    if cached_node.get("last_telemetry_pull") and not current_node.get("last_telemetry_pull"):
        current_node["last_telemetry_pull"] = cached_node.get("last_telemetry_pull")
    detail["node"] = current_node
    detail["raw"] = raw
    detail["cached_at"] = datetime.now(timezone.utc).isoformat()
    ps.seeker_detail_cache[node.id] = detail
    await state_manager.update_seeker_cache(node.id, detail)
    return detail


async def seeker_polling_loop(ps: PollerState) -> None:
    while True:
        db = SessionLocal()
        try:
            nodes = db.scalars(select(Node).order_by(Node.id)).all()
        finally:
            db.close()

        enabled_nodes = [node for node in nodes if node.enabled and node.api_username and node.api_password]
        if enabled_nodes:
            results = await asyncio.gather(
                *(refresh_seeker_detail_for_node(ps, node) for node in enabled_nodes),
                return_exceptions=True,
            )
            for node, result in zip(enabled_nodes, results):
                if isinstance(result, Exception):
                    cached = ps.seeker_detail_cache.get(node.id, {})
                    ps.seeker_detail_cache[node.id] = {
                        **cached,
                        "node": {
                            "id": node.id,
                            "name": node.name,
                            "host": node.host,
                            "location": node.location,
                            "status": "offline",
                            "web_ok": False,
                            "ssh_ok": False,
                            "last_refresh": node.last_checked.isoformat() if node.last_checked else None,
                            "last_telemetry_pull": None,
                        },
                        "node_summary": cached.get("node_summary", {}),
                        "config_summary": cached.get("config_summary", {}),
                        "mates": cached.get("mates", []),
                        "tunnels": cached.get("tunnels", []),
                        "channels": cached.get("channels", []),
                        "static_routes": cached.get("static_routes", []),
                        "learnt_routes": cached.get("learnt_routes", []),
                        "errors": {
                            "config": "Polling failed",
                            "stats": "Polling failed",
                            "routes": "Polling failed",
                        },
                        "raw": cached.get("raw", {}),
                        "cached_at": datetime.now(timezone.utc).isoformat(),
                    }
                    await state_manager.update_seeker_cache(node.id, ps.seeker_detail_cache[node.id])

            backfill_needed = []
            for node in enabled_nodes:
                if node.node_id:
                    continue
                detail = ps.seeker_detail_cache.get(node.id) or {}
                cfg = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
                cfg_site_id = str(cfg.get("site_id") or "").strip()
                if cfg_site_id and cfg_site_id != "--":
                    backfill_needed.append((node.id, cfg_site_id))

            if backfill_needed:
                bdb = SessionLocal()
                try:
                    for node_id_pk, site_id_val in backfill_needed:
                        db_node = bdb.get(Node, node_id_pk)
                        if db_node and not db_node.node_id:
                            db_node.node_id = site_id_val
                            logger.info("Backfilled node_id=%s for Node.id=%d", site_id_val, node_id_pk)
                    bdb.commit()
                except Exception:
                    logger.exception("Failed to backfill node_id values")
                    bdb.rollback()
                finally:
                    bdb.close()

        await asyncio.sleep(SEEKER_POLL_INTERVAL_SECONDS)
