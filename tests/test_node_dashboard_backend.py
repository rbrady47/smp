from collections import deque
from datetime import datetime, timedelta, timezone
import unittest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base
from app.models import DiscoveredNode, DiscoveredNodeObservation, Node, NodeRelationship
from app.node_dashboard_backend import NodeDashboardBackend


async def _unused_async(*args, **kwargs):
    raise AssertionError("Unexpected async dependency call")


def _unused_sync(*args, **kwargs):
    raise AssertionError("Unexpected sync dependency call")


class NodeDashboardBackendTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.async_session_factory = async_sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False,
        )
        self.session = self.async_session_factory()

    async def asyncTearDown(self) -> None:
        await self.session.close()
        await self.engine.dispose()

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

    async def test_ensure_discovered_node_cached_uses_persisted_record(self) -> None:
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
                last_ping_up=datetime.now(timezone.utc),
            )
        )
        await self.session.commit()

        row = await backend.ensure_discovered_node_cached(self.session, "4001")

        self.assertIsNotNone(row)
        self.assertEqual(row["site_id"], "4001")
        self.assertEqual(row["site_name"], "Delta")
        self.assertEqual(row["unit"], "DIV HQ")
        self.assertEqual(row["surfaced_by_names"], ["Anchor A"])
        self.assertIn("4001", backend.discovered_node_cache)

    async def test_refresh_discovered_inventory_persists_discovered_inventory(self) -> None:
        anchor = Node(
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
                        {"mate_site_id": "4001", "mate_ip": "10.10.10.10", "ping": "up", "tunnel_up_bitmap": "1"},
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

        await backend.refresh_discovered_inventory(self.session, [anchor])
        record = await self.session.get(DiscoveredNode, "4001")
        observation = await self.session.get(DiscoveredNodeObservation, "4001")
        relationship = await self.session.get(NodeRelationship, {
            "source_site_id": "1001",
            "target_site_id": "4001",
            "relationship_kind": "surfaced_by",
        })
        self.assertIsNotNone(record)
        self.assertIsNotNone(observation)
        self.assertIsNotNone(relationship)
        self.assertEqual(record.host, "10.10.10.10")
        self.assertEqual(record.unit, "--")
        self.assertEqual(record.discovered_parent_site_id, "1001")
        self.assertEqual(observation.ping, "Up")
        self.assertEqual(observation.latency_ms, 15)
        self.assertEqual(relationship.target_unit, "--")

    async def test_build_projection_uses_persisted_and_cached_state_without_discovery_side_effects(self) -> None:
        anchor = Node(
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
                last_ping_up=datetime.now(timezone.utc),
            )
        )
        await self.session.commit()

        payload = await backend.build_projection(self.session, [anchor])

        self.assertEqual(len(payload["anchors"]), 1)
        self.assertEqual(len(payload["discovered"]), 1)
        self.assertEqual(payload["discovered"][0]["site_id"], "4001")

    def test_get_cached_payload_applies_windowed_anchor_metrics(self) -> None:
        backend = self._build_backend()
        sampled_at = datetime.now(timezone.utc)
        backend.node_dashboard_cache = {
            "anchors": [{
                "id": 1,
                "name": "Anchor A",
                "host": "10.0.0.1",
                "web_port": 443,
                "ssh_port": 22,
                "web_scheme": "http",
                "ssh_username": None,
                "site": "Cloud",
                "status": "healthy",
                "web_ok": True,
                "ssh_ok": True,
                "ping_ok": True,
                "ping_state": "good",
                "ping_avg_ms": 100,
                "latency_ms": 100,
                "tx_bps": 1000,
                "rx_bps": 2000,
                "cpu_avg": 10.0,
                "version": "1.0.0",
                "sites_up": 1,
                "sites_total": 1,
                "wan_up": 1,
                "wan_total": 1,
                "last_seen": sampled_at.isoformat(),
                "row_type": "anchor",
                "pin_key": "anchor:1",
                "detail_url": "/nodes/1",
                "site_id": "1001",
                "site_name": "Anchor A",
                "unit": "DIV HQ",
                "last_ping_up": sampled_at.isoformat(),
                "discovered_parent_site_id": None,
                "discovered_parent_name": None,
                "discovered_level": 1,
                "topology_map_id": 0,
                "tx_display": "1.0 Kbps",
                "rx_display": "2.0 Kbps",
            }],
            "discovered": [],
        }
        backend.anchor_metric_history["anchor:1"] = deque([
            {"sampled_at": sampled_at - timedelta(seconds=110), "latency_ms": 100, "tx_bps": 1000, "rx_bps": 2000, "ping_ok": True},
            {"sampled_at": sampled_at - timedelta(seconds=70), "latency_ms": 100, "tx_bps": 1000, "rx_bps": 2000, "ping_ok": True},
            {"sampled_at": sampled_at - timedelta(seconds=20), "latency_ms": 100, "tx_bps": 1000, "rx_bps": 2000, "ping_ok": True},
            {"sampled_at": sampled_at - timedelta(seconds=10), "latency_ms": 100, "tx_bps": 3000, "rx_bps": 5000, "ping_ok": True},
            {"sampled_at": sampled_at, "latency_ms": 180, "tx_bps": 5000, "rx_bps": 7000, "ping_ok": True},
        ])

        payload = backend.get_cached_payload(60)

        self.assertEqual(payload["anchors"][0]["avg_latency_ms"], 127)
        self.assertEqual(payload["anchors"][0]["latency_ms"], 127)
        self.assertEqual(payload["anchors"][0]["latest_latency_ms"], 180)
        self.assertEqual(payload["anchors"][0]["rtt_baseline_ms"], 100)
        self.assertEqual(payload["anchors"][0]["rtt_state"], "warn")
        self.assertEqual(payload["anchors"][0]["avg_tx_bps"], 3000)
        self.assertEqual(payload["anchors"][0]["avg_rx_bps"], 4667)

    def test_get_row_window_metrics_returns_empty_metrics_without_history(self) -> None:
        backend = self._build_backend()

        metrics = backend.get_row_window_metrics("discovered", "4001", 60)

        self.assertEqual(metrics["refresh_window_seconds"], 60)
        self.assertIsNone(metrics["avg_latency_ms"])
        self.assertIsNone(metrics["latest_latency_ms"])
        self.assertEqual(metrics["rtt_state"], "good")

    async def test_refresh_discovered_inventory_does_not_churn_projection_when_ping_snapshot_is_unchanged(self) -> None:
        anchor = Node(
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
                        {"mate_site_id": "4001", "mate_ip": "10.10.10.10", "ping": "up", "tunnel_up_bitmap": "1"},
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

        await backend.refresh_discovered_inventory(self.session, [anchor])
        first_last_seen = backend.discovered_node_cache["4001"]["last_seen"]

        backend.projection_dirty = False
        await backend.refresh_discovered_inventory(self.session, [anchor])

        self.assertEqual(backend.discovered_node_cache["4001"]["last_seen"], first_last_seen)
        self.assertFalse(backend.projection_dirty)

    async def test_delete_discovered_node_removes_inventory_observation_and_relationships(self) -> None:
        backend = self._build_backend()
        self.session.add(DiscoveredNode(site_id="4001", site_name="Delta"))
        self.session.add(DiscoveredNodeObservation(site_id="4001", ping="Up"))
        self.session.add(
            NodeRelationship(
                source_site_id="1001",
                target_site_id="4001",
                relationship_kind="surfaced_by",
                source_row_type="anchor",
                target_row_type="discovered",
            )
        )
        await self.session.commit()

        await backend.delete_discovered_node(self.session, "4001")

        self.assertIsNone(await self.session.get(DiscoveredNode, "4001"))
        self.assertIsNone(await self.session.get(DiscoveredNodeObservation, "4001"))
        self.assertIsNone(
            await self.session.get(
                NodeRelationship,
                {
                    "source_site_id": "1001",
                    "target_site_id": "4001",
                    "relationship_kind": "surfaced_by",
                },
            )
        )

    async def test_get_discovered_ping_snapshot_does_not_reuse_stale_positive_cache(self) -> None:
        backend = self._build_backend()
        stale_checked_at = datetime.now(timezone.utc).replace(year=2025).isoformat()
        backend.discovered_ping_cache["4001"] = {
            "host": "10.10.10.10",
            "reachable": True,
            "latency_ms": 15,
            "checked_at": stale_checked_at,
        }

        snapshot = await backend.get_discovered_ping_snapshot("4001", "10.10.10.10")

        self.assertFalse(snapshot["reachable"])
        self.assertIsNone(snapshot["latency_ms"])

    async def test_refresh_discovered_inventory_marks_node_down_when_ping_fails(self) -> None:
        anchor = Node(
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
                        {"mate_site_id": "4001", "mate_ip": "10.10.10.10", "ping": "up", "tunnel_up_bitmap": "1"},
                    ],
                }
            },
            summarize_dashboard_node=summarize_dashboard_node,
            ping_host=lambda host: {"reachable": False, "latency_ms": None},
            check_tcp_port=lambda host, port: {"reachable": False, "latency_ms": None},
            get_bwv_cfg=_unused_async,
            get_bwv_stats=_unused_async,
            normalize_bwv_stats=lambda payload: {},
            build_detail_payload=lambda *args, **kwargs: {},
        )
        now = datetime.now(timezone.utc).isoformat()
        backend.discovered_ping_cache["4001"] = {
            "host": "10.10.10.10",
            "reachable": False,
            "latency_ms": None,
            "checked_at": now,
        }
        backend.discovered_node_cache["4001"] = {
            "row_type": "discovered",
            "pin_key": "discovered:4001",
            "detail_url": "/nodes/discovered/4001",
            "site_id": "4001",
            "site_name": "Delta",
            "host": "10.10.10.10",
            "location": "Cloud",
            "unit": "DIV HQ",
            "version": "1.2.3",
            "discovered_level": 2,
            "discovered_parent_site_id": "1001",
            "discovered_parent_name": "Anchor A",
            "surfaced_by_names": ["Anchor A"],
            "latency_ms": 15,
            "tx_bps": 1200,
            "rx_bps": 3400,
            "tx_display": "1.2 Kbps",
            "rx_display": "3.4 Kbps",
            "web_ok": True,
            "ssh_ok": True,
            "ping": "Up",
            "last_seen": now,
            "last_ping_up": now,
            "ping_down_since": None,
            "detail": {},
            "probed_at": now,
            "level": 2,
            "surfaced_by_site_id": "1001",
            "surfaced_by_name": "Anchor A",
        }

        await backend.refresh_discovered_inventory(self.session, [anchor])

        # When ping fails, the DN is pruned (not kept as "Down")
        self.assertNotIn("4001", backend.discovered_node_cache)

    async def test_refresh_discovered_inventory_forces_stale_persisted_rows_down_when_not_refreshed(self) -> None:
        anchor = Node(
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
                    "tunnels": [],
                }
            },
            summarize_dashboard_node=summarize_dashboard_node,
            ping_host=lambda host: {"reachable": False, "latency_ms": None},
            check_tcp_port=lambda host, port: {"reachable": False, "latency_ms": None},
            get_bwv_cfg=_unused_async,
            get_bwv_stats=_unused_async,
            normalize_bwv_stats=lambda payload: {},
            build_detail_payload=lambda *args, **kwargs: {},
        )
        last_ping_up = datetime.now(timezone.utc).isoformat()
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
                latency_ms=15,
                tx_bps=1200,
                rx_bps=3400,
                tx_display="1.2 Kbps",
                rx_display="3.4 Kbps",
                web_ok=True,
                ssh_ok=True,
                ping="Up",
                last_seen=datetime.now(timezone.utc),
                last_ping_up=datetime.now(timezone.utc),
            )
        )
        await self.session.commit()

        await backend.refresh_discovered_inventory(self.session, [anchor])

        # Stale persisted rows with no active tunnel are pruned from cache and DB
        self.assertNotIn("4001", backend.discovered_node_cache)
        record = await self.session.get(DiscoveredNode, "4001")
        self.assertIsNone(record)

    async def test_discovered_version_persists_when_refresh_only_has_placeholder(self) -> None:
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
        await self.session.commit()

        await backend._upsert_discovered_inventory_record(self.session, {
            "site_id": "4001",
            "site_name": "Delta",
            "host": "10.10.10.10",
            "location": "Cloud",
            "unit": "DIV HQ",
            "version": "--",
            "discovered_level": 2,
            "discovered_parent_site_id": "1001",
            "discovered_parent_name": "Anchor A",
            "surfaced_by_names": ["Anchor A"],
        })
        await self.session.commit()

        record = await self.session.get(DiscoveredNode, "4001")
        self.assertIsNotNone(record)
        self.assertEqual(record.version, "1.2.3")

    async def test_anchor_version_persists_when_summary_version_is_placeholder(self) -> None:
        anchor = Node(
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
                "version": "--",
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
        backend.node_dashboard_cache["anchors"] = [{
            "id": 1,
            "name": "Anchor A",
            "host": "10.0.0.1",
            "web_port": 443,
            "ssh_port": 22,
            "web_scheme": "http",
            "ssh_username": None,
            "site": "Cloud",
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
            "version": "v1.15.2",
            "sites_up": 1,
            "sites_total": 1,
            "wan_up": 1,
            "wan_total": 1,
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "row_type": "anchor",
            "pin_key": "anchor:1",
            "detail_url": "/nodes/1",
            "site_id": "1001",
            "site_name": "Anchor A",
            "unit": "DIV HQ",
            "last_ping_up": datetime.now(timezone.utc).isoformat(),
            "discovered_parent_site_id": None,
            "discovered_parent_name": None,
            "discovered_level": 1,
            "topology_map_id": 0,
            "tx_display": "1.0 Kbps",
            "rx_display": "2.0 Kbps",
        }]

        payload = await backend.build_projection(self.session, [anchor])

        self.assertEqual(payload["anchors"][0]["version"], "v1.15.2")
