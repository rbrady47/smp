"""Topology API routes — /api/topology, links, editor-state."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import (
    DiscoveredNode,
    Node,
    OperationalMapObject,
    OperationalMapView,
    TopologyLink,
)
from app.schemas import TopologyEditorStateUpdate, TopologyLinkCreate, TopologyLinkUpdate
from app.topology import build_topology_payload_for_map, build_topology_discovery_payload
from app.topology_editor_state_service import get_topology_editor_state_payload, upsert_topology_editor_state
from app import state_manager

router = APIRouter(prefix="/api")


@router.get("/topology/discovery")
async def topology_discovery_payload(
    window_seconds: int = Query(default=60),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    from app.main import node_dashboard_backend, normalize_node_dashboard_window
    return build_topology_discovery_payload(
        node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds)),
        await node_dashboard_backend.get_topology_relationships(db),
    )


@router.get("/topology/editor-state")
async def topology_editor_state_payload(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    return await get_topology_editor_state_payload(db)


@router.put("/topology/editor-state")
async def update_topology_editor_state_route(
    payload: TopologyEditorStateUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    return await upsert_topology_editor_state(payload, db)


@router.get("/topology/links")
async def list_topology_links(db: AsyncSession = Depends(get_db)) -> list[dict[str, object]]:
    links = (await db.scalars(select(TopologyLink).order_by(TopologyLink.id))).all()
    return [
        {
            "id": link.id,
            "source_entity_id": link.source_entity_id,
            "target_entity_id": link.target_entity_id,
            "source_anchor": link.source_anchor,
            "target_anchor": link.target_anchor,
            "link_type": link.link_type,
            "status_node_id": link.status_node_id,
        }
        for link in links
    ]


@router.post("/topology/links", status_code=status.HTTP_201_CREATED)
async def create_topology_link(
    payload: TopologyLinkCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    link = TopologyLink(
        source_entity_id=payload.source_entity_id,
        target_entity_id=payload.target_entity_id,
        source_anchor=payload.source_anchor,
        target_anchor=payload.target_anchor,
        link_type=payload.link_type,
        status_node_id=payload.status_node_id,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    await state_manager.publish_topology_change("link_created", id=link.id)
    return {
        "id": link.id,
        "source_entity_id": link.source_entity_id,
        "target_entity_id": link.target_entity_id,
        "source_anchor": link.source_anchor,
        "target_anchor": link.target_anchor,
        "link_type": link.link_type,
        "status_node_id": link.status_node_id,
    }


@router.put("/topology/links/{link_id}")
async def update_topology_link(
    link_id: int,
    payload: TopologyLinkUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    link = await db.get(TopologyLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(link, key, value)
    await db.commit()
    await db.refresh(link)
    await state_manager.publish_topology_change("link_updated", id=link.id)
    return {
        "id": link.id,
        "source_entity_id": link.source_entity_id,
        "target_entity_id": link.target_entity_id,
        "source_anchor": link.source_anchor,
        "target_anchor": link.target_anchor,
        "link_type": link.link_type,
        "status_node_id": link.status_node_id,
    }


@router.delete("/topology/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topology_link(
    link_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    link = await db.get(TopologyLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    deleted_id = link.id
    await db.delete(link)
    await db.commit()
    await state_manager.publish_topology_change("link_deleted", id=deleted_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/topology")
async def topology_payload(
    window_seconds: int = Query(default=60),
    map_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    from app.main import dn_ping_snapshots, node_dashboard_backend, normalize_node_dashboard_window
    nodes = (await db.scalars(select(Node).order_by(Node.name))).all()
    dashboard_payload = node_dashboard_backend.get_cached_payload(normalize_node_dashboard_window(window_seconds))
    anchor_rows_by_id = {
        int(row.get("id")): row
        for row in dashboard_payload.get("anchors") or []
        if isinstance(row, dict) and row.get("id") is not None
    }
    inventory_nodes = []
    for node in nodes:
        anchor = anchor_rows_by_id.get(node.id, {})
        inventory_nodes.append(
            {
                "id": node.id,
                "name": node.name,
                "host": node.host,
                "location": anchor.get("site") or node.location,
                "node_id": node.node_id,
                "status": anchor.get("status"),
                "topology_map_id": node.topology_map_id,
                "site_id": anchor.get("site_id") or node.node_id,
                "latency_ms": anchor.get("latency_ms"),
                "rtt_state": anchor.get("rtt_state"),
                "tx_bps": anchor.get("tx_bps"),
                "rx_bps": anchor.get("rx_bps"),
                "tx_display": anchor.get("tx_display"),
                "rx_display": anchor.get("rx_display"),
                "cpu_avg": anchor.get("cpu_avg"),
                "version": anchor.get("version"),
                "web_port": anchor.get("web_port", node.web_port),
                "web_scheme": anchor.get("web_scheme") or ("https" if node.api_use_https else "http"),
            }
        )

    result = build_topology_payload_for_map(inventory_nodes, map_id=map_id)

    db_links = (await db.scalars(select(TopologyLink).order_by(TopologyLink.id))).all()
    authored_links = [
        {
            "id": f"topo-link-{link.id}",
            "db_id": link.id,
            "from": link.source_entity_id,
            "to": link.target_entity_id,
            "source_anchor": link.source_anchor,
            "target_anchor": link.target_anchor,
            "link_type": link.link_type,
            "status_node_id": link.status_node_id,
            "kind": "authored",
            "status": "neutral",
        }
        for link in db_links
    ]

    submap_views = (await db.scalars(
        select(OperationalMapView).where(OperationalMapView.parent_map_id.is_(None)).order_by(OperationalMapView.name)
    )).all()
    submap_objects = (await db.scalars(
        select(OperationalMapObject).where(
            OperationalMapObject.object_type == "submap",
            OperationalMapObject.child_map_view_id.isnot(None),
        ).order_by(OperationalMapObject.id)
    )).all()
    submap_object_by_child_id = {obj.child_map_view_id: obj for obj in submap_objects}

    submap_view_ids = [view.id for view in submap_views]
    submap_dn_counts: dict[int, dict] = {
        vid: {"up": 0, "down": 0, "up_names": [], "down_names": []}
        for vid in submap_view_ids
    }
    if submap_view_ids:
        submap_dns = (await db.scalars(
            select(DiscoveredNode).where(
                DiscoveredNode.map_view_id.in_(submap_view_ids),
                DiscoveredNode.source_anchor_node_id.isnot(None),
            )
        )).all()
        for dn in submap_dns:
            vid = dn.map_view_id
            if vid not in submap_dn_counts:
                continue
            snap = dn_ping_snapshots.get(dn.site_id)
            if snap and snap.get("ping_ok"):
                submap_dn_counts[vid]["up"] += 1
                submap_dn_counts[vid]["up_names"].append(dn.site_id)
            else:
                submap_dn_counts[vid]["down"] += 1
                submap_dn_counts[vid]["down_names"].append(dn.site_id)

    submaps = []
    for view in submap_views:
        obj = submap_object_by_child_id.get(view.id)
        counts = submap_dn_counts.get(view.id, {"up": 0, "down": 0})
        submaps.append({
            "id": f"submap-{view.id}",
            "map_view_id": view.id,
            "name": view.name,
            "slug": view.slug,
            "kind": "submap",
            "level": 0,
            "x": obj.x if obj else 100,
            "y": obj.y if obj else 100,
            "width": obj.width if obj else 192,
            "height": obj.height if obj else 72,
            "dn_up": counts["up"],
            "dn_down": counts["down"],
            "dn_up_names": counts["up_names"],
            "dn_down_names": counts["down_names"],
        })

    result["links"] = authored_links
    result["submaps"] = submaps
    return result
