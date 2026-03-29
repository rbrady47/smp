from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DiscoveredNode, DiscoveredNodeObservation, Node
from app.node_projection_service import build_anchor_records


def _merge_source_entries(existing: object, new_source: dict[str, object] | None = None) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    for item in existing if isinstance(existing, list) else []:
        if not isinstance(item, dict):
            continue
        site_id = str(item.get("site_id") or "").strip()
        name = str(item.get("name") or "").strip()
        row_type = str(item.get("row_type") or "anchor").strip() or "anchor"
        key = (site_id, row_type)
        if not site_id or key in seen:
            continue
        seen.add(key)
        merged.append({
            "site_id": site_id,
            "name": name or None,
            "row_type": row_type,
        })

    if isinstance(new_source, dict):
        site_id = str(new_source.get("site_id") or "").strip()
        row_type = str(new_source.get("row_type") or "anchor").strip() or "anchor"
        key = (site_id, row_type)
        if site_id and key not in seen:
            seen.add(key)
            merged.append({
                "site_id": site_id,
                "name": str(new_source.get("name") or "").strip() or None,
                "row_type": row_type,
            })

    return merged


def _candidate_source(site_id: str | None, name: str | None, row_type: str) -> dict[str, object] | None:
    normalized_site_id = str(site_id or "").strip()
    if not normalized_site_id:
        return None
    return {
        "site_id": normalized_site_id,
        "name": str(name or "").strip() or None,
        "row_type": row_type,
    }


def _merge_discovered_candidate(
    backend: Any,
    discovery_candidates: dict[str, dict[str, object]],
    *,
    source_row_type: str,
    source_site_id: str | None,
    source_site_name: str | None,
    source_location: str | None,
    source_unit: str | None,
    source_level: int,
    tunnels: list[dict[str, object]],
    source_node: Node | None,
    anchor_by_site_id: dict[str, dict[str, object]],
) -> None:
    source_entry = _candidate_source(source_site_id, source_site_name, source_row_type)

    for row in tunnels:
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
                "source_node": source_node,
                "location": source_location,
                "unit": source_unit or "--",
                "discovered_level": min(max(source_level, 1) + 1, 3),
                "surfaced_by_site_id": source_site_id or None,
                "surfaced_by_name": source_site_name or None,
                "surfaced_by_names": [source_site_name] if source_site_name else [],
                "surfaced_by_sources": _merge_source_entries([], source_entry),
                "source_row_type": source_row_type,
            },
        )
        candidate["host"] = mate_ip
        candidate["source_node"] = source_node or candidate.get("source_node")
        candidate["location"] = source_location or candidate.get("location")
        candidate["unit"] = source_unit or candidate.get("unit") or "--"
        candidate["discovered_level"] = min(
            int(candidate.get("discovered_level") or 2),
            min(max(source_level, 1) + 1, 3),
        )
        candidate["surfaced_by_site_id"] = candidate.get("surfaced_by_site_id") or source_site_id or None
        candidate["surfaced_by_name"] = candidate.get("surfaced_by_name") or source_site_name or None
        candidate["surfaced_by_names"] = backend._merge_discovered_sources(candidate.get("surfaced_by_names"), source_site_name)
        candidate["surfaced_by_sources"] = _merge_source_entries(candidate.get("surfaced_by_sources"), source_entry)
        candidate["source_row_type"] = candidate.get("source_row_type") or source_row_type


