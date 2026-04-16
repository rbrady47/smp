import asyncio
from datetime import datetime, timezone
import unittest

from app.main import ping_snapshot_by_node, seeker_detail_cache, summarize_dashboard_node
from app.models import Node


class NodeDashboardSummaryTest(unittest.TestCase):
    def tearDown(self) -> None:
        seeker_detail_cache.clear()
        ping_snapshot_by_node.clear()

    def test_summarize_dashboard_node_fails_closed_when_ping_is_down(self) -> None:
        node = Node(
            id=1,
            name="Anchor A",
            node_id="1001",
            host="10.0.0.1",
            web_port=443,
            ssh_port=22,
            location="Cloud",
            topology_map_id=0,
            enabled=True,
            notes=None,
            api_username="admin",
            api_password="secret",
            api_use_https=False,
            last_checked=datetime.now(timezone.utc),
            latency_ms=None,
        )
        seeker_detail_cache[node.id] = {
            "node": {
                "status": "online",
                "web_ok": True,
                "ssh_ok": True,
            },
            "config_summary": {"version": "1.2.3"},
            "raw": {
                "bwv_stats": {
                    "tx_bps": 1200,
                    "rx_bps": 3400,
                    "cpu_avg": 10.0,
                    "is_active": True,
                    "rxTunnelLock": [1, 1],
                    "wanUp": [1, 1],
                }
            },
        }
        ping_snapshot_by_node[node.id] = {
            "ping_ok": False,
            "latency_ms": None,
            "avg_latency_ms": None,
            "state": "down",
            "updated_at": datetime.now(timezone.utc),
        }

        summary = asyncio.run(summarize_dashboard_node(node))

        self.assertEqual(summary["status"], "offline")
        self.assertFalse(summary["web_ok"])
        self.assertFalse(summary["ssh_ok"])
        self.assertEqual(summary["tx_bps"], 0)
        self.assertEqual(summary["rx_bps"], 0)
        self.assertEqual(summary["sites_up"], 0)
        self.assertEqual(summary["wan_up"], 0)
