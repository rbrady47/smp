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


def build_topology_discovery_payload(node_dashboard_payload: dict[str, Any]) -> dict[str, Any]:
    anchors: list[dict[str, Any]] = []
    discovered: list[dict[str, Any]] = []
    by_location: dict[str, int] = {}
    by_unit: dict[str, int] = {}
    by_location_unit: dict[str, int] = {}

    for row in node_dashboard_payload.get("anchors") or []:
        if not isinstance(row, dict):
            continue
        anchors.append(
            {
                "inventory_node_id": int(row.get("id") or 0),
                "site_id": str(row.get("site_id") or ""),
                "site_name": str(row.get("site_name") or row.get("name") or ""),
                "location": normalize_topology_location(str(row.get("site") or "").strip() or None),
                "unit": str(row.get("unit") or "").strip() or None,
                "topology_level": int(row.get("topology_level")) if row.get("topology_level") is not None else None,
                "status": str(row.get("status") or "unknown"),
                "include_in_topology": bool(row.get("include_in_topology")),
            }
        )

    for row in node_dashboard_payload.get("discovered") or []:
        if not isinstance(row, dict):
            continue
        location = normalize_topology_location(str(row.get("location") or "").strip() or None)
        unit = str(row.get("unit") or "").strip() or None
        discovered_row = {
            "site_id": str(row.get("site_id") or ""),
            "site_name": str(row.get("site_name") or ""),
            "location": location,
            "unit": unit,
            "discovered_level": int(row.get("discovered_level") or row.get("level") or 2),
            "surfaced_by_site_id": str(row.get("surfaced_by_site_id") or row.get("discovered_parent_site_id") or "").strip() or None,
            "surfaced_by_name": str(row.get("surfaced_by_name") or row.get("discovered_parent_name") or "").strip() or None,
            "ping": str(row.get("ping") or "Down"),
            "web_ok": bool(row.get("web_ok")),
            "ssh_ok": bool(row.get("ssh_ok")),
        }
        discovered.append(discovered_row)

        location_key = location or "--"
        unit_key = unit or "--"
        location_unit_key = f"{location_key}::{unit_key}"
        by_location[location_key] = by_location.get(location_key, 0) + 1
        by_unit[unit_key] = by_unit.get(unit_key, 0) + 1
        by_location_unit[location_unit_key] = by_location_unit.get(location_unit_key, 0) + 1

    return TopologyDiscoveryPayload.model_validate(
        {
            "anchors": anchors,
            "discovered": discovered,
            "summary": {
                "total_discovered": len(discovered),
                "by_location": by_location,
                "by_unit": by_unit,
                "by_location_unit": by_location_unit,
            },
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