def build_discovery_candidates(
    backend: Any,
    nodes: list[Node],
    anchor_by_site_id: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    discovery_candidates: dict[str, dict[str, object]] = {}

    for node in nodes:
        detail = backend.seeker_detail_cache.get(node.id) or {}
        config_summary = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
        _merge_discovered_candidate(
            backend,
            discovery_candidates,
            source_row_type="anchor",
            source_site_id=str(config_summary.get("site_id") or node.node_id or "").strip() or None,
            source_site_name=str(config_summary.get("site_name") or node.name or "").strip() or None,
            source_location=node.location,
            source_unit=node.topology_unit or "--",
            source_level=int(node.topology_level) if node.topology_level is not None else 1,
            tunnels=[row for row in (detail.get("tunnels") or []) if isinstance(row, dict)],
            source_node=node,
            anchor_by_site_id=anchor_by_site_id,
        )

    for cached in backend.discovered_node_cache.values():
        if not isinstance(cached, dict):
            continue
        cached_detail = cached.get("detail") if isinstance(cached.get("detail"), dict) else {}
        tunnels = cached_detail.get("tunnels") if isinstance(cached_detail.get("tunnels"), list) else []
        if not tunnels:
            continue
        _merge_discovered_candidate(
            backend,
            discovery_candidates,
            source_row_type="discovered",
            source_site_id=str(cached.get("site_id") or "").strip() or None,
            source_site_name=str(cached.get("site_name") or "").strip() or None,
            source_location=str(cached.get("location") or "").strip() or None,
            source_unit=str(cached.get("unit") or "").strip() or None,
            source_level=int(cached.get("discovered_level") or cached.get("level") or 2),
            tunnels=[row for row in tunnels if isinstance(row, dict)],
            source_node=None,
            anchor_by_site_id=anchor_by_site_id,
        )

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
    refreshed_site_ids: set[str] = set()

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
            "latency_ms": ping_payload.get("latency_ms") if ping_up else None,
            "tx_bps": cached.get("tx_bps", 0) if ping_up else 0,
            "rx_bps": cached.get("rx_bps", 0) if ping_up else 0,
            "tx_display": str(cached.get("tx_display") or "--") if ping_up else "--",
            "rx_display": str(cached.get("rx_display") or "--") if ping_up else "--",
            "ping": "Up" if ping_up else "Down",
            "web_ok": bool(cached.get("web_ok")) if ping_up else False,
            "ssh_ok": bool(cached.get("ssh_ok")) if ping_up else False,
            "last_seen": cached.get("last_seen"),
            "last_ping_up": cached.get("last_ping_up"),
            "ping_down_since": cached.get("ping_down_since"),
            "discovered_parent_site_id": candidate.get("surfaced_by_site_id"),
            "discovered_parent_name": candidate.get("surfaced_by_name"),
            "surfaced_by_names": backend._merge_discovered_sources(cached.get("surfaced_by_names"), candidate.get("surfaced_by_names")),
            "surfaced_by_sources": _merge_source_entries(cached.get("surfaced_by_sources"), None),
            "discovered_level": int(candidate.get("discovered_level") or 2),
            "detail": cached.get("detail", {}),
            "probed_at": cached.get("probed_at"),
            "level": int(candidate.get("discovered_level") or 2),
            "surfaced_by_site_id": candidate.get("surfaced_by_site_id"),
            "surfaced_by_name": candidate.get("surfaced_by_name"),
        }
        entry["surfaced_by_sources"] = _merge_source_entries(entry.get("surfaced_by_sources"), None)
        for source in candidate.get("surfaced_by_sources") if isinstance(candidate.get("surfaced_by_sources"), list) else []:
            entry["surfaced_by_sources"] = _merge_source_entries(entry.get("surfaced_by_sources"), source)
        backend._update_discovered_ping_timestamps(entry, ping_up=ping_up, observed_at=observed_at)
        backend._store_discovered_node_cache(site_id, entry)
        backend._upsert_discovered_record(db, entry)
        refreshed_site_ids.add(site_id)
        if ping_up and isinstance(candidate.get("source_node"), Node):
            backend._schedule_discovered_node_probe(
                candidate["source_node"],
                site_id=site_id,
                site_ip=str(candidate["host"]),
                level=int(candidate.get("discovered_level") or 2),
                surfaced_by_site_id=str(candidate.get("surfaced_by_site_id") or "").strip() or None,
                surfaced_by_name=str(candidate.get("surfaced_by_name") or "").strip() or None,
            )

    stale_site_ids = {
        *persisted_discovered.keys(),
        *(site_id for site_id, row in backend.discovered_node_cache.items() if isinstance(row, dict)),
    } - refreshed_site_ids
    for site_id in stale_site_ids:
        if site_id in anchor_by_site_id or site_id in backend.discovered_node_tombstones:
            continue
        cached = {
            **persisted_discovered.get(site_id, {}),
            **(backend.discovered_node_cache.get(site_id, {}) if isinstance(backend.discovered_node_cache.get(site_id), dict) else {}),
        }
        if not cached:
            continue
        observed_at = now
        entry = {
            **cached,
            "row_type": "discovered",
            "pin_key": backend._discovered_pin_key(site_id),
            "detail_url": f"/nodes/discovered/{site_id}",
            "site_id": site_id,
            "site_name": backend._prefer_discovered_site_name(cached.get("site_name"), None, site_id) or f"Site {site_id}",
            "host": str(cached.get("host") or "--"),
            "location": str(cached.get("location") or "--"),
            "unit": str(cached.get("unit") or "--"),
            "version": str(cached.get("version") or "--"),
            "latency_ms": None,
            "tx_bps": 0,
            "rx_bps": 0,
            "tx_display": "--",
            "rx_display": "--",
            "ping": "Down",
            "web_ok": False,
            "ssh_ok": False,
            "last_seen": cached.get("last_seen"),
            "last_ping_up": cached.get("last_ping_up"),
            "ping_down_since": cached.get("ping_down_since"),
            "discovered_parent_site_id": cached.get("discovered_parent_site_id") or cached.get("surfaced_by_site_id"),
            "discovered_parent_name": cached.get("discovered_parent_name") or cached.get("surfaced_by_name"),
            "surfaced_by_names": backend._merge_discovered_sources(cached.get("surfaced_by_names")),
            "surfaced_by_sources": _merge_source_entries(cached.get("surfaced_by_sources"), None),
            "discovered_level": int(cached.get("discovered_level") or cached.get("level") or 2),
            "detail": cached.get("detail", {}),
            "probed_at": cached.get("probed_at"),
            "level": int(cached.get("discovered_level") or cached.get("level") or 2),
            "surfaced_by_site_id": cached.get("surfaced_by_site_id"),
            "surfaced_by_name": cached.get("surfaced_by_name"),
        }
        backend._update_discovered_ping_timestamps(entry, ping_up=False, observed_at=observed_at)
        backend._store_discovered_node_cache(site_id, entry)
        backend._upsert_discovered_record(db, entry)

    db.commit()

