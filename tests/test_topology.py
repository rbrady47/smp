import unittest

from app.topology import (
    TOPOLOGY_LOCATIONS,
    TOPOLOGY_UNITS,
    build_mock_topology_payload,
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

    def test_build_mock_topology_payload_filters_and_binds_inventory_nodes(self) -> None:
        payload = build_mock_topology_payload(
            [
                {
                    "id": "agg-cloud",
                    "name": "Cloud Aggregate",
                    "host": "cloud-agg.example",
                    "location": "cloud",
                    "status": "online",
                    "include_in_topology": True,
                    "topology_level": 0,
                    "topology_unit": "AGG",
                },
                {
                    "id": "lvl1-cloud-divhq",
                    "name": "DIV HQ Cloud",
                    "host": "divhq-cloud.example",
                    "location": "Cloud",
                    "status": "degraded",
                    "include_in_topology": True,
                    "topology_level": 1,
                    "topology_unit": "DIV HQ",
                },
                {
                    "id": "ignored-node",
                    "name": "Ignored",
                    "host": "ignored.example",
                    "location": "Nowhere",
                    "status": "online",
                    "include_in_topology": True,
                    "topology_level": 1,
                    "topology_unit": "DIV HQ",
                },
            ]
        )

        self.assertEqual(payload["locations"], TOPOLOGY_LOCATIONS)
        self.assertEqual(payload["units"], TOPOLOGY_UNITS)
        self.assertEqual(len(payload["lvl0_nodes"]), 3)
        self.assertEqual(len(payload["lvl1_nodes"]), len(TOPOLOGY_LOCATIONS) * len(TOPOLOGY_UNITS))
        self.assertEqual(len(payload["lvl2_clusters"]), len(TOPOLOGY_UNITS))
        self.assertTrue(all(node["name"].endswith("Edge Nodes") for node in payload["lvl2_clusters"]))

        cloud_anchor = next(node for node in payload["lvl0_nodes"] if node["location"] == "Cloud")
        self.assertEqual(cloud_anchor["inventory_node_id"], "agg-cloud")
        self.assertEqual(cloud_anchor["inventory_name"], "Cloud Aggregate")
        self.assertEqual(cloud_anchor["status"], "healthy")

        div_hq_cloud = next(
            node for node in payload["lvl1_nodes"] if node["location"] == "Cloud" and node["unit"] == "DIV HQ"
        )
        self.assertEqual(div_hq_cloud["inventory_node_id"], "lvl1-cloud-divhq")
        self.assertEqual(div_hq_cloud["status"], "degraded")

        self.assertTrue(all(node["inventory_node_id"] != "ignored-node" for node in payload["lvl0_nodes"]))
        self.assertTrue(all(node["inventory_node_id"] != "ignored-node" for node in payload["lvl1_nodes"]))

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
                        "include_in_topology": True,
                        "topology_level": 1,
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
            }
        )

        self.assertEqual(payload["summary"]["total_discovered"], 1)
        self.assertEqual(payload["summary"]["by_location"]["Cloud"], 1)
        self.assertEqual(payload["summary"]["by_unit"]["DIV HQ"], 1)
        self.assertEqual(payload["summary"]["by_location_unit"]["Cloud::DIV HQ"], 1)
        self.assertEqual(payload["anchors"][0]["location"], "Cloud")
        self.assertEqual(payload["anchors"][0]["inventory_node_id"], 1)
        self.assertEqual(payload["discovered"][0]["surfaced_by_site_id"], "1001")
