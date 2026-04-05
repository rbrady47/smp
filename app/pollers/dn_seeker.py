"""DN Seeker poller — probes discovered node Seeker APIs using anchor credentials."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.db import SessionLocal
from app.models import DiscoveredNode, Node

if TYPE_CHECKING:
    from app.poller_state import PollerState

logger = logging.getLogger(__name__)

DN_SEEKER_POLL_INTERVAL_SECONDS = 5.0


async def dn_seeker_polling_loop(ps: PollerState) -> None:
    """Background loop that periodically probes DN Seeker APIs.

    Uses credentials inherited from the owning anchor node (source_anchor_node_id).
    Polls both DB-persisted DNs and in-memory cached DNs so that DNs discovered
    via tunnel analysis (but not yet persisted by a submap view) still get probed.
    """
    from app.pollers.dashboard import probe_discovered_node_detail

    await asyncio.sleep(10.0)  # initial delay to let AN polling populate first
    while True:
        try:
            db = SessionLocal()
            try:
                dns = db.scalars(
                    select(DiscoveredNode).where(
                        DiscoveredNode.source_anchor_node_id.isnot(None),
                        DiscoveredNode.host.isnot(None),
                    )
                ).all()
                anchor_ids = {dn.source_anchor_node_id for dn in dns if dn.source_anchor_node_id}

                for _sid, cached_row in ps.dashboard_backend.discovered_node_cache.items():
                    if isinstance(cached_row, dict) and cached_row.get("source_anchor_id"):
                        try:
                            anchor_ids.add(int(cached_row["source_anchor_id"]))
                        except (ValueError, TypeError):
                            pass

                anchors_by_id: dict[int, Node] = {}
                if anchor_ids:
                    anchor_nodes = db.scalars(select(Node).where(Node.id.in_(anchor_ids))).all()
                    anchors_by_id = {n.id: n for n in anchor_nodes}
            finally:
                db.close()

            probe_targets: dict[str, tuple] = {}
            for dn in dns:
                source_node = anchors_by_id.get(dn.source_anchor_node_id) if dn.source_anchor_node_id else None
                if not source_node or not source_node.api_username or not source_node.api_password:
                    continue
                host = str(dn.host or "").strip()
                if not host or host == "--":
                    continue
                probe_targets[dn.site_id] = (
                    source_node, host,
                    dn.discovered_level or 2,
                    dn.discovered_parent_site_id,
                    dn.discovered_parent_name,
                )

            for cached_sid, cached_row in ps.dashboard_backend.discovered_node_cache.items():
                if cached_sid in probe_targets:
                    continue
                if not isinstance(cached_row, dict):
                    continue
                host = str(cached_row.get("host") or "").strip()
                if not host or host == "--":
                    continue
                anchor_id = None
                try:
                    anchor_id = int(cached_row.get("source_anchor_id") or 0) or None
                except (ValueError, TypeError):
                    pass
                if not anchor_id:
                    continue
                source_node = anchors_by_id.get(anchor_id)
                if not source_node or not source_node.api_username or not source_node.api_password:
                    continue
                probe_targets[cached_sid] = (
                    source_node, host,
                    int(cached_row.get("level") or 2),
                    str(cached_row.get("surfaced_by_site_id") or "") or None,
                    str(cached_row.get("surfaced_by_name") or "") or None,
                )

            tasks = []
            for site_id, (source_node, host, level, parent_sid, parent_name) in probe_targets.items():
                tasks.append(
                    probe_discovered_node_detail(
                        ps,
                        source_node,
                        site_id=site_id,
                        site_ip=host,
                        level=level,
                        surfaced_by_site_id=parent_sid,
                        surfaced_by_name=parent_name,
                    )
                )

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            logger.exception("DN Seeker polling loop iteration failed")

        await asyncio.sleep(DN_SEEKER_POLL_INTERVAL_SECONDS)
