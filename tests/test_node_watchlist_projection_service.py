import unittest

from app.node_watchlist_projection_service import build_node_watchlist_payload


class NodeWatchlistProjectionServiceTest(unittest.TestCase):
    def test_build_node_watchlist_payload_filters_and_orders_by_pin_key(self) -> None:
        payload = build_node_watchlist_payload(
            {
                "anchors": [
                    {
                        "pin_key": "anchor:1",
                        "row_type": "anchor",
                        "id": 1,
                        "name": "Anchor A",
                        "site_id": "1001",
                        "site_name": "Anchor A",
                        "host": "10.0.0.1",
                        "site": "Cloud",
                        "status": "healthy",
                        "web_ok": True,
                        "ssh_ok": True,
                        "latency_ms": 15,
                        "tx_display": "1.0 Kbps",
                        "rx_display": "2.0 Kbps",
                        "unit": "DIV HQ",
                        "version": "1.0.0",
                        "detail_url": "/nodes/1",
                        "web_port": 443,
                        "ssh_port": 22,
                        "web_scheme": "http",
                    }
                ],
                "discovered": [
                    {
                        "pin_key": "discovered:4001",
                        "row_type": "discovered",
                        "site_id": "4001",
                        "site_name": "Delta",
                        "host": "10.10.10.10",
                        "location": "Cloud",
                        "ping": "Up",
                        "web_ok": False,
                        "ssh_ok": False,
                        "detail_url": "/nodes/discovered/4001",
                    }
                ],
            },
            ["discovered:4001", "anchor:1"],
        )

        self.assertEqual([row["pin_key"] for row in payload["nodes"]], ["discovered:4001", "anchor:1"])
        self.assertEqual(payload["nodes"][0]["status"], "degraded")
        self.assertEqual(payload["nodes"][1]["status"], "healthy")
