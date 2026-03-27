from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DiscoveredNode, DiscoveredNodeObservation, Node
from app.node_projection_service import build_anchor_records


def build_discovery_candidates(
    backend: Any,
    nodes: list[Node],
    anchor_by_site_id: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    discovery_candidates: dict[str, dict[str, object]] = {}

    for node in nodes:
        detail = backend.seeker_detail_cache.get(node.id) or {}
        config_summary = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
        parent_site_id = str(config_summary.get("site_id") or "")
        parent_site_name = str(config_summary.get("site_name") or node.name)
        for row in detail.get("tunnels") or []:
            site_id = str(row.get("mate_site_id") or "").strip()
            if not site_id or site_id in anchor_by_site_id or site_id in backend.discovered_node_tombstones:
                continue
            mate_ip = str(row.get("mate_ip") or "").strip()
            if not mate_ip or mate_ip == "--":
                continue
            candidate = discovery_candidates.setdefault(
                site_id,
                {
                    "site_id": site_id,
                    "host": mate_ip,
                    "source_node": node,
                    "location": node.location,
                    "unit": node.topology_unit or "--",
                    "discovered_level": min((int(node.topology_level) if node.topology_level is not None else 1) + 1, 3),
                    "surfaced_by_site_id": parent_site_id or None,
                    "surfaced_by_name": parent_site_name or None,
                    "surfaced_by_names": [parent_site_name] if parent_site_name else [],
                },
            )
            candidate["host"] = mate_ip
            candidate["source_node"] = node
            candidate["location"] = node.location
            candidate["unit"] = node.topology_unit or candidate.get("unit") or "--"
            candidate["discovered_level"] = min(
                candidate.get("discovered_level", 2),
                min((int(node.topology_level) if node.topology_level is not None else 1) + 1, 3),
            )
            candidate["surfaced_by_site_id"] = candidate.get("surfaced_by_site_id") or parent_site_id or None
            candidate["surfaced_by_name"] = candidate.get("surfaced_by_name") or parent_site_name or None
            candidate["surfaced_by_names"] = backend._merge_discovered_sources(candidate.get("surfaced_by_names"), parent_site_name)

    return discovery_candidates


async def refresh_discovered_inventory(backend: Any, db: Session, nodes: list[Node]) -> None:
    for cached_site_id, cached in list(backend.discovered_node_cache.items()):
        if not isinstance(cached, dict):
            backend.discovered_node_cache.pop(cached_site_id, None)
            continue
        if str(cached.get("ping") or "").strip().lower() != "up" and not backend._should_keep_discovered(cached):
            backend.discovered_node_cache.pop(cached_site_id, None)

    _, anchor_by_site_id = await build_anchor_records(backend, nodes)
    inventory_records = {
        record.site_id: record
        for record in db.scalars(select(DiscoveredNode).order_by(DiscoveredNode.site_id)).all()
    }
    observation_records = {
        record.site_id: record
        for record in db.scalars(select(DiscoveredNodeObservation).order_by(DiscoveredNodeObservation.site_id)).all()
    }
    persisted_discovered = {
        site_id: backend._compose_discovered_row(inventory_record, observation_records.get(site_id))
        for site_id, inventory_record in inventory_records.items()
    }
    discovery_candidates = build_discovery_candidates(backend, nodes, anchor_by_site_id)
    now = datetime.now(timezone.utc)

    for site_id, candidate in discovery_candidates.items():
        ping_payload = await backend.get_discovered_ping_snapshot(site_id, str(candidate["host"]))
        ping_up = bool(ping_payload.get("reachable"))
        observed_at = backend._safe_parse_iso(ping_payload.get("checked_at")) or now
        cached = {
            **persisted_discovered.get(site_id, {}),
            **(backend.discovered_node_cache.get(site_id, {}) if isinstance(backend.discovered_node_cache.get(site_id), dict) else {}),
        }
        entry = {
            **cached,
            "row_type": "discovered",
            "pin_key": backend._discovered_pin_key(site_id),
            "detail_url": f"/nodes/discovered/{site_id}",
            "site_id": site_id,
            "site_name": backend._prefer_discovered_site_name(cached.get("site_name"), candidate.get("site_name"), site_id) or f"Site {site_id}",
            "host": str(candidate.get("host") or cached.get("host") or "--"),
            "location": str(candidate.get("location") or cached.get("location") or "--"),
            "unit": str(candidate.get("unit") or cached.get("unit") or "--"),
            "version": str(cached.get("version") or "--"),
            "latency_ms": ping_payload.get("latency_ms") if ping_payload.get("latency_ms") is not None else cached.get("latency_ms"),
            "tx_bps": cached.get("tx_bps", 0),
            "rx_bps": cached.get("rx_bps", 0),
            "tx_display": str(cached.get("tx_display") or "--"),
            "rx_display": str(cached.get("rx_display") or "--"),
            "ping": "Up" if ping_up else str(cached.get("ping") or "Down"),
            "web_ok": bool(cached.get("web_ok")),
            "ssh_ok": bool(cached.get("ssh_ok")),
            "last_seen": cached.get("last_seen"),
            "last_ping_up": cached.get("last_ping_up"),
            "ping_down_since": cached.get("ping_down_since"),
            "discovered_parent_site_id": candidate.get("surfaced_by_site_id"),
            "discovered_parent_name": candidate.get("surfaced_by_name"),
            "surfaced_by_names": backend._merge_discovered_sources(cached.get("surfaced_by_names"), candidate.get("surfaced_by_names")),
            "discovered_level": int(candidate.get("discovered_level") or 2),
            "detail": cached.get("detail", {}),
            "probed_at": cached.get("probed_at"),
            "level": int(candidate.get("discovered_level") or 2),
            "surfaced_by_site_id": candidate.get("surfaced_by_site_id"),
            "surfaced_by_name": candidate.get("surfaced_by_name"),
        }
        backend._update_discovered_ping_timestamps(entry, ping_up=ping_up, observed_at=observed_at)
        backend._store_discovered_node_cache(site_id, entry)
        backend._upsert_discovered_record(db, entry)
        if ping_up:
            backend._schedule_discovered_node_probe(
                candidate["source_node"],
                site_id=site_id,
                site_ip=str(candidate["host"]),
                level=int(candidate.get("discovered_level") or 2),
                surfaced_by_site_id=str(candidate.get("surfaced_by_site_id") or "").strip() or None,
                surfaced_by_name=str(candidate.get("surfaced_by_name") or "").strip() or None,
            )

    db.commit()
