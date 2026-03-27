from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DiscoveredNode, DiscoveredNodeObservation, Node
from app.schemas import NodeDashboardAnchorRow, NodeDashboardPayload


async def build_anchor_records(backend: Any, nodes: list[Node]) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    anchor_summaries = await asyncio.gather(*(backend.summarize_dashboard_node(node) for node in nodes))
    anchor_by_site_id: dict[str, dict[str, object]] = {}
    anchors: list[dict[str, object]] = []

    for node, summary in zip(nodes, anchor_summaries):
        detail = backend.seeker_detail_cache.get(node.id) or {}
        config_summary = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
        site_id = str(node.node_id or config_summary.get("site_id") or node.id)
        site_name = str(node.name or config_summary.get("site_name") or f"Node {node.id}")
        record = {
            **summary,
            "row_type": "anchor",
            "pin_key": backend._anchor_pin_key(node.id),
            "detail_url": f"/nodes/{node.id}",
            "site_id": site_id,
            "site_name": site_name,
            "unit": node.topology_unit or "AGG",
            "last_ping_up": summary.get("last_seen"),
            "discovered_parent_site_id": None,
            "discovered_parent_name": None,
            "discovered_level": 1,
            "include_in_topology": bool(node.include_in_topology),
            "topology_level": int(node.topology_level) if node.topology_level is not None else None,
            "tx_display": backend._format_dashboard_rate(summary.get("tx_bps", 0)),
            "rx_display": backend._format_dashboard_rate(summary.get("rx_bps", 0)),
        }
        validated_record = NodeDashboardAnchorRow.model_validate(record).model_dump()
        anchors.append(validated_record)
        anchor_by_site_id[site_id] = validated_record

    return anchors, anchor_by_site_id


async def build_projection(backend: Any, db: Session, nodes: list[Node]) -> dict[str, list[dict[str, object]]]:
    anchors, anchor_by_site_id = await build_anchor_records(backend, nodes)
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
    discovered_map: dict[str, dict[str, object]] = {}

    merged_cached_rows = {**persisted_discovered, **backend.discovered_node_cache}
    for cached_site_id, cached in list(merged_cached_rows.items()):
        if not isinstance(cached, dict):
            continue
        site_id = str(cached.get("site_id") or cached_site_id).strip()
        if not site_id or site_id in anchor_by_site_id or site_id in backend.discovered_node_tombstones:
            continue
        discovered_map[site_id] = {
            **discovered_map.get(site_id, {}),
            **cached,
            "row_type": "discovered",
            "pin_key": backend._discovered_pin_key(site_id),
            "detail_url": f"/nodes/discovered/{site_id}",
            "site_id": site_id,
            "site_name": backend._prefer_discovered_site_name(
                discovered_map.get(site_id, {}).get("site_name"),
                cached.get("site_name"),
                site_id,
            ) or f"Site {site_id}",
            "host": str(cached.get("host") or discovered_map.get(site_id, {}).get("host") or "--"),
            "location": str(cached.get("location") or discovered_map.get(site_id, {}).get("location") or "--"),
            "unit": str(cached.get("unit") or discovered_map.get(site_id, {}).get("unit") or "--"),
            "version": str(cached.get("version") or discovered_map.get(site_id, {}).get("version") or "--"),
            "discovered_level": int(cached.get("level") or discovered_map.get(site_id, {}).get("discovered_level") or 2),
            "discovered_parent_site_id": cached.get("surfaced_by_site_id") or discovered_map.get(site_id, {}).get("discovered_parent_site_id"),
            "discovered_parent_name": cached.get("surfaced_by_name") or discovered_map.get(site_id, {}).get("discovered_parent_name"),
            "surfaced_by_names": backend._merge_discovered_sources(
                discovered_map.get(site_id, {}).get("surfaced_by_names"),
                cached.get("surfaced_by_names"),
                cached.get("surfaced_by_name"),
            ),
            "ping_down_since": cached.get("ping_down_since") or discovered_map.get(site_id, {}).get("ping_down_since"),
            "latency_ms": cached.get("latency_ms") if cached.get("latency_ms") is not None else discovered_map.get(site_id, {}).get("latency_ms"),
        }

    discovered = [
        row for row in discovered_map.values()
        if str(row.get("site_id") or "").strip() not in anchor_by_site_id and backend._should_keep_discovered(row)
    ]
    discovered.sort(
        key=lambda row: (
            int(row.get("discovered_level") or 99),
            str(row.get("discovered_parent_name") or ""),
            0 if str(row.get("ping") or "").strip().lower() == "up" else 1,
            str(row.get("site_name") or "").lower(),
        )
    )

    return NodeDashboardPayload.model_validate({
        "anchors": anchors,
        "discovered": discovered,
    }).model_dump()
