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
    if status_value == "online":
        return "healthy"
    if status_value == "degraded":
        return "degraded"
    if status_value in {"offline", "disabled"}:
        return "down"
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



def build_mock_topology_payload(inventory_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    inventory_lookup: dict[tuple[str, int, str], dict[str, Any]] = {}
    for node in inventory_nodes:
        if not node.get("include_in_topology", True):
            continue
        location = normalize_topology_location(node.get("location"))
        level = node.get("topology_level")
        unit = node.get("topology_unit")
        if location not in TOPOLOGY_LOCATIONS or level not in (0, 1) or not unit:
            continue
        inventory_lookup[(location, int(level), str(unit))] = node

    lvl0_nodes: list[dict[str, Any]] = []
    lvl1_nodes: list[dict[str, Any]] = []
    lvl2_clusters: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []

    for location in TOPOLOGY_LOCATIONS:
        inventory = inventory_lookup.get((location, 0, "AGG"))
        node_id = f"lvl0-{location.lower()}"
        lvl0_nodes.append(
            {
                "id": node_id,
                "name": f"{location} AGG",
                "location": location,
                "level": 0,
                "unit": "AGG",
                "status": topology_status_from_node_status((inventory or {}).get("status")),
                "include_in_topology": bool((inventory or {}).get("include_in_topology", inventory is not None)),
                "inventory_node_id": (inventory or {}).get("id"),
                "inventory_name": (inventory or {}).get("name"),
                "metrics_text": (inventory or {}).get("host") or "Awaiting anchor binding",
            }
        )

    for left, right in combinations(lvl0_nodes, 2):
        links.append(
            {
                "id": f"mesh-{left['id']}-{right['id']}",
                "from": left["id"],
                "to": right["id"],
                "kind": "backbone",
                "status": "healthy" if left["status"] == right["status"] == "healthy" else "neutral",
            }
        )

    for unit in TOPOLOGY_UNITS:
        cluster_id = f"lvl2-{unit.lower().replace('/', '-').replace(' ', '-')}"
        lvl2_clusters.append(
            {
                "id": cluster_id,
                "name": f"{unit} Edge Nodes",
                "count": TOPOLOGY_LVL2_COUNTS[unit],
                "level": 2,
                "unit": unit,
                "status": "neutral",
                "metrics_text": f"Edge nodes placeholder for {unit} subordinate nodes.",
            }
        )

        for location in TOPOLOGY_LOCATIONS:
            inventory = inventory_lookup.get((location, 1, unit))
            node_id = f"lvl1-{location.lower()}-{unit.lower().replace('/', '-').replace(' ', '-')}"
            lvl1_nodes.append(
                {
                    "id": node_id,
                    "name": unit,
                    "location": location,
                    "level": 1,
                    "unit": unit,
                    "status": topology_status_from_node_status((inventory or {}).get("status")),
                    "include_in_topology": bool((inventory or {}).get("include_in_topology", inventory is not None)),
                    "inventory_node_id": (inventory or {}).get("id"),
                    "inventory_name": (inventory or {}).get("name"),
                    "metrics_text": (inventory or {}).get("host") or "Mock Phase 1 unit anchor slot",
                }
            )
            links.append(
                {
                    "id": f"agg-{location.lower()}-{node_id}",
                    "from": f"lvl0-{location.lower()}",
                    "to": node_id,
                    "kind": "uplink",
                    "status": "healthy" if (inventory or {}).get("status") == "online" else "neutral",
                }
            )
            links.append(
                {
                    "id": f"{node_id}-{cluster_id}",
                    "from": node_id,
                    "to": cluster_id,
                    "kind": "cluster",
                    "status": topology_status_from_node_status((inventory or {}).get("status")),
                }
            )

    # Phase 1 visual demo: bias link colors toward healthy so the topology
    # reads as an operational network, while leaving a few obvious exceptions
    # to preview degraded/down styling before real link health is wired in.
    demo_status_overrides = {
        "mesh-lvl0-cloud-lvl0-hsmc": "healthy",
        "mesh-lvl0-cloud-lvl0-episodic": "healthy",
        "mesh-lvl0-hsmc-lvl0-episodic": "healthy",
        "agg-cloud-lvl1-cloud-div-hq": "down",
        "agg-hsmc-lvl1-hsmc-2bct": "degraded",
        "lvl1-episodic-sustainment-lvl2-sustainment": "down",
    }

    for link in links:
        if link["id"] in demo_status_overrides:
            link["status"] = demo_status_overrides[link["id"]]
        elif link.get("status") == "neutral":
            link["status"] = "healthy"

    return {
        "locations": TOPOLOGY_LOCATIONS,
        "units": TOPOLOGY_UNITS,
        "lvl0_nodes": lvl0_nodes,
        "lvl1_nodes": lvl1_nodes,
        "lvl2_clusters": lvl2_clusters,
        "links": links,
        "inventory_nodes": inventory_nodes,
    }

