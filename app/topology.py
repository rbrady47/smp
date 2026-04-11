from __future__ import annotations

from itertools import combinations
from typing import Any

from app.schemas import TopologyDiscoveryPayload


TOPOLOGY_LOCATIONS = ["HSMC", "Cloud", "Episodic"]
TOPOLOGY_UNITS = ["DIV HQ", "1BCT", "2BCT", "3BCT", "CAB/DIVARTY", "Sustainment"]
TOPOLOGY_LVL2_COUNTS = {
    "DIV HQ": 63,
    "1BCT": 58,
    "2BCT": 54,
    "3BCT": 49,
    "CAB/DIVARTY": 44,
    "Sustainment": 38,
}
LOCATION_ALIASES = {
    "hsmc": "HSMC",
    "cloud": "Cloud",
    "azure": "Cloud",
    "episodic": "Episodic",
    "epis": "Episodic",
}


def normalize_topology_location(location: str | None) -> str | None:
    if not location:
        return None
    return LOCATION_ALIASES.get(location.strip().lower(), location.strip())


def topology_status_from_node_status(status: str | None) -> str:
    status_value = (status or "").lower()
    if status_value in {"online", "healthy", "up"}:
        return "healthy"
    if status_value == "degraded":
        return "degraded"
    if status_value in {"offline", "disabled", "down", "failed"}:
        return "down"
    return "neutral"


def topology_status_from_rtt_state(rtt_state: str | None) -> str:
    rtt_value = (rtt_state or "").lower()
    if rtt_value == "good":
        return "healthy"
    if rtt_value == "warn":
        return "degraded"
    if rtt_value == "down":
        return "down"
    return "neutral"


def topology_status_from_inventory(inventory: dict[str, Any] | None) -> str:
    if not inventory:
        return "neutral"
    rtt_status = topology_status_from_rtt_state(inventory.get("rtt_state"))
    if rtt_status != "neutral":
        return rtt_status
    return topology_status_from_node_status(inventory.get("status"))


def _merge_topology_statuses(*statuses: str | None) -> str:
    normalized = [str(status or "neutral").strip().lower() for status in statuses]
    if any(status == "down" for status in normalized):
        return "down"
    if any(status == "degraded" for status in normalized):
        return "degraded"
    if normalized and all(status == "healthy" for status in normalized):
        return "healthy"
    return "neutral"


def _resolve_discovered_unit(
    site_id: str,
    discovered_by_site_id: dict[str, dict[str, Any]],
    anchor_units_by_site_id: dict[str, str],
    relationships_by_target: dict[str, list[dict[str, Any]]],
    resolved_cache: dict[str, tuple[str | None, str]],
    lineage_stack: set[str] | None = None,
) -> tuple[str | None, str]:
    if site_id in resolved_cache:
        return resolved_cache[site_id]

    if lineage_stack is None:
        lineage_stack = set()
    if site_id in lineage_stack:
        return (None, "unresolved")

    lineage_stack = {*lineage_stack, site_id}
    relationships = relationships_by_target.get(site_id, [])

    anchor_units = {
        anchor_units_by_site_id.get(str(relationship.get("source_site_id") or "").strip())
        for relationship in relationships
        if str(relationship.get("source_row_type") or "anchor").strip() == "anchor"
    }
    anchor_units.discard(None)
    anchor_units.discard("")

    if len(anchor_units) == 1:
        resolved = (next(iter(anchor_units)), "anchor")
        resolved_cache[site_id] = resolved
        return resolved
    if len(anchor_units) > 1:
        resolved = (None, "ambiguous")
        resolved_cache[site_id] = resolved
        return resolved

    lineage_units: set[str] = set()
    lineage_states: set[str] = set()
    for relationship in relationships:
        if str(relationship.get("source_row_type") or "anchor").strip() != "discovered":
            continue
        source_site_id = str(relationship.get("source_site_id") or "").strip()
        if not source_site_id:
            continue
        source_unit, source_state = _resolve_discovered_unit(
            source_site_id,
            discovered_by_site_id,
            anchor_units_by_site_id,
            relationships_by_target,
            resolved_cache,
            lineage_stack,
        )
        if source_unit:
            lineage_units.add(source_unit)
        if source_state:
            lineage_states.add(source_state)

    if len(lineage_units) == 1:
        resolved = (next(iter(lineage_units)), "dn_lineage")
        resolved_cache[site_id] = resolved
        return resolved
    if len(lineage_units) > 1:
        resolved = (None, "ambiguous")
        resolved_cache[site_id] = resolved
        return resolved

    discovered_row = discovered_by_site_id.get(site_id) or {}
    fallback_unit = str(discovered_row.get("unit") or "").strip()
    if fallback_unit and fallback_unit != "--":
        resolved = (fallback_unit, "fallback")
        resolved_cache[site_id] = resolved
        return resolved

    resolved = (None, "unresolved")
    resolved_cache[site_id] = resolved
    return resolved



