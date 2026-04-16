"""Discovery API routes — /api/discovered-nodes, submap discovery."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import DiscoveredNode, DiscoveredNodeObservation, Node, OperationalMapObject, OperationalMapView
from app.node_discovery_service import _tunnel_row_is_eligible, _tunnel_row_exists
from app.schemas import DnPromoteRequest
from app import state_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/discovered-nodes/{site_id}/detail")
async def discovered_node_detail(
    site_id: str,
    window_seconds: int = Query(default=60),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    from app.main import (
        apply_windowed_detail_summary,
        node_dashboard_backend,
        normalize_node_dashboard_window,
        probe_discovered_node_detail,
    )
    cached = await node_dashboard_backend.ensure_discovered_node_cached(db, site_id)

    if not cached:
        raise HTTPException(status_code=404, detail="Discovered node not found")

    detail = cached.get("detail") if isinstance(cached.get("detail"), dict) else {}
    if detail:
        return apply_windowed_detail_summary(
            dict(detail),
            window_metrics=node_dashboard_backend.get_row_window_metrics(
                "discovered",
                site_id,
                normalize_node_dashboard_window(window_seconds),
            ),
        )

    site_ip = str(cached.get("host") or "").strip()
    if not site_ip or site_ip == "--":
        raise HTTPException(status_code=404, detail="Discovered node detail not available")

    parent_site_id = str(cached.get("discovered_parent_site_id") or cached.get("surfaced_by_site_id") or "").strip()
    nodes = (await db.scalars(select(Node).order_by(Node.name))).all()
    source_node = node_dashboard_backend.find_source_node_for_discovered_detail(cached, nodes)
    if source_node is None:
        raise HTTPException(status_code=404, detail="Discovered node detail not available")

    probed = await probe_discovered_node_detail(
        source_node,
        site_id=str(site_id),
        site_ip=site_ip,
        level=int(cached.get("discovered_level") or 2),
        surfaced_by_site_id=parent_site_id or None,
        surfaced_by_name=str(cached.get("discovered_parent_name") or "").strip() or None,
    )
    if not probed:
        raise HTTPException(status_code=404, detail="Discovered node detail not available")

    detail = probed.get("detail") if isinstance(probed.get("detail"), dict) else {}
    if not detail:
        raise HTTPException(status_code=404, detail="Discovered node detail not available")
    return apply_windowed_detail_summary(
        dict(detail),
        window_metrics=node_dashboard_backend.get_row_window_metrics(
            "discovered",
            site_id,
            normalize_node_dashboard_window(window_seconds),
        ),
    )


@router.delete("/discovered-nodes/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_discovered_node(site_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    from app.main import node_dashboard_backend
    await node_dashboard_backend.delete_discovered_node(db, site_id)
    await state_manager.publish_discovery_event("dn_removed", site_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/discovered-nodes/{site_id}/promote")
async def promote_discovered_node(
    site_id: str,
    payload: DnPromoteRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Promote a Discovered Node to an Anchor Node.

    Creates a new Node record from the DN data + operator-supplied
    credentials, then deletes the DN and all related records.
    """
    from app.main import node_dashboard_backend, serialize_node

    # Verify DN exists
    dn = await db.get(DiscoveredNode, site_id)
    if not dn:
        raise HTTPException(status_code=404, detail="Discovered node not found")

    # Prevent duplicate promotion — check if AN with same node_id already exists
    existing_an = (await db.scalars(
        select(Node).where(Node.node_id == site_id)
    )).first()
    if existing_an:
        raise HTTPException(
            status_code=409,
            detail=f"An anchor node with node_id '{site_id}' already exists (id={existing_an.id})",
        )

    # Build the new Anchor Node from DN data + payload overrides
    node = Node(
        name=payload.name or dn.site_name or f"Site {site_id}",
        node_id=site_id,
        host=payload.host or dn.host or "",
        web_port=payload.web_port,
        ssh_port=payload.ssh_port,
        location=payload.location or dn.location or "--",
        topology_map_id=payload.topology_map_id,
        enabled=True,
        notes=payload.notes,
        api_username=payload.api_username,
        api_password=payload.api_password,
        api_use_https=payload.api_use_https,
        ping_enabled=payload.ping_enabled,
        ping_interval_seconds=payload.ping_interval_seconds,
        charts_enabled=payload.charts_enabled,
    )
    db.add(node)
    await db.flush()  # get node.id before deleting DN

    new_node_id = node.id
    logger.info(
        "Promoting DN %s (%s) to AN id=%d",
        site_id, node.name, new_node_id,
    )

    # Delete the DN and related records
    await node_dashboard_backend.delete_discovered_node(db, site_id)

    # Commit the new AN (DN deletion already committed inside delete_discovered_node)
    await db.commit()
    await db.refresh(node)

    # Publish events
    await state_manager.publish_discovery_event("dn_removed", site_id)
    await state_manager.publish_topology_change("node_created", id=new_node_id)

    pending_health = {
        "status": "unknown",
        "latency_ms": None,
        "last_checked": None,
        "web_ok": False,
        "ssh_ok": False,
        "ping_ok": False,
        "ping_state": "unknown",
        "ping_avg_ms": None,
    }
    return {
        "status": "ok",
        "promoted_site_id": site_id,
        "node": serialize_node(node, pending_health),
    }


