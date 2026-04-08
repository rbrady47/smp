"""Seeker poller — polls anchor node Seeker APIs for config/stats/routes.

The polling loop is split into a **fast path** and a **slow path**:

- **Fast path** (`seeker_polling_loop`): Runs every 5s.  Fetches config, stats,
  and learnt-routes from the Seeker API, applies *already-known* site names
  from the in-memory cache, and publishes the result immediately.

- **Slow path** (`site_name_resolution_loop`): Runs every 30s.  Walks the
  cached tunnel lists looking for peers whose site-name is still unknown,
  probes them via `resolve_site_name_map`, and patches the cached detail
  in-place.  This keeps the expensive per-peer HTTP probes off the critical
  polling cadence.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
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
    seeker_fetch_all,
)
from app import state_manager

if TYPE_CHECKING:
    from app.poller_state import PollerState

logger = logging.getLogger(__name__)

SEEKER_POLL_INTERVAL_SECONDS = 10.0
SITE_NAME_RESOLUTION_INTERVAL_SECONDS = 30.0
SEEKER_POLL_CONCURRENCY = 20  # max simultaneous Seeker API sessions


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


def _collect_known_site_names(ps: PollerState, detail: dict[str, object] | None = None) -> dict[str, str]:
    """Build a site-id → site-name map from everything already in cache.

    This is cheap (pure dict iteration) and used on the fast path so that
    tunnel / active-site rows get labelled without any HTTP calls.
    """
    known: dict[str, str] = {}
    for cached_detail in ps.seeker_detail_cache.values():
        if not isinstance(cached_detail, dict):
            continue
        cached_cfg = cached_detail.get("config_summary")
        if not isinstance(cached_cfg, dict):
            continue
        sid = str(cached_cfg.get("site_id") or "").strip()
        sname = str(cached_cfg.get("site_name") or "").strip()
        if sid and sid != "--" and sname and sname != "--":
            known[sid] = sname

    if isinstance(detail, dict):
        cfg = detail.get("config_summary")
        if isinstance(cfg, dict):
            sid = str(cfg.get("site_id") or "").strip()
            sname = str(cfg.get("site_name") or "").strip()
            if sid and sid != "--" and sname and sname != "--":
                known[sid] = sname

    return known


def _apply_known_site_names(detail: dict[str, object], known: dict[str, str]) -> None:
    """Stamp site names onto tunnel/active-site rows using *already-known* names only."""
    for row in detail.get("tunnels") or []:
        site_id = str(row.get("mate_site_id") or "").strip()
        if site_id in known:
            row["site_name"] = known[site_id]

    for row in detail.get("active_sites") or []:
        site_id = str(row.get("site_id") or "").strip()
        if site_id in known:
            row["site_name"] = known[site_id]


def _sort_tunnels(detail: dict[str, object]) -> None:
    """Sort tunnels: primary-up first, then up, then by mate index."""
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


async def load_node_detail(ps: PollerState, node: Node) -> dict[str, object]:
    """Fast path: fetch config + stats + routes and apply cached site names.

    Does NOT call ``resolve_site_name_map`` — that work is deferred to
    ``site_name_resolution_loop`` so the poll cycle stays < 5 s.
    """
    health = await compute_node_status(ps, node)
    node.latency_ms = health["latency_ms"]
    node.last_checked = health["last_checked"]

    # Single login session for all three data requests (was 3 separate logins)
    cfg_result, stats_result, learnt_routes_result = await seeker_fetch_all(node)

    detail = build_detail_payload(
        node,
        node_health=health,
        cfg_result=cfg_result,
        stats_result=stats_result,
        learnt_routes_result=learnt_routes_result,
    )

    # Apply only names we already know — zero HTTP overhead
    known = _collect_known_site_names(ps, detail)
    _apply_known_site_names(detail, known)
    _sort_tunnels(detail)

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
    """Fast-path poller: config + stats every cycle.  No remote site-name probes."""
    logger.info(
        "Seeker poller started: interval=%.0fs, concurrency=%d",
        SEEKER_POLL_INTERVAL_SECONDS, SEEKER_POLL_CONCURRENCY,
    )
    while True:
        t0 = time.monotonic()
        try:
            def _query_nodes():
                db = SessionLocal()
                try:
                    return db.scalars(select(Node).order_by(Node.id)).all()
                finally:
                    db.close()

            nodes = await asyncio.to_thread(_query_nodes)

            enabled_nodes = [node for node in nodes if node.enabled and node.api_username and node.api_password]
            if enabled_nodes:
                sem = asyncio.Semaphore(SEEKER_POLL_CONCURRENCY)

                async def _poll_with_limit(node: Node) -> dict[str, object]:
                    async with sem:
                        return await asyncio.wait_for(
                            refresh_seeker_detail_for_node(ps, node), timeout=30.0,
                        )

                results = await asyncio.gather(
                    *(_poll_with_limit(node) for node in enabled_nodes),
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
                    def _backfill():
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

                    await asyncio.to_thread(_backfill)

            elapsed = time.monotonic() - t0
            logger.info("Seeker poll cycle completed in %.1fs for %d nodes", elapsed, len(enabled_nodes))
        except Exception:
            logger.exception("Seeker polling loop iteration failed")

        await asyncio.sleep(SEEKER_POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Slow path — background site-name resolution
# ---------------------------------------------------------------------------

async def site_name_resolution_loop(ps: PollerState) -> None:
    """Resolve unknown tunnel-peer site names in the background.

    Runs every 30 s.  Walks each cached node detail, finds tunnel rows whose
    ``site_name`` is missing or ``"--"``, and calls ``resolve_site_name_map``
    to probe the remote Seeker for its config.  Resolved names are patched
    into the cached detail and re-published to Redis.

    Because this runs independently of the fast poller, the dashboard always
    has fresh config/stats data while site names fill in progressively.
    """
    # Give the fast poller a chance to populate the cache first
    await asyncio.sleep(10.0)

    while True:
        t0 = time.monotonic()
        resolved_count = 0
        try:
            def _query_nodes_snr():
                db = SessionLocal()
                try:
                    return db.scalars(select(Node).order_by(Node.id)).all()
                finally:
                    db.close()

            nodes = await asyncio.to_thread(_query_nodes_snr)

            nodes_by_id: dict[int, Node] = {
                n.id: n for n in nodes
                if n.enabled and n.api_username and n.api_password
            }

            known = _collect_known_site_names(ps)

            for node_id, node in nodes_by_id.items():
                detail = ps.seeker_detail_cache.get(node_id)
                if not isinstance(detail, dict):
                    continue

                tunnels = detail.get("tunnels")
                if not isinstance(tunnels, list):
                    continue

                # Are there any tunnels with unknown site names?
                has_unknown = False
                for row in tunnels:
                    sid = str(row.get("mate_site_id") or "").strip()
                    if not sid or sid == "--":
                        continue
                    if sid not in known:
                        has_unknown = True
                        break

                if not has_unknown:
                    continue

                # Resolve via remote probes — this is the expensive part
                try:
                    site_name_map = await resolve_site_name_map(node, tunnels, dict(known))
                except Exception:
                    logger.exception("Site name resolution failed for node %s", node.name)
                    continue

                # Patch the cached detail in-place
                patched = False
                for row in tunnels:
                    sid = str(row.get("mate_site_id") or "").strip()
                    if sid in site_name_map and row.get("site_name") != site_name_map[sid]:
                        row["site_name"] = site_name_map[sid]
                        patched = True

                for row in detail.get("active_sites") or []:
                    sid = str(row.get("site_id") or "").strip()
                    if sid in site_name_map and row.get("site_name") != site_name_map[sid]:
                        row["site_name"] = site_name_map[sid]
                        patched = True

                if patched:
                    resolved_count += len(site_name_map)
                    # Merge resolved names into the known map for next node
                    known.update(site_name_map)
                    await state_manager.update_seeker_cache(node_id, detail)

            elapsed = time.monotonic() - t0
            if resolved_count:
                logger.info(
                    "Site name resolution completed in %.1fs — resolved %d names",
                    elapsed, resolved_count,
                )
            else:
                logger.debug("Site name resolution completed in %.1fs — nothing new", elapsed)
        except Exception:
            logger.exception("Site name resolution loop iteration failed")

        await asyncio.sleep(SITE_NAME_RESOLUTION_INTERVAL_SECONDS)
