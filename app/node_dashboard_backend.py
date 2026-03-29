from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import re
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DiscoveredNode, DiscoveredNodeObservation, Node, NodeRelationship
from app.node_discovery_service import refresh_discovered_inventory
from app.node_projection_service import build_anchor_records, build_projection
from app.schemas import NodeDashboardDiscoveredRow


class NodeDashboardBackend:
    def __init__(
        self,
        *,
        seeker_detail_cache: dict[int, dict[str, object]],
        summarize_dashboard_node: Callable[[Node], Awaitable[dict[str, object]]],
        ping_host: Callable[[str], dict[str, int | bool | None]],
        check_tcp_port: Callable[[str, int], dict[str, int | bool | None]],
        get_bwv_cfg: Callable[[Node], Awaitable[dict[str, object]]],
        get_bwv_stats: Callable[..., Awaitable[dict[str, object]]],
        normalize_bwv_stats: Callable[[dict[str, Any]], dict[str, Any]],
        build_detail_payload: Callable[..., dict[str, object]],
        logger: logging.Logger | None = None,
    ) -> None:
        self.seeker_detail_cache = seeker_detail_cache
        self.summarize_dashboard_node = summarize_dashboard_node
        self.ping_host = ping_host
        self.check_tcp_port = check_tcp_port
        self.get_bwv_cfg = get_bwv_cfg
        self.get_bwv_stats = get_bwv_stats
        self.normalize_bwv_stats = normalize_bwv_stats
        self.build_detail_payload = build_detail_payload
        self.logger = logger or logging.getLogger(__name__)

        self.discovered_probe_ttl_seconds = 300.0
        self.discovered_ping_ttl_seconds = 5.0
        self.discovered_ping_memory_hours = 24
        self.projection_refresh_seconds = 5.0

        self.discovered_node_cache: dict[str, dict[str, object]] = {}
        self.discovered_node_tombstones: dict[str, str] = {}
        self.discovered_ping_cache: dict[str, dict[str, object]] = {}
        self.discovered_ping_inflight: set[str] = set()
        self.discovered_probe_inflight: set[str] = set()
        self.projection_dirty = True
        self.last_projection_refresh_at: datetime | None = None
        self.node_dashboard_cache: dict[str, object] = {
            "anchors": [],
            "discovered": [],
            "cached_at": None,
            "warming": True,
        }

    def get_cached_discovered_node(self, site_id: str) -> dict[str, object] | None:
        cached = self.discovered_node_cache.get(site_id)
        return cached if isinstance(cached, dict) else None

    def ensure_discovered_node_cached(self, db: Session, site_id: str) -> dict[str, object] | None:
        cached = self.get_cached_discovered_node(site_id)
        if cached:
            return cached

        inventory_record = db.get(DiscoveredNode, site_id)
        if inventory_record is None:
            return None

        observation_record = db.get(DiscoveredNodeObservation, site_id)
        return self.merge_cached_discovered_node(
            site_id,
            self._compose_discovered_row(inventory_record, observation_record),
        )

    def merge_cached_discovered_node(self, site_id: str, payload: dict[str, object]) -> dict[str, object]:
        merged = self._normalize_discovered_row({**(self.get_cached_discovered_node(site_id) or {}), **payload})
        self._store_discovered_node_cache(site_id, merged)
        return merged

    def delete_discovered_node(self, db: Session, site_id: str) -> None:
        self.discovered_node_cache.pop(site_id, None)
        self.discovered_node_tombstones[site_id] = datetime.now(timezone.utc).isoformat()
        self.mark_projection_dirty()
        for relationship in db.scalars(
            select(NodeRelationship).where(
                (NodeRelationship.source_site_id == site_id) | (NodeRelationship.target_site_id == site_id)
            )
        ).all():
            db.delete(relationship)
        observation_record = db.get(DiscoveredNodeObservation, site_id)
        if observation_record is not None:
            db.delete(observation_record)
        record = db.get(DiscoveredNode, site_id)
        if record is not None:
            db.delete(record)
        db.commit()

    def _serialize_discovered_inventory_record(self, record: DiscoveredNode) -> dict[str, object]:
        surfaced_by_names: list[str] = []
        if record.surfaced_by_names_json:
            try:
                parsed = json.loads(record.surfaced_by_names_json)
                if isinstance(parsed, list):
                    surfaced_by_names = [str(value).strip() for value in parsed if str(value).strip()]
            except json.JSONDecodeError:
                surfaced_by_names = []

        return {
            "row_type": "discovered",
            "pin_key": self._discovered_pin_key(record.site_id),
            "detail_url": f"/nodes/discovered/{record.site_id}",
            "site_id": record.site_id,
            "site_name": record.site_name or f"Site {record.site_id}",
            "host": record.host or "--",
            "location": record.location or "--",
            "unit": record.unit or "--",
            "version": record.version or "--",
            "discovered_level": record.discovered_level or 2,
            "discovered_parent_site_id": record.discovered_parent_site_id,
            "discovered_parent_name": record.discovered_parent_name,
            "surfaced_by_names": surfaced_by_names,
            "level": record.discovered_level or 2,
            "surfaced_by_site_id": record.discovered_parent_site_id,
            "surfaced_by_name": record.discovered_parent_name,
        }

    def _serialize_discovered_observation_record(self, record: DiscoveredNodeObservation) -> dict[str, object]:
        detail: dict[str, object] = {}
        if record.detail_json:
            try:
                parsed_detail = json.loads(record.detail_json)
                if isinstance(parsed_detail, dict):
                    detail = parsed_detail
            except json.JSONDecodeError:
                detail = {}

        return {
            "latency_ms": record.latency_ms,
            "tx_bps": record.tx_bps or 0,
            "rx_bps": record.rx_bps or 0,
            "tx_display": record.tx_display or "--",
            "rx_display": record.rx_display or "--",
            "web_ok": bool(record.web_ok),
            "ssh_ok": bool(record.ssh_ok),
            "ping": record.ping or "Down",
            "last_seen": record.last_seen.isoformat() if record.last_seen else None,
            "last_ping_up": record.last_ping_up.isoformat() if record.last_ping_up else None,
            "ping_down_since": record.ping_down_since.isoformat() if record.ping_down_since else None,
            "detail": detail,
            "probed_at": record.probed_at.isoformat() if record.probed_at else None,
        }

    def _compose_discovered_row(
        self,
        inventory_record: DiscoveredNode,
        observation_record: DiscoveredNodeObservation | None,
    ) -> dict[str, object]:
        inventory_payload = self._serialize_discovered_inventory_record(inventory_record)
        observation_payload = (
            self._serialize_discovered_observation_record(observation_record)
            if observation_record is not None
            else {}
        )
        return self._normalize_discovered_row({
            **inventory_payload,
            **observation_payload,
        })

    def _serialize_relationship_record(self, record: NodeRelationship) -> dict[str, object]:
        return {
            "source_site_id": record.source_site_id,
            "target_site_id": record.target_site_id,
            "relationship_kind": record.relationship_kind,
            "source_row_type": record.source_row_type,
            "target_row_type": record.target_row_type,
            "source_name": record.source_name,
            "target_name": record.target_name,
            "target_unit": record.target_unit,
            "target_location": record.target_location,
            "discovered_level": record.discovered_level,
        }

    def get_topology_relationships(self, db: Session) -> list[dict[str, object]]:
        return [
            self._serialize_relationship_record(record)
            for record in db.scalars(
                select(NodeRelationship).order_by(
                    NodeRelationship.relationship_kind,
                    NodeRelationship.source_site_id,
                    NodeRelationship.target_site_id,
                )
            ).all()
        ]

    def _upsert_discovered_relationships(self, db: Session, row: dict[str, object]) -> None:
        target_site_id = str(row.get("site_id") or "").strip()
        source_site_id = str(row.get("discovered_parent_site_id") or row.get("surfaced_by_site_id") or "").strip()
        relationship_kind = "surfaced_by"

        existing_relationships = db.scalars(
            select(NodeRelationship).where(
                (NodeRelationship.target_site_id == target_site_id)
                & (NodeRelationship.relationship_kind == relationship_kind)
            )
        ).all()

        for relationship in existing_relationships:
            if not source_site_id or relationship.source_site_id != source_site_id:
                db.delete(relationship)

        if not target_site_id or not source_site_id:
            return

        record = db.get(NodeRelationship, {
            "source_site_id": source_site_id,
            "target_site_id": target_site_id,
            "relationship_kind": relationship_kind,
        })
        if record is None:
            record = NodeRelationship(
                source_site_id=source_site_id,
                target_site_id=target_site_id,
                relationship_kind=relationship_kind,
            )
            db.add(record)

        record.source_row_type = str(row.get("source_row_type") or "anchor").strip() or "anchor"
        record.target_row_type = "discovered"
        record.source_name = str(row.get("discovered_parent_name") or row.get("surfaced_by_name") or "").strip() or None
        record.target_name = str(row.get("site_name") or "").strip() or None
        record.target_unit = str(row.get("unit") or "").strip() or None
        record.target_location = str(row.get("location") or "").strip() or None
        record.discovered_level = int(row.get("discovered_level") or row.get("level") or 2)

    def _upsert_discovered_record(self, db: Session, row: dict[str, object]) -> None:
        site_id = str(row.get("site_id") or "").strip()
        if not site_id:
            return

        self._upsert_discovered_inventory_record(db, row)
        self._upsert_discovered_observation_record(db, row)
        self._upsert_discovered_relationships(db, row)

    def _upsert_discovered_inventory_record(self, db: Session, row: dict[str, object]) -> None:
        site_id = str(row.get("site_id") or "").strip()
        if not site_id:
            return

        record = db.get(DiscoveredNode, site_id)
        if record is None:
            record = DiscoveredNode(site_id=site_id)
            db.add(record)

        record.site_name = str(row.get("site_name") or "").strip() or None
        record.host = str(row.get("host") or "").strip() or None
        record.location = str(row.get("location") or "").strip() or None
        record.unit = str(row.get("unit") or "").strip() or None
        record.version = str(row.get("version") or "").strip() or None
        record.discovered_level = int(row.get("discovered_level") or row.get("level") or 2)
        record.discovered_parent_site_id = str(row.get("discovered_parent_site_id") or row.get("surfaced_by_site_id") or "").strip() or None
        record.discovered_parent_name = str(row.get("discovered_parent_name") or row.get("surfaced_by_name") or "").strip() or None
        record.surfaced_by_names_json = json.dumps(self._merge_discovered_sources(row.get("surfaced_by_names")))

    def _upsert_discovered_observation_record(self, db: Session, row: dict[str, object]) -> None:
        site_id = str(row.get("site_id") or "").strip()
        if not site_id:
            return

        record = db.get(DiscoveredNodeObservation, site_id)
        if record is None:
            record = DiscoveredNodeObservation(site_id=site_id)
            db.add(record)

        detail = row.get("detail")
        detail_json = None
        if isinstance(detail, dict):
            detail_json = json.dumps(detail)

        record.latency_ms = int(row["latency_ms"]) if isinstance(row.get("latency_ms"), int) else None
        record.tx_bps = int(row["tx_bps"]) if isinstance(row.get("tx_bps"), int) else 0
        record.rx_bps = int(row["rx_bps"]) if isinstance(row.get("rx_bps"), int) else 0
        record.tx_display = str(row.get("tx_display") or "").strip() or None
        record.rx_display = str(row.get("rx_display") or "").strip() or None
        record.web_ok = bool(row.get("web_ok"))
        record.ssh_ok = bool(row.get("ssh_ok"))
        record.ping = str(row.get("ping") or "").strip() or None
        record.last_seen = self._safe_parse_iso(row.get("last_seen"))
        record.last_ping_up = self._safe_parse_iso(row.get("last_ping_up"))
        record.ping_down_since = self._safe_parse_iso(row.get("ping_down_since"))
        record.probed_at = self._safe_parse_iso(row.get("probed_at"))
        record.detail_json = detail_json

    def find_source_node_for_discovered_detail(self, cached: dict[str, object], nodes: list[Node]) -> Node | None:
        parent_site_id = str(cached.get("discovered_parent_site_id") or cached.get("surfaced_by_site_id") or "").strip()

        for node in nodes:
            node_detail = self.seeker_detail_cache.get(node.id) or {}
            config_summary = node_detail.get("config_summary") if isinstance(node_detail.get("config_summary"), dict) else {}
            node_site_id = str(config_summary.get("site_id") or "").strip()
            if parent_site_id and node_site_id == parent_site_id:
                return node

        return next((node for node in nodes if node.api_username and node.api_password), None)

    def get_cached_payload(self) -> dict[str, list[dict[str, object]]]:
        return {
            "anchors": list(self.node_dashboard_cache.get("anchors") or []),
            "discovered": list(self.node_dashboard_cache.get("discovered") or []),
        }

    def get_serialized_cache(self) -> dict[str, object]:
        return {
            "anchors": list(self.node_dashboard_cache.get("anchors") or []),
            "discovered": list(self.node_dashboard_cache.get("discovered") or []),
            "cached_at": self.node_dashboard_cache.get("cached_at"),
            "warming": bool(self.node_dashboard_cache.get("warming")),
        }

    async def refresh_cache(self, db: Session, nodes: list[Node]) -> None:
        await self.refresh_discovered_inventory(db, nodes)
        if not self.should_refresh_projection():
            self.node_dashboard_cache["warming"] = False
            return
        payload = await self.build_projection(db, nodes)
        now = datetime.now(timezone.utc)
        self.node_dashboard_cache.clear()
        self.node_dashboard_cache.update({
            **payload,
            "cached_at": now.isoformat(),
            "warming": False,
        })
        self.last_projection_refresh_at = now
        self.projection_dirty = False

    def mark_cache_refresh_failed(self) -> None:
        self.node_dashboard_cache.update({
            **self.node_dashboard_cache,
            "warming": False,
        })

    def mark_projection_dirty(self) -> None:
        self.projection_dirty = True

    def should_refresh_projection(self) -> bool:
        if bool(self.node_dashboard_cache.get("warming")):
            return True
        if self.projection_dirty:
            return True
        if self.last_projection_refresh_at is None:
            return True
        return (datetime.now(timezone.utc) - self.last_projection_refresh_at).total_seconds() >= self.projection_refresh_seconds

    @staticmethod
    def _safe_parse_iso(value: object) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @staticmethod
    def _discovered_pin_key(site_id: str) -> str:
        return f"discovered:{site_id}"

    @staticmethod
    def _anchor_pin_key(node_id: int) -> str:
        return f"anchor:{node_id}"

    def _is_generic_site_name(self, value: object, site_id: object = None) -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        if site_id is not None and text.lower() == f"site {site_id}".lower():
            return True
        return bool(re.fullmatch(r"site\s+\S+", text, re.IGNORECASE))

    def _prefer_discovered_site_name(self, current_value: object, candidate_value: object, site_id: object) -> str:
        current = str(current_value or "").strip()
        candidate = str(candidate_value or "").strip()
        if not current:
            return candidate
        if not candidate:
            return current
        if self._is_generic_site_name(current, site_id) and not self._is_generic_site_name(candidate, site_id):
            return candidate
        return current

    @staticmethod
    def _merge_discovered_sources(*values: object) -> list[str]:
        merged: list[str] = []
        for value in values:
            if isinstance(value, list):
                items = value
            elif value:
                items = [value]
            else:
                items = []
            for item in items:
                text = str(item or "").strip()
                if text and text not in merged:
                    merged.append(text)
        return merged

    @staticmethod
    def _normalize_discovered_row(row: dict[str, object]) -> dict[str, object]:
        sanitized_row = dict(row)
        sanitized_row.pop("source_row_type", None)
        sanitized_row.pop("surfaced_by_sources", None)
        return NodeDashboardDiscoveredRow.model_validate(sanitized_row).model_dump()

    def _store_discovered_node_cache(self, site_id: str, row: dict[str, object]) -> bool:
        normalized = self._normalize_discovered_row(row)
        previous = self.get_cached_discovered_node(site_id)
        changed = previous != normalized
        self.discovered_node_cache[site_id] = normalized
        if changed:
            self.mark_projection_dirty()
        return changed

    def _update_discovered_ping_timestamps(self, entry: dict[str, object], *, ping_up: bool, observed_at: datetime) -> None:
        if ping_up:
            entry["last_seen"] = observed_at.isoformat()
            entry["last_ping_up"] = observed_at.isoformat()
            entry["ping_down_since"] = None
        elif entry.get("last_ping_up") and not entry.get("ping_down_since"):
            entry["ping_down_since"] = observed_at.isoformat()

    def _should_keep_discovered(self, entry: dict[str, object]) -> bool:
        if str(entry.get("ping") or "").strip().lower() == "up":
            return True
        last_ping_up = self._safe_parse_iso(entry.get("last_ping_up"))
        if last_ping_up is None:
            return False
        down_since = self._safe_parse_iso(entry.get("ping_down_since"))
        if down_since is None:
            return last_ping_up >= datetime.now(timezone.utc) - timedelta(hours=self.discovered_ping_memory_hours)
        return down_since >= datetime.now(timezone.utc) - timedelta(hours=self.discovered_ping_memory_hours)

    @staticmethod
    def _format_dashboard_rate(value: object) -> str:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            text = str(value or "").strip()
            return text or "--"

        if numeric <= 0:
            return "0 bps"
        if numeric >= 1_000_000:
            return f"{numeric / 1_000_000:.1f} Mbps"
        if numeric >= 1_000:
            return f"{numeric / 1_000:.1f} Kbps"
        if numeric.is_integer():
            return f"{int(numeric)} bps"
        return f"{numeric:.0f} bps"

    def _schedule_discovered_ping_refresh(self, site_id: str, host: str) -> None:
        normalized_host = str(host or "").strip()
        if not normalized_host or site_id in self.discovered_ping_inflight:
            return

        self.discovered_ping_inflight.add(site_id)

        async def _runner() -> None:
            checked_at = datetime.now(timezone.utc)
            try:
                result = await asyncio.to_thread(self.ping_host, normalized_host)
                self.discovered_ping_cache[site_id] = {
                    "host": normalized_host,
                    "reachable": bool(result.get("reachable")),
                    "latency_ms": result.get("latency_ms"),
                    "checked_at": checked_at.isoformat(),
                }
            except Exception:
                self.logger.exception(
                    "Background discovered ping refresh failed for site_id=%s host=%s",
                    site_id,
                    normalized_host,
                )
            finally:
                self.discovered_ping_inflight.discard(site_id)

        asyncio.create_task(_runner())

    async def get_discovered_ping_snapshot(self, site_id: str, host: str) -> dict[str, int | bool | None | str]:
        now = datetime.now(timezone.utc)
        cached = self.discovered_ping_cache.get(site_id)
        cached_at = self._safe_parse_iso(cached.get("checked_at")) if isinstance(cached, dict) else None
        normalized_host = str(host or "").strip()
        cached_host = str(cached.get("host") or "").strip() if isinstance(cached, dict) else ""

        if (
            cached
            and cached_at
            and (now - cached_at).total_seconds() < self.discovered_ping_ttl_seconds
            and cached_host == normalized_host
        ):
            return {
                "reachable": bool(cached.get("reachable")),
                "latency_ms": cached.get("latency_ms") if cached.get("latency_ms") is not None else None,
                "checked_at": cached.get("checked_at"),
            }

        self._schedule_discovered_ping_refresh(site_id, normalized_host)

        if cached and cached_host == normalized_host:
            return {
                "reachable": bool(cached.get("reachable")),
                "latency_ms": cached.get("latency_ms") if cached.get("latency_ms") is not None else None,
                "checked_at": cached.get("checked_at"),
            }

        return {
            "reachable": False,
            "latency_ms": None,
            "checked_at": None,
        }

    async def probe_discovered_node_detail(
        self,
        source_node: Node,
        *,
        site_id: str,
        site_ip: str,
        level: int,
        surfaced_by_site_id: str | None,
        surfaced_by_name: str | None,
    ) -> dict[str, object] | None:
        if not source_node.api_username or not source_node.api_password or not site_ip or site_ip == "--":
            return None

        now = datetime.now(timezone.utc)
        cached = self.discovered_node_cache.get(site_id)
        cached_at = self._safe_parse_iso(cached.get("probed_at")) if isinstance(cached, dict) else None
        if cached and cached_at and (now - cached_at).total_seconds() < self.discovered_probe_ttl_seconds:
            updated_cached = {
                **cached,
                "level": level,
                "surfaced_by_site_id": surfaced_by_site_id,
                "surfaced_by_name": surfaced_by_name,
            }
            return self.merge_cached_discovered_node(site_id, updated_cached)

        ping_result = self.ping_host(site_ip)
        web_check, ssh_check = await asyncio.gather(
            asyncio.to_thread(self.check_tcp_port, site_ip, source_node.web_port),
            asyncio.to_thread(self.check_tcp_port, site_ip, source_node.ssh_port),
        )
        health = {
            "status": "online" if web_check["reachable"] and ssh_check["reachable"] else "degraded" if web_check["reachable"] or ssh_check["reachable"] else "offline",
            "latency_ms": ping_result["latency_ms"] if ping_result["reachable"] else None,
            "last_checked": now,
            "web_ok": bool(web_check["reachable"]),
            "ssh_ok": bool(ssh_check["reachable"]),
            "ping_ok": bool(ping_result["reachable"]),
            "ping_state": "good" if ping_result["reachable"] else "down",
            "ping_avg_ms": ping_result["latency_ms"] if ping_result["reachable"] else None,
        }

        transient_node = Node(
            id=-1,
            name=site_id or site_ip,
            host=site_ip,
            web_port=source_node.web_port,
            ssh_port=source_node.ssh_port,
            location=source_node.location,
            include_in_topology=False,
            topology_level=None,
            topology_unit=source_node.topology_unit,
            enabled=True,
            notes=None,
            api_username=source_node.api_username,
            api_password=source_node.api_password,
            api_use_https=source_node.api_use_https,
            last_checked=now,
            latency_ms=health["latency_ms"],
        )

        cfg_result, stats_result, learnt_routes_result = await asyncio.gather(
            self.get_bwv_cfg(transient_node),
            self.get_bwv_stats(transient_node),
            self.get_bwv_stats(transient_node, extra_args={"learntRoutes": "1"}),
        )

        detail = self.build_detail_payload(
            transient_node,
            node_health=health,
            cfg_result=cfg_result,
            stats_result=stats_result,
            learnt_routes_result=learnt_routes_result,
        )

        config_summary = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
        normalized = self.normalize_bwv_stats(detail.get("raw", {}).get("bwv_stats", {}) or {})
        payload = {
            "site_id": str(config_summary.get("site_id") or site_id or "--"),
            "site_name": str(config_summary.get("site_name") or f"Site {site_id}" or site_ip),
            "host": site_ip,
            "location": transient_node.location,
            "unit": transient_node.topology_unit or "--",
            "version": str(config_summary.get("version") or "--"),
            "latency_ms": health["latency_ms"],
            "tx_bps": normalized.get("tx_bps", 0),
            "rx_bps": normalized.get("rx_bps", 0),
            "tx_display": self._format_dashboard_rate(normalized.get("tx_bps", 0)),
            "rx_display": self._format_dashboard_rate(normalized.get("rx_bps", 0)),
            "web_ok": health["web_ok"],
            "ssh_ok": health["ssh_ok"],
            "ping": "Up" if health["ping_ok"] else "Down",
            "last_seen": now.isoformat(),
            "last_ping_up": now.isoformat() if health["ping_ok"] else (cached.get("last_ping_up") if isinstance(cached, dict) else None),
            "ping_down_since": None if health["ping_ok"] else (cached.get("ping_down_since") if isinstance(cached, dict) else now.isoformat()),
            "level": level,
            "surfaced_by_site_id": surfaced_by_site_id,
            "surfaced_by_name": surfaced_by_name,
            "surfaced_by_names": self._merge_discovered_sources(
                cached.get("surfaced_by_names") if isinstance(cached, dict) else None,
                surfaced_by_name,
            ),
            "detail": detail,
            "probed_at": now.isoformat(),
        }
        return self.merge_cached_discovered_node(payload["site_id"], payload)

    def _schedule_discovered_node_probe(
        self,
        source_node: Node,
        *,
        site_id: str,
        site_ip: str,
        level: int,
        surfaced_by_site_id: str | None,
        surfaced_by_name: str | None,
    ) -> None:
        if not source_node.api_username or not source_node.api_password or not site_ip or site_ip == "--":
            return
        if site_id in self.discovered_probe_inflight:
            return

        cached = self.discovered_node_cache.get(site_id)
        cached_at = self._safe_parse_iso(cached.get("probed_at")) if isinstance(cached, dict) else None
        now = datetime.now(timezone.utc)
        if cached and cached_at and (now - cached_at).total_seconds() < self.discovered_probe_ttl_seconds:
            return

        self.discovered_probe_inflight.add(site_id)

        async def _runner() -> None:
            try:
                await self.probe_discovered_node_detail(
                    source_node,
                    site_id=site_id,
                    site_ip=site_ip,
                    level=level,
                    surfaced_by_site_id=surfaced_by_site_id,
                    surfaced_by_name=surfaced_by_name,
                )
            except Exception:
                self.logger.exception(
                    "Background discovered node probe failed for site_id=%s host=%s",
                    site_id,
                    site_ip,
                )
            finally:
                self.discovered_probe_inflight.discard(site_id)

        asyncio.create_task(_runner())

    async def refresh_discovered_inventory(self, db: Session, nodes: list[Node]) -> None:
        await refresh_discovered_inventory(self, db, nodes)

    async def build_projection(self, db: Session, nodes: list[Node]) -> dict[str, list[dict[str, object]]]:
        return await build_projection(self, db, nodes)

    async def _build_anchor_records(self, nodes: list[Node]) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
        return await build_anchor_records(self, nodes)

    async def build_payload(self, db: Session, nodes: list[Node]) -> dict[str, list[dict[str, object]]]:
        return await self.build_projection(db, nodes)