@router.post("/discovered-nodes/flush-unreachable")
async def flush_unreachable_discovered_nodes(
    window_seconds: int = Query(default=60),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    from app.main import node_dashboard_backend, normalize_node_dashboard_window
    payload = node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))
    unreachable_site_ids = [
        str(row.get("site_id", "")).strip()
        for row in payload.get("discovered", [])
        if str(row.get("site_id", "")).strip()
        and str(row.get("ping") or "").strip().lower() != "up"
    ]
    deleted_site_ids = await node_dashboard_backend.delete_discovered_nodes(db, unreachable_site_ids)
    for sid in deleted_site_ids:
        await state_manager.publish_discovery_event("dn_removed", sid)
    return {
        "deleted_site_ids": deleted_site_ids,
        "deleted_count": len(deleted_site_ids),
    }


@router.post("/discovered-nodes/flush-discovery")
async def flush_discovery(
    window_seconds: int = Query(default=60),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    from app.main import node_dashboard_backend, normalize_node_dashboard_window
    from app.node_discovery_service import refresh_discovered_inventory
    existing_payload = node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))
    previous_site_ids = [
        str(row.get("site_id") or "").strip()
        for row in existing_payload.get("discovered", [])
        if str(row.get("site_id") or "").strip()
    ]
    await node_dashboard_backend.clear_discovery(db)
    nodes = (await db.scalars(select(Node).order_by(Node.name))).all()
    await refresh_discovered_inventory(node_dashboard_backend, db, nodes)
    refreshed_payload = node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))
    refreshed_site_ids = [
        str(row.get("site_id") or "").strip()
        for row in refreshed_payload.get("discovered", [])
        if str(row.get("site_id") or "").strip()
    ]
    return {
        "deleted_site_ids": previous_site_ids,
        "deleted_count": len(previous_site_ids),
        "rediscovered_site_ids": refreshed_site_ids,
        "rediscovered_count": len(refreshed_site_ids),
    }


def _resolve_dn_owner_anchor_id(
    candidate_anchor_id: int,
    existing_anchor_id: int | None,
    anchor_site_id_map: dict[int, str],
) -> int:
    """Ownership: AN with lowest site_id wins AN-vs-AN conflicts."""
    if existing_anchor_id is None:
        return candidate_anchor_id
    candidate_sid = anchor_site_id_map.get(candidate_anchor_id, str(candidate_anchor_id))
    existing_sid = anchor_site_id_map.get(existing_anchor_id, str(existing_anchor_id))
    return candidate_anchor_id if candidate_sid < existing_sid else existing_anchor_id


