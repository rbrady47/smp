from __future__ import annotations

from typing import Any

from app.schemas import TopologyDiscoveryPayload


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


def build_topology_payload_for_map(
    inventory_nodes: list[dict[str, Any]],
    map_id: int | None = None,
) -> dict[str, Any]:
    """Build topology payload containing only nodes assigned to the given map.

    map_id=None -> main map (nodes with topology_map_id == 0)
    map_id=<int> -> submap (nodes with topology_map_id == <int>)
    """
    target_map_id = 0 if map_id is None else map_id

    assigned_nodes = [
        node for node in inventory_nodes
        if node.get("topology_map_id") == target_map_id
    ]

    entities: list[dict[str, Any]] = []
    for node in assigned_nodes:
        entity_id = f"node-{node.get('id') or 0}"
        entities.append({
            "id": entity_id,
            "name": str(node.get("name") or f"Node {node.get('id')}"),
            "location": str(node.get("location") or "--"),
            "status": topology_status_from_inventory(node) if node else "neutral",
            "inventory_node_id": node.get("id"),
            "node_id": node.get("node_id"),
            "site_id": node.get("site_id"),
            "latency_ms": node.get("latency_ms"),
            "rtt_state": node.get("rtt_state"),
            "tx_bps": node.get("tx_bps"),
            "rx_bps": node.get("rx_bps"),
            "tx_display": node.get("tx_display"),
            "rx_display": node.get("rx_display"),
            "cpu_avg": node.get("cpu_avg"),
            "version": node.get("version"),
            "web_port": node.get("web_port"),
            "web_scheme": node.get("web_scheme"),
            "metrics_text": node.get("host") or "--",
            "topology_map_id": node.get("topology_map_id"),
        })

    return {
        "entities": entities,
        "links": [],
    }


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
            "topology_map_id": row.get("topology_map_id"),
            "status": str(row.get("status") or "unknown"),
            "rtt_state": str(row.get("rtt_state") or "").strip() or None,
            "latency_ms": row.get("latency_ms"),
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




