from __future__ import annotations

from itertools import combinations
from typing import Any


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
                "name": f"{unit} Lvl2",
                "count": TOPOLOGY_LVL2_COUNTS[unit],
                "level": 2,
                "unit": unit,
                "status": "neutral",
                "metrics_text": f"Aggregated placeholder for {unit} subordinate nodes.",
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
