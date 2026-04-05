"""Discovery API routes — /api/discovered-nodes, submap discovery."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DiscoveredNode, DiscoveredNodeObservation, Node, OperationalMapObject, OperationalMapView
from app.node_discovery_service import _tunnel_row_is_eligible, _tunnel_row_exists
from app import state_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/discovered-nodes/{site_id}/detail")
async def discovered_node_detail(
    site_id: str,
    window_seconds: int = Query(default=60),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    from app.main import (
        apply_windowed_detail_summary,
        node_dashboard_backend,
        normalize_node_dashboard_window,
        probe_discovered_node_detail,
    )
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    cached = node_dashboard_backend.ensure_discovered_node_cached(db, site_id)

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
async def delete_discovered_node(site_id: str, db: Session = Depends(get_db)) -> Response:
    from app.main import node_dashboard_backend
    node_dashboard_backend.delete_discovered_node(db, site_id)
    await state_manager.publish_discovery_event("dn_removed", site_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/discovered-nodes/flush-unreachable")
async def flush_unreachable_discovered_nodes(
    window_seconds: int = Query(default=60),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    from app.main import node_dashboard_backend, normalize_node_dashboard_window
    payload = node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))
    unreachable_site_ids = [
        str(row.get("site_id", "")).strip()
        for row in payload.get("discovered", [])
        if str(row.get("site_id", "")).strip()
        and str(row.get("ping") or "").strip().lower() != "up"
    ]
    deleted_site_ids = node_dashboard_backend.delete_discovered_nodes(db, unreachable_site_ids)
    for sid in deleted_site_ids:
        await state_manager.publish_discovery_event("dn_removed", sid)
    return {
        "deleted_site_ids": deleted_site_ids,
        "deleted_count": len(deleted_site_ids),
    }


@router.post("/discovered-nodes/flush-discovery")
async def flush_discovery(
    window_seconds: int = Query(default=60),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    from app.main import node_dashboard_backend, normalize_node_dashboard_window
    from app.node_discovery_service import refresh_discovered_inventory
    existing_payload = node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))
    previous_site_ids = [
        str(row.get("site_id") or "").strip()
        for row in existing_payload.get("discovered", [])
        if str(row.get("site_id") or "").strip()
    ]
    node_dashboard_backend.clear_discovery(db)
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
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
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Return tunnel peers for anchor nodes placed on this submap."""
    from app.main import dn_ping_snapshots, node_dashboard_backend, seeker_detail_cache

    map_view = db.get(OperationalMapView, map_view_id)
    if not map_view:
        raise HTTPException(status_code=404, detail="Map view not found")

    placed_objects = db.scalars(
        select(OperationalMapObject).where(
            OperationalMapObject.map_view_id == map_view_id,
            OperationalMapObject.object_type == "node",
        )
    ).all()

    placed_anchor_ids: set[int] = set()
    for obj in placed_objects:
        binding_key = obj.binding_key or ""
        if binding_key.startswith("anchor:"):
            try:
                placed_anchor_ids.add(int(binding_key.split(":")[1]))
            except (ValueError, IndexError):
                pass

    anchor_nodes = db.scalars(
        select(Node).where(Node.id.in_(placed_anchor_ids))
    ).all() if placed_anchor_ids else []

    anchor_site_id_map: dict[int, str] = {}
    for node in anchor_nodes:
        detail = seeker_detail_cache.get(node.id) or {}
        cfg = detail.get("config_summary") if isinstance(detail.get("config_summary"), dict) else {}
        anchor_site_id_map[node.id] = str(cfg.get("site_id") or node.node_id or node.id).strip()

    all_inventory_nodes = db.scalars(select(Node)).all()
    inventory_site_ids: set[str] = set()
    inventory_hosts: set[str] = set()
    for inv_node in all_inventory_nodes:
        if inv_node.node_id:
            inventory_site_ids.add(inv_node.node_id)
            inventory_site_ids.add(inv_node.node_id.lower())
        inventory_site_ids.add(str(inv_node.id))
        if inv_node.host:
            inventory_hosts.add(str(inv_node.host).strip().lower())
        inv_detail = seeker_detail_cache.get(inv_node.id) or {}
        inv_config = inv_detail.get("config_summary") if isinstance(inv_detail.get("config_summary"), dict) else {}
        cfg_site_id = str(inv_config.get("site_id") or "").strip()
        if cfg_site_id:
            inventory_site_ids.add(cfg_site_id)

    for inv_node in all_inventory_nodes:
        inv_detail = seeker_detail_cache.get(inv_node.id) or {}
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

    known_dns = db.scalars(
        select(DiscoveredNode).where(
            DiscoveredNode.map_view_id == map_view_id,
            DiscoveredNode.source_anchor_node_id.isnot(None),
        )
    ).all()
    for kdn in known_dns:
        if kdn.site_id not in seen_site_ids and kdn.source_anchor_node_id:
            seen_site_ids[kdn.site_id] = kdn.source_anchor_node_id

    for node in anchor_nodes:
        detail = seeker_detail_cache.get(node.id) or {}
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
    first_hop_site_ids = set(seen_site_ids.keys())
    for first_hop_peer in list(discovered_peers):
        fh_site_id = str(first_hop_peer["site_id"])
        cached_dn = node_dashboard_backend.get_cached_discovered_node(fh_site_id)
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
    for peer in discovered_peers:
        site_id = str(peer["site_id"])
        existing = db.get(DiscoveredNode, site_id)
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

        obs = db.get(DiscoveredNodeObservation, site_id)
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

    db.commit()

    # Publish discovery events for newly-discovered peers
    for peer in discovered_peers:
        await state_manager.publish_discovery_event(
            "dn_discovered",
            str(peer["site_id"]),
            name=str(peer.get("name") or ""),
            host=str(peer.get("host") or ""),
            map_view_id=map_view_id,
        )

    persisted_dns = db.scalars(
        select(DiscoveredNode).where(DiscoveredNode.map_view_id == map_view_id)
    ).all()
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
    db: Session = Depends(get_db),
) -> dict[str, str]:
    dn = db.get(DiscoveredNode, site_id)
    if not dn:
        raise HTTPException(status_code=404, detail="Discovered node not found")
    dn.map_x = payload.get("x")
    dn.map_y = payload.get("y")
    dn.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok"}