def build_topology_discovery_payload(
    node_dashboard_payload: dict[str, Any],
    relationships_payload: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    anchors: list[dict[str, Any]] = []
    discovered: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    by_location: dict[str, int] = {}
    by_unit: dict[str, int] = {}
    by_location_unit: dict[str, int] = {}
    by_relationship_kind: dict[str, int] = {}
    by_unit_source: dict[str, int] = {}
    anchor_units_by_site_id: dict[str, str] = {}

    for row in node_dashboard_payload.get("anchors") or []:
        if not isinstance(row, dict):
            continue
        anchor = {
            "inventory_node_id": int(row.get("id") or 0),
            "site_id": str(row.get("site_id") or ""),
            "site_name": str(row.get("site_name") or row.get("name") or ""),
            "location": normalize_topology_location(str(row.get("site") or "").strip() or None),
            "unit": str(row.get("unit") or "").strip() or None,
            "topology_level": int(row.get("topology_level")) if row.get("topology_level") is not None else None,
            "status": str(row.get("status") or "unknown"),
            "rtt_state": str(row.get("rtt_state") or "").strip() or None,
            "latency_ms": row.get("latency_ms"),
            "include_in_topology": bool(row.get("include_in_topology")),
        }
        anchors.append(anchor)
        if anchor["site_id"] and anchor["unit"]:
            anchor_units_by_site_id[anchor["site_id"]] = str(anchor["unit"])

    for relationship in relationships_payload or []:
        if not isinstance(relationship, dict):
            continue
        relationship_kind = str(relationship.get("relationship_kind") or "").strip()
        relationship_row = {
            "source_site_id": str(relationship.get("source_site_id") or "").strip(),
            "target_site_id": str(relationship.get("target_site_id") or "").strip(),
            "relationship_kind": relationship_kind,
            "source_row_type": str(relationship.get("source_row_type") or "anchor").strip() or "anchor",
            "target_row_type": str(relationship.get("target_row_type") or "discovered").strip() or "discovered",
            "source_name": str(relationship.get("source_name") or "").strip() or None,
            "target_name": str(relationship.get("target_name") or "").strip() or None,
            "target_unit": str(relationship.get("target_unit") or "").strip() or None,
            "target_location": normalize_topology_location(str(relationship.get("target_location") or "").strip() or None),
            "discovered_level": int(relationship.get("discovered_level")) if relationship.get("discovered_level") is not None else None,
        }
        if not relationship_row["source_site_id"] or not relationship_row["target_site_id"] or not relationship_kind:
            continue
        relationships.append(relationship_row)
        by_relationship_kind[relationship_kind] = by_relationship_kind.get(relationship_kind, 0) + 1

    relationships_by_target: dict[str, list[dict[str, Any]]] = {}
    for relationship in relationships:
        relationships_by_target.setdefault(str(relationship["target_site_id"]), []).append(relationship)

    discovered_seed_rows: list[dict[str, Any]] = []
    for row in node_dashboard_payload.get("discovered") or []:
        if not isinstance(row, dict):
            continue
        location = normalize_topology_location(str(row.get("location") or "").strip() or None)
        unit = str(row.get("unit") or "").strip() or None
        discovered_seed_rows.append(
            {
                "site_id": str(row.get("site_id") or ""),
                "site_name": str(row.get("site_name") or ""),
                "host": str(row.get("host") or "").strip() or None,
                "version": str(row.get("version") or "").strip() or None,
                "location": location,
                "unit": unit,
                "discovered_level": int(row.get("discovered_level") or row.get("level") or 2),
                "surfaced_by_site_id": str(row.get("surfaced_by_site_id") or row.get("discovered_parent_site_id") or "").strip() or None,
                "surfaced_by_name": str(row.get("surfaced_by_name") or row.get("discovered_parent_name") or "").strip() or None,
                "surfaced_by_names": [str(value).strip() for value in (row.get("surfaced_by_names") or []) if str(value).strip()],
                "ping": str(row.get("ping") or "Down"),
                "rtt_state": str(row.get("rtt_state") or "").strip() or None,
                "latency_ms": row.get("latency_ms"),
                "web_ok": bool(row.get("web_ok")),
                "ssh_ok": bool(row.get("ssh_ok")),
            }
        )

    discovered_by_site_id = {str(row.get("site_id") or ""): row for row in discovered_seed_rows}
    resolved_cache: dict[str, tuple[str | None, str]] = {}

    for row in discovered_seed_rows:
        site_id = str(row.get("site_id") or "")
        resolved_unit, unit_source = _resolve_discovered_unit(
            site_id,
            discovered_by_site_id,
            anchor_units_by_site_id,
            relationships_by_target,
            resolved_cache,
        )
        discovered_row = {
            **row,
            "resolved_unit": resolved_unit,
            "unit_source": unit_source,
        }
        discovered.append(discovered_row)

        effective_unit = resolved_unit or row.get("unit")
        location_key = str(row.get("location") or "--")
        unit_key = str(effective_unit or "--")
        location_unit_key = f"{location_key}::{unit_key}"
        by_location[location_key] = by_location.get(location_key, 0) + 1
        by_unit[unit_key] = by_unit.get(unit_key, 0) + 1
        by_location_unit[location_unit_key] = by_location_unit.get(location_unit_key, 0) + 1
        by_unit_source[unit_source] = by_unit_source.get(unit_source, 0) + 1

    return TopologyDiscoveryPayload.model_validate(
        {
            "anchors": anchors,
            "discovered": discovered,
            "summary": {
                "total_discovered": len(discovered),
                "total_relationships": len(relationships),
                "by_location": by_location,
                "by_unit": by_unit,
                "by_location_unit": by_location_unit,
                "by_relationship_kind": by_relationship_kind,
                "by_unit_source": by_unit_source,
            },
            "relationships": relationships,
        }
    ).model_dump()



def _make_entity(node: dict[str, Any] | None, *, entity_id: str, name: str, location: str, level: int, unit: str) -> dict[str, Any]:
    """Build a topology entity dict, overlaying inventory data when available."""
    return {
        "id": entity_id,
        "name": str(node.get("name") or name) if node else name,
        "location": location,
        "level": level,
        "unit": unit,
        "status": topology_status_from_inventory(node) if node else "neutral",
        "include_in_topology": True,
        "inventory_node_id": node.get("id") if node else None,
        "inventory_name": node.get("name") if node else None,
        "node_id": node.get("node_id") if node else None,
        "site_id": node.get("site_id") if node else None,
        "latency_ms": node.get("latency_ms") if node else None,
        "rtt_state": node.get("rtt_state") if node else None,
        "tx_bps": node.get("tx_bps") if node else None,
        "rx_bps": node.get("rx_bps") if node else None,
        "tx_display": node.get("tx_display") if node else None,
        "rx_display": node.get("rx_display") if node else None,
        "cpu_avg": node.get("cpu_avg") if node else None,
        "version": node.get("version") if node else None,
        "web_port": node.get("web_port") if node else None,
        "web_scheme": node.get("web_scheme") if node else None,
        "metrics_text": (node.get("host") or "Awaiting first Seeker pull") if node else "Awaiting first Seeker pull",
    }


def build_mock_topology_payload(inventory_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    authored_nodes = [
        node
        for node in inventory_nodes
        if node.get("include_in_topology", True)
    ]

    # Index inventory nodes by topology position for binding
    inv_by_loc_lvl0: dict[str, dict[str, Any]] = {}
    inv_by_loc_unit_lvl1: dict[tuple[str, str], dict[str, Any]] = {}

    for node in authored_nodes:
        location = normalize_topology_location(node.get("location")) or "Cloud"
        if location not in TOPOLOGY_LOCATIONS:
            location = "Cloud"
        level = int(node.get("topology_level") or 0)
        unit = str(node.get("topology_unit") or ("AGG" if level == 0 else "DIV HQ")).strip() or ("AGG" if level == 0 else "DIV HQ")
        if level == 0:
            inv_by_loc_lvl0.setdefault(location, node)
        else:
            inv_by_loc_unit_lvl1.setdefault((location, unit), node)

    # Generate skeleton lvl0 nodes (one per location), overlay inventory data
    lvl0_nodes: list[dict[str, Any]] = []
    for location in TOPOLOGY_LOCATIONS:
        inv = inv_by_loc_lvl0.get(location)
        entity = _make_entity(
            inv,
            entity_id=f"node-{inv.get('id') or 0}" if inv else f"agg-{location.lower()}",
            name=f"{location} Aggregate",
            location=location,
            level=0,
            unit="AGG",
        )
        lvl0_nodes.append(entity)

    # Generate skeleton lvl1 nodes (one per location × unit)
    lvl1_nodes: list[dict[str, Any]] = []
    for location in TOPOLOGY_LOCATIONS:
        for unit in TOPOLOGY_UNITS:
            inv = inv_by_loc_unit_lvl1.get((location, unit))
            entity = _make_entity(
                inv,
                entity_id=f"node-{inv.get('id') or 0}" if inv else f"lvl1-{location.lower()}-{unit.lower().replace(' ', '-').replace('/', '-')}",
                name=f"{location} {unit}",
                location=location,
                level=1,
                unit=unit,
            )
            lvl1_nodes.append(entity)

    # Generate lvl2 cluster entries (one per unit)
    lvl2_clusters: list[dict[str, Any]] = [
        {
            "id": f"cluster-{unit.lower().replace(' ', '-').replace('/', '-')}",
            "name": f"{unit} Edge Nodes",
            "unit": unit,
            "count": TOPOLOGY_LVL2_COUNTS.get(unit, 0),
            "level": 2,
            "status": "neutral",
        }
        for unit in TOPOLOGY_UNITS
    ]

    # Generate backbone links between all lvl0 node pairs
    links: list[dict[str, Any]] = []
    for a, b in combinations(lvl0_nodes, 2):
        link_status = _merge_topology_statuses(a["status"], b["status"])
        links.append({
            "id": f"backbone-{a['id']}-{b['id']}",
            "from": a["id"],
            "to": b["id"],
            "kind": "backbone",
            "status": link_status,
        })

    return {
        "locations": TOPOLOGY_LOCATIONS,
        "units": TOPOLOGY_UNITS,
        "lvl0_nodes": lvl0_nodes,
        "lvl1_nodes": lvl1_nodes,
        "lvl2_clusters": lvl2_clusters,
        "links": links,
        "inventory_nodes": inventory_nodes,
    }