@router.get("/topology/maps/{map_view_id}/discovery")
async def get_submap_discovery(
    map_view_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Return tunnel peers for anchor nodes placed on this submap."""
    from app.main import dn_ping_snapshots, node_dashboard_backend, seeker_detail_cache

    map_view = await db.get(OperationalMapView, map_view_id)
    if not map_view:
        raise HTTPException(status_code=404, detail="Map view not found")

    placed_objects = (await db.scalars(
        select(OperationalMapObject).where(
            OperationalMapObject.map_view_id == map_view_id,
            OperationalMapObject.object_type == "node",
        )
    )).all()

    placed_anchor_ids: set[int] = set()
    for obj in placed_objects:
        binding_key = obj.binding_key or ""
        if binding_key.startswith("anchor:"):
            try:
                placed_anchor_ids.add(int(binding_key.split(":")[1]))
            except (ValueError, IndexError):
                pass

    # Also include ANs assigned to this submap via topology_map_id
    map_assigned_nodes = (await db.scalars(
        select(Node).where(Node.topology_map_id == map_view_id)
    )).all()
    for node in map_assigned_nodes:
        placed_anchor_ids.add(node.id)

    anchor_nodes = (await db.scalars(
        select(Node).where(Node.id.in_(placed_anchor_ids))
    )).all() if placed_anchor_ids else []

    # Pre-load all seeker details into a local dict for O(1) lookups
    all_seeker_details: dict[int, dict] = {
        node_id: seeker_detail_cache.get(node_id) or {}
        for node_id in seeker_detail_cache
    }

    anchor_site_id_map: dict[int, str] = {}
    for node in anchor_nodes:
        detail = all_seeker_details.get(node.id) or {}
        cfg = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
        anchor_site_id_map[node.id] = str(cfg.get("site_id") or node.node_id or node.id).strip()

    all_inventory_nodes = (await db.scalars(select(Node))).all()
    inventory_site_ids: set[str] = set()
    inventory_hosts: set[str] = set()
    for inv_node in all_inventory_nodes:
        if inv_node.node_id:
            inventory_site_ids.add(inv_node.node_id)
            inventory_site_ids.add(inv_node.node_id.lower())
        inventory_site_ids.add(str(inv_node.id))
        if inv_node.host:
            inventory_hosts.add(str(inv_node.host).strip().lower())
        inv_detail = all_seeker_details.get(inv_node.id) or {}
        inv_config = inv_detail.get("config_summary") if isinstance(inv_detail.get("config_summary"), dict) else {}
        cfg_site_id = str(inv_config.get("site_id") or "").strip()
        if cfg_site_id:
            inventory_site_ids.add(cfg_site_id)

    for inv_node in all_inventory_nodes:
        inv_detail = all_seeker_details.get(inv_node.id) or {}
        for tun in (inv_detail.get("tunnels") or []):
            if not isinstance(tun, dict):
                continue
            mate_ip = str(tun.get("mate_ip") or "").strip().lower()
            mate_sid = str(tun.get("mate_site_id") or "").strip()
            if mate_ip and mate_ip in inventory_hosts and mate_sid:
                inventory_site_ids.add(mate_sid)

    discovered_peers: list[dict[str, object]] = []
    seen_site_ids: dict[str, int] = {}
    discovery_links: list[dict[str, object]] = []

    known_dns = (await db.scalars(
        select(DiscoveredNode).where(
            DiscoveredNode.map_view_id == map_view_id,
            DiscoveredNode.source_anchor_node_id.isnot(None),
        )
    )).all()
    for kdn in known_dns:
        if kdn.site_id not in seen_site_ids and kdn.source_anchor_node_id:
            seen_site_ids[kdn.site_id] = kdn.source_anchor_node_id

    for node in anchor_nodes:
        detail = all_seeker_details.get(node.id) or {}
        config_summary = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
        source_site_id = str(config_summary.get("site_id") or node.node_id or "").strip() or str(node.id)
        source_name = str(config_summary.get("site_name") or node.name or "").strip() or node.name
        tunnels = [row for row in (detail.get("tunnels") or []) if isinstance(row, dict)]

        for row in tunnels:
            if not _tunnel_row_exists(row):
                continue
            mate_site_id = str(row.get("mate_site_id") or "").strip()
            if not mate_site_id or mate_site_id in inventory_site_ids or mate_site_id.lower() in inventory_site_ids:
                continue
            mate_ip = str(row.get("mate_ip") or "").strip()
            mate_name = str(row.get("site_name") or row.get("mate_site_name") or "").strip() or mate_site_id
            if not mate_ip or mate_ip == "--":
                continue
            ping_status = str(row.get("ping") or "").strip()
            tx_rate = str(row.get("tx_rate") or "").strip()
            rx_rate = str(row.get("rx_rate") or "").strip()
            is_up = _tunnel_row_is_eligible(row)

            peer_in_list = any(p["site_id"] == mate_site_id for p in discovered_peers)
            if mate_site_id not in seen_site_ids:
                if not is_up:
                    continue
                seen_site_ids[mate_site_id] = node.id
                discovered_peers.append({
                    "site_id": mate_site_id,
                    "name": mate_name,
                    "host": mate_ip,
                    "source_anchor_id": node.id,
                    "source_site_id": source_site_id,
                    "source_name": source_name,
                    "ping": ping_status,
                    "tx_rate": tx_rate,
                    "rx_rate": rx_rate,
                })
            elif not peer_in_list:
                discovered_peers.append({
                    "site_id": mate_site_id,
                    "name": mate_name,
                    "host": mate_ip,
                    "source_anchor_id": seen_site_ids[mate_site_id],
                    "source_site_id": source_site_id,
                    "source_name": source_name,
                    "ping": ping_status,
                    "tx_rate": tx_rate,
                    "rx_rate": rx_rate,
                })
            else:
                new_owner = _resolve_dn_owner_anchor_id(
                    node.id, seen_site_ids[mate_site_id], anchor_site_id_map,
                )
                if new_owner != seen_site_ids[mate_site_id]:
                    seen_site_ids[mate_site_id] = new_owner
                    for peer in discovered_peers:
                        if peer["site_id"] == mate_site_id:
                            peer["source_anchor_id"] = new_owner
                            peer["source_site_id"] = source_site_id
                            peer["source_name"] = source_name
                            break

            discovery_links.append({
                "source_anchor_id": node.id,
                "target_site_id": mate_site_id,
                "status": "healthy" if ping_status.lower() == "up" else "down",
                "tx_rate": tx_rate,
                "rx_rate": rx_rate,
            })

    # --- Second-hop: DN-to-DN discovery from cached DN tunnel data ---
    # Pre-load all DN cache entries for O(1) lookups
    all_dn_cache: dict[str, dict] = {
        site_id: node_dashboard_backend.get_cached_discovered_node(site_id)
        for site_id in node_dashboard_backend.discovered_node_cache
    }
    first_hop_site_ids = set(seen_site_ids.keys())
    for first_hop_peer in list(discovered_peers):
        fh_site_id = str(first_hop_peer["site_id"])
        cached_dn = all_dn_cache.get(fh_site_id)
        if not cached_dn:
            logger.debug("DN-to-DN: no cache for %s", fh_site_id)
            continue
        dn_detail = cached_dn.get("detail") if isinstance(cached_dn.get("detail"), dict) else {}
        dn_tunnels = [row for row in (dn_detail.get("tunnels") or []) if isinstance(row, dict)]
        if not dn_tunnels:
            logger.debug("DN-to-DN: no tunnels for %s (has_detail=%s)", fh_site_id, bool(dn_detail))
            continue
        logger.debug("DN-to-DN: %s has %d tunnels", fh_site_id, len(dn_tunnels))

        for row in dn_tunnels:
            if not _tunnel_row_exists(row):
                continue
            mate_sid_dbg = str(row.get("mate_site_id") or "").strip()
            is_inventory = mate_sid_dbg in inventory_site_ids or mate_sid_dbg.lower() in inventory_site_ids
            if not mate_sid_dbg or is_inventory:
                continue
            mate_site_id = mate_sid_dbg
            mate_ip = str(row.get("mate_ip") or "").strip()
            mate_name = str(row.get("site_name") or row.get("mate_site_name") or "").strip() or mate_site_id
            if not mate_ip or mate_ip == "--":
                continue
            ping_status = str(row.get("ping") or "").strip()
            tx_rate = str(row.get("tx_rate") or "").strip()
            rx_rate = str(row.get("rx_rate") or "").strip()
            is_up = _tunnel_row_is_eligible(row)

            if mate_site_id not in seen_site_ids:
                if not is_up:
                    continue
                owner_anchor_id = seen_site_ids.get(fh_site_id)
                if owner_anchor_id is not None:
                    seen_site_ids[mate_site_id] = owner_anchor_id
                    discovered_peers.append({
                        "site_id": mate_site_id,
                        "name": mate_name,
                        "host": mate_ip,
                        "source_anchor_id": owner_anchor_id,
                        "source_site_id": fh_site_id,
                        "source_name": str(first_hop_peer.get("name") or fh_site_id),
                        "ping": ping_status,
                        "tx_rate": tx_rate,
                        "rx_rate": rx_rate,
                    })

            if mate_site_id in seen_site_ids:
                discovery_links.append({
                    "source_anchor_id": seen_site_ids.get(fh_site_id),
                    "source_dn_site_id": fh_site_id,
                    "target_site_id": mate_site_id,
                    "kind": "dn-dn",
                    "status": "healthy" if ping_status.lower() == "up" else "down",
                    "tx_rate": tx_rate,
                    "rx_rate": rx_rate,
                })

    # Persist discovered nodes to database
    now = datetime.now(timezone.utc)
    newly_created_site_ids: set[str] = set()
    for peer in discovered_peers:
        site_id = str(peer["site_id"])
        existing = await db.get(DiscoveredNode, site_id)
        owner_anchor_id = seen_site_ids.get(site_id)
        if existing:
            existing.host = str(peer.get("host") or existing.host or "")
            existing.site_name = str(peer.get("name") or existing.site_name or site_id)
            existing.source_anchor_node_id = owner_anchor_id
            if existing.map_view_id is None:
                existing.map_view_id = map_view_id
            existing.updated_at = now
        else:
            dn = DiscoveredNode(
                site_id=site_id,
                site_name=str(peer.get("name") or site_id),
                host=str(peer.get("host") or ""),
                map_view_id=map_view_id,
                source_anchor_node_id=owner_anchor_id,
                created_at=now,
                updated_at=now,
            )
            db.add(dn)
            newly_created_site_ids.add(site_id)

        obs = await db.get(DiscoveredNodeObservation, site_id)
        ping_up = str(peer.get("ping") or "").strip().lower() == "up"
        if obs:
            obs.ping = "Up" if ping_up else "Down"
            obs.last_seen = now
            if ping_up:
                obs.last_ping_up = now
                obs.ping_down_since = None
            elif obs.ping_down_since is None:
                obs.ping_down_since = now
            obs.observed_at = now
        else:
            obs = DiscoveredNodeObservation(
                site_id=site_id,
                ping="Up" if ping_up else "Down",
                last_seen=now,
                last_ping_up=now if ping_up else None,
                ping_down_since=None if ping_up else now,
                observed_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(obs)

    await db.commit()

    # Publish discovery events only for genuinely new peers (not updates)
    for peer in discovered_peers:
        site_id = str(peer["site_id"])
        if site_id in newly_created_site_ids:
            await state_manager.publish_discovery_event(
                "dn_discovered",
                site_id,
                name=str(peer.get("name") or ""),
                host=str(peer.get("host") or ""),
                map_view_id=map_view_id,
            )

    persisted_dns = (await db.scalars(
        select(DiscoveredNode).where(DiscoveredNode.map_view_id == map_view_id)
    )).all()
    saved_positions: dict[str, dict[str, int | None]] = {}
    for dn in persisted_dns:
        if dn.map_x is not None and dn.map_y is not None:
            saved_positions[dn.site_id] = {"x": dn.map_x, "y": dn.map_y}

    live_status_by_site: dict[str, str] = {}
    for peer in discovered_peers:
        sid = str(peer["site_id"])
        snap = dn_ping_snapshots.get(sid)
        if snap:
            peer["ping_state"] = snap.get("state", "down")
            peer["latency_ms"] = snap.get("latency_ms")
            peer["avg_latency_ms"] = snap.get("avg_latency_ms")
            peer["ping_ok"] = snap.get("ping_ok", False)
            peer["ping"] = "up" if snap.get("ping_ok") else "down"
            live_status_by_site[sid] = "healthy" if snap.get("ping_ok") else "down"

    for link in discovery_links:
        live = live_status_by_site.get(str(link["target_site_id"]))
        if live:
            link["status"] = live

    return {
        "map_view_id": map_view_id,
        "anchor_count": len(anchor_nodes),
        "discovered_peers": discovered_peers,
        "discovery_links": discovery_links,
        "saved_positions": saved_positions,
    }


@router.put("/topology/maps/discovered-nodes/{site_id}/position")
async def save_dn_position(
    site_id: str,
    payload: dict[str, int | None],
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    dn = await db.get(DiscoveredNode, site_id)
    if not dn:
        raise HTTPException(status_code=404, detail="Discovered node not found")
    dn.map_x = payload.get("x")
    dn.map_y = payload.get("y")
    dn.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "ok"}
