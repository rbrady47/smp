import unittest

from app.topology import (
    build_topology_payload_for_map,
    build_topology_discovery_payload,
    normalize_topology_location,
    topology_status_from_node_status,
)


class TopologyHelpersTest(unittest.TestCase):
    def test_normalize_topology_location_aliases(self) -> None:
        self.assertEqual(normalize_topology_location("hsmc"), "HSMC")
        self.assertEqual(normalize_topology_location("Azure"), "Cloud")
        self.assertEqual(normalize_topology_location(" Epis "), "Episodic")
        self.assertIsNone(normalize_topology_location(None))

    def test_topology_status_from_node_status(self) -> None:
        self.assertEqual(topology_status_from_node_status("online"), "healthy")
        self.assertEqual(topology_status_from_node_status("degraded"), "degraded")
        self.assertEqual(topology_status_from_node_status("offline"), "down")
        self.assertEqual(topology_status_from_node_status("disabled"), "down")
        self.assertEqual(topology_status_from_node_status("unknown"), "neutral")

    def test_build_topology_payload_for_map_returns_only_assigned_nodes(self) -> None:
        payload = build_topology_payload_for_map(
            [
                {
                    "id": 1,
                    "name": "Node A",
                    "host": "10.0.0.1",
                    "location": "Cloud",
                    "status": "online",
                    "topology_map_id": 0,
                    "rtt_state": "good",
                },
                {
                    "id": 2,
                    "name": "Node B",
                    "host": "10.0.0.2",
                    "location": "HSMC",
                    "status": "degraded",
                    "topology_map_id": 0,
                    "rtt_state": None,
                },
                {
                    "id": 3,
                    "name": "Orphan",
                    "host": "10.0.0.3",
                    "location": "Cloud",
                    "status": "online",
                    "topology_map_id": None,
                },
                {
                    "id": 4,
                    "name": "Submap Node",
                    "host": "10.0.0.4",
                    "location": "Cloud",
                    "status": "online",
                    "topology_map_id": 5,
                },
            ],
            map_id=None,
        )

        self.assertEqual(len(payload["entities"]), 2)
        self.assertEqual(payload["entities"][0]["id"], "node-1")
        self.assertEqual(payload["entities"][0]["name"], "Node A")
        self.assertEqual(payload["entities"][0]["status"], "healthy")
        self.assertEqual(payload["entities"][1]["id"], "node-2")
        self.assertEqual(payload["entities"][1]["status"], "degraded")
        self.assertEqual(payload["links"], [])

    def test_build_topology_payload_for_submap(self) -> None:
        payload = build_topology_payload_for_map(
            [
                {"id": 1, "name": "Main Node", "topology_map_id": 0, "status": "online", "rtt_state": None},
                {"id": 2, "name": "Submap Node", "topology_map_id": 5, "status": "online", "rtt_state": None},
            ],
            map_id=5,
        )

        self.assertEqual(len(payload["entities"]), 1)
        self.assertEqual(payload["entities"][0]["inventory_node_id"], 2)

    def test_build_topology_payload_orphans_excluded(self) -> None:
        payload = build_topology_payload_for_map(
            [
                {"id": 1, "name": "Orphan", "topology_map_id": None, "status": "online", "rtt_state": None},
            ],
            map_id=None,
        )
        self.assertEqual(len(payload["entities"]), 0)

    def test_build_topology_discovery_payload_projects_dashboard_rows(self) -> None:
        payload = build_topology_discovery_payload(
            {
                "anchors": [
                    {
                        "id": 1,
                        "site_id": "1001",
                        "site_name": "Anchor A",
                        "site": "cloud",
                        "unit": "DIV HQ",
                        "status": "healthy",
                        "topology_map_id": 0,
                    }
                ],
                "discovered": [
                    {
                        "site_id": "4001",
                        "site_name": "Delta",
                        "location": "cloud",
                        "unit": "DIV HQ",
                        "discovered_level": 2,
                        "surfaced_by_site_id": "1001",
                        "surfaced_by_name": "Anchor A",
                        "ping": "Up",
                        "web_ok": True,
                        "ssh_ok": False,
                    }
                ],
            },
            [
                {
                    "source_site_id": "1001",
                    "target_site_id": "4001",
                    "relationship_kind": "surfaced_by",
                    "source_row_type": "anchor",
                    "target_row_type": "discovered",
                    "source_name": "Anchor A",
                    "target_name": "Delta",
                    "target_unit": "DIV HQ",
                    "target_location": "cloud",
                    "discovered_level": 2,
                }
            ]
        )

        self.assertEqual(payload["summary"]["total_discovered"], 1)
        self.assertEqual(payload["summary"]["total_relationships"], 1)
        self.assertEqual(payload["summary"]["by_relationship_kind"]["surfaced_by"], 1)
        self.assertEqual(payload["summary"]["by_location"]["Cloud"], 1)
        self.assertEqual(payload["summary"]["by_unit"]["DIV HQ"], 1)
        self.assertEqual(payload["summary"]["by_location_unit"]["Cloud::DIV HQ"], 1)
        self.assertEqual(payload["summary"]["by_unit_source"]["anchor"], 1)
        self.assertEqual(payload["anchors"][0]["location"], "Cloud")
        self.assertEqual(payload["anchors"][0]["inventory_node_id"], 1)
        self.assertEqual(payload["discovered"][0]["surfaced_by_site_id"], "1001")
        self.assertEqual(payload["discovered"][0]["resolved_unit"], "DIV HQ")
        self.assertEqual(payload["discovered"][0]["unit_source"], "anchor")
        self.assertEqual(payload["relationships"][0]["source_site_id"], "1001")
        self.assertEqual(payload["relationships"][0]["target_site_id"], "4001")
