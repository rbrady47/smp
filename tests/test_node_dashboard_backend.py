import asyncio
from datetime import datetime, timezone
import os
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///smp-test.db")

from app.db import Base
from app.models import DiscoveredNode, DiscoveredNodeObservation, Node
from app.node_dashboard_backend import NodeDashboardBackend


async def _unused_async(*args, **kwargs):
    raise AssertionError("Unexpected async dependency call")


def _unused_sync(*args, **kwargs):
    raise AssertionError("Unexpected sync dependency call")


class NodeDashboardBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    def tearDown(self) -> None:
        self.session.close()

    def _build_backend(self) -> NodeDashboardBackend:
        return NodeDashboardBackend(
            seeker_detail_cache={},
            summarize_dashboard_node=_unused_async,
            ping_host=lambda host: {"reachable": False, "latency_ms": None},
            check_tcp_port=lambda host, port: {"reachable": False, "latency_ms": None},
            get_bwv_cfg=_unused_async,
            get_bwv_stats=_unused_async,
            normalize_bwv_stats=lambda payload: {},
            build_detail_payload=lambda *args, **kwargs: {},
        )

    def test_ensure_discovered_node_cached_uses_persisted_record(self) -> None:
        backend = self._build_backend()
        self.session.add(
            DiscoveredNode(
                site_id="4001",
                site_name="Delta",
                host="10.10.10.10",
                location="Cloud",
                unit="DIV HQ",
                version="1.2.3",
                discovered_level=2,
                discovered_parent_site_id="1001",
                discovered_parent_name="Anchor A",
                surfaced_by_names_json='["Anchor A"]',
            )
        )
        self.session.add(
            DiscoveredNodeObservation(
                site_id="4001",
                ping="Up",
                last_seen=datetime.now(timezone.utc),
            )
        )
        self.session.commit()

        row = backend.ensure_discovered_node_cached(self.session, "4001")

        self.assertIsNotNone(row)
        self.assertEqual(row["site_id"], "4001")
        self.assertEqual(row["site_name"], "Delta")
        self.assertEqual(row["unit"], "DIV HQ")
        self.assertEqual(row["surfaced_by_names"], ["Anchor A"])
        self.assertIn("4001", backend.discovered_node_cache)

    def test_refresh_discovered_inventory_persists_discovered_inventory(self) -> None:
        anchor = Node(
            id=1,
            name="Anchor A",
            node_id="1001",
            host="10.0.0.1",
            web_port=443,
            ssh_port=22,
            location="Cloud",
            include_in_topology=True,
            topology_level=1,
            topology_unit="DIV HQ",
            enabled=True,
            notes=None,
            api_username=None,
            api_password=None,
            api_use_https=False,
            last_checked=datetime.now(timezone.utc),
            latency_ms=12,
        )

        async def summarize_dashboard_node(node: Node) -> dict[str, object]:
            return {
                "id": node.id,
                "name": node.name,
                "host": node.host,
                "web_port": node.web_port,
                "ssh_port": node.ssh_port,
                "web_scheme": "http",
                "ssh_username": node.api_username,
                "site": node.location,
                "status": "healthy",
                "web_ok": True,
                "ssh_ok": True,
                "ping_ok": True,
                "ping_state": "good",
                "ping_avg_ms": 12,
                "latency_ms": 12,
                "tx_bps": 1000,
                "rx_bps": 2000,
                "cpu_avg": 10.0,
                "version": "1.0.0",
                "sites_up": 1,
                "sites_total": 1,
                "wan_up": 1,
                "wan_total": 1,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            }

        backend = NodeDashboardBackend(
            seeker_detail_cache={
                1: {
                    "config_summary": {"site_id": "1001", "site_name": "Anchor A"},
                    "tunnels": [
                        {"mate_site_id": "4001", "mate_ip": "10.10.10.10"},
                    ],
                }
            },
            summarize_dashboard_node=summarize_dashboard_node,
            ping_host=lambda host: {"reachable": True, "latency_ms": 15},
            check_tcp_port=lambda host, port: {"reachable": False, "latency_ms": None},
            get_bwv_cfg=_unused_async,
            get_bwv_stats=_unused_async,
            normalize_bwv_stats=lambda payload: {},
            build_detail_payload=lambda *args, **kwargs: {},
        )
        backend.discovered_ping_cache["4001"] = {
            "host": "10.10.10.10",
            "reachable": True,
            "latency_ms": 15,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

        asyncio.run(backend.refresh_discovered_inventory(self.session, [anchor]))
        record = self.session.get(DiscoveredNode, "4001")
        observation = self.session.get(DiscoveredNodeObservation, "4001")
        self.assertIsNotNone(record)
        self.assertIsNotNone(observation)
        self.assertEqual(record.host, "10.10.10.10")
        self.assertEqual(record.unit, "DIV HQ")
        self.assertEqual(record.discovered_parent_site_id, "1001")
        self.assertEqual(observation.ping, "Up")
        self.assertEqual(observation.latency_ms, 15)

    def test_build_projection_uses_persisted_and_cached_state_without_discovery_side_effects(self) -> None:
        anchor = Node(
            id=1,
            name="Anchor A",
            node_id="1001",
            host="10.0.0.1",
            web_port=443,
            ssh_port=22,
            location="Cloud",
            include_in_topology=True,
            topology_level=1,
            topology_unit="DIV HQ",
            enabled=True,
            notes=None,
            api_username=None,
            api_password=None,
            api_use_https=False,
            last_checked=datetime.now(timezone.utc),
            latency_ms=12,
        )

        async def summarize_dashboard_node(node: Node) -> dict[str, object]:
            return {
                "id": node.id,
                "name": node.name,
                "host": node.host,
                "web_port": node.web_port,
                "ssh_port": node.ssh_port,
                "web_scheme": "http",
                "ssh_username": node.api_username,
                "site": node.location,
                "status": "healthy",
                "web_ok": True,
                "ssh_ok": True,
                "ping_ok": True,
                "ping_state": "good",
                "ping_avg_ms": 12,
                "latency_ms": 12,
                "tx_bps": 1000,
                "rx_bps": 2000,
                "cpu_avg": 10.0,
                "version": "1.0.0",
                "sites_up": 1,
                "sites_total": 1,
                "wan_up": 1,
                "wan_total": 1,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            }

        backend = NodeDashboardBackend(
            seeker_detail_cache={},
            summarize_dashboard_node=summarize_dashboard_node,
            ping_host=_unused_sync,
            check_tcp_port=_unused_sync,
            get_bwv_cfg=_unused_async,
            get_bwv_stats=_unused_async,
            normalize_bwv_stats=lambda payload: {},
            build_detail_payload=lambda *args, **kwargs: {},
        )
        self.session.add(
            DiscoveredNode(
                site_id="4001",
                site_name="Delta",
                host="10.10.10.10",
                location="Cloud",
                unit="DIV HQ",
                version="1.2.3",
                discovered_level=2,
                discovered_parent_site_id="1001",
                discovered_parent_name="Anchor A",
                surfaced_by_names_json='["Anchor A"]',
            )
        )
        self.session.add(
            DiscoveredNodeObservation(
                site_id="4001",
                ping="Up",
                last_seen=datetime.now(timezone.utc),
            )
        )
        self.session.commit()

        payload = asyncio.run(backend.build_projection(self.session, [anchor]))

        self.assertEqual(len(payload["anchors"]), 1)
        self.assertEqual(len(payload["discovered"]), 1)
        self.assertEqual(payload["discovered"][0]["site_id"], "4001")

    def test_refresh_discovered_inventory_does_not_churn_projection_when_ping_snapshot_is_unchanged(self) -> None:
        anchor = Node(
            id=1,
            name="Anchor A",
            node_id="1001",
            host="10.0.0.1",
            web_port=443,
            ssh_port=22,
            location="Cloud",
            include_in_topology=True,
            topology_level=1,
            topology_unit="DIV HQ",
            enabled=True,
            notes=None,
            api_username=None,
            api_password=None,
            api_use_https=False,
            last_checked=datetime.now(timezone.utc),
            latency_ms=12,
        )

        async def summarize_dashboard_node(node: Node) -> dict[str, object]:
            return {
                "id": node.id,
                "name": node.name,
                "host": node.host,
                "web_port": node.web_port,
                "ssh_port": node.ssh_port,
                "web_scheme": "http",
                "ssh_username": node.api_username,
                "site": node.location,
                "status": "healthy",
                "web_ok": True,
                "ssh_ok": True,
                "ping_ok": True,
                "ping_state": "good",
                "ping_avg_ms": 12,
                "latency_ms": 12,
                "tx_bps": 1000,
                "rx_bps": 2000,
                "cpu_avg": 10.0,
                "version": "1.0.0",
                "sites_up": 1,
                "sites_total": 1,
                "wan_up": 1,
                "wan_total": 1,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            }

        checked_at = datetime.now(timezone.utc).isoformat()
        backend = NodeDashboardBackend(
            seeker_detail_cache={
                1: {
                    "config_summary": {"site_id": "1001", "site_name": "Anchor A"},
                    "tunnels": [
                        {"mate_site_id": "4001", "mate_ip": "10.10.10.10"},
                    ],
                }
            },
            summarize_dashboard_node=summarize_dashboard_node,
            ping_host=lambda host: {"reachable": True, "latency_ms": 15},
            check_tcp_port=lambda host, port: {"reachable": False, "latency_ms": None},
            get_bwv_cfg=_unused_async,
            get_bwv_stats=_unused_async,
            normalize_bwv_stats=lambda payload: {},
            build_detail_payload=lambda *args, **kwargs: {},
        )
        backend.discovered_ping_cache["4001"] = {
            "host": "10.10.10.10",
            "reachable": True,
            "latency_ms": 15,
            "checked_at": checked_at,
        }

        asyncio.run(backend.refresh_discovered_inventory(self.session, [anchor]))
        first_last_seen = backend.discovered_node_cache["4001"]["last_seen"]

        backend.projection_dirty = False
        asyncio.run(backend.refresh_discovered_inventory(self.session, [anchor]))

        self.assertEqual(backend.discovered_node_cache["4001"]["last_seen"], first_last_seen)
        self.assertFalse(backend.projection_dirty)

    def test_delete_discovered_node_removes_inventory_and_observation(self) -> None:
        backend = self._build_backend()
        self.session.add(DiscoveredNode(site_id="4001", site_name="Delta"))
        self.session.add(DiscoveredNodeObservation(site_id="4001", ping="Up"))
        self.session.commit()

        backend.delete_discovered_node(self.session, "4001")

        self.assertIsNone(self.session.get(DiscoveredNode, "4001"))
        self.assertIsNone(self.session.get(DiscoveredNodeObservation, "4001"))
