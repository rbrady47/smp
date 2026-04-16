"""Node API routes — /api/nodes CRUD, detail, refresh, telemetry, bwvstats."""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import (
    ChartSample,
    DiscoveredNode,
    DiscoveredNodeObservation,
    Node,
    NodeRelationship,
    OperationalMapObject,
    OperationalMapView,
    TopologyEditorState,
    TopologyLink,
)
from app.bwvstats_ingest import collect_bwvstats_phase1, get_raw_bwvstats_snapshots
from app.schemas import NodeCreate, NodeUpdate
from app import state_manager


async def sync_node_map_object(
    node: Node,
    old_map_id: int | None,
    new_map_id: int | None,
    db: AsyncSession,
) -> None:
    """Create or remove OperationalMapObject rows when a node's map assignment changes."""
    if old_map_id == new_map_id:
        return

    if old_map_id is not None and old_map_id > 0:
        old_objects = (await db.scalars(
            select(OperationalMapObject).where(
                OperationalMapObject.map_view_id == old_map_id,
                OperationalMapObject.object_type == "node",
                OperationalMapObject.binding_key == f"anchor:{node.id}",
            )
        )).all()
        for obj in old_objects:
            await db.delete(obj)

    if new_map_id is not None and new_map_id > 0:
        target_map = await db.get(OperationalMapView, new_map_id)
        if target_map is not None:
            existing = (await db.scalars(
                select(OperationalMapObject).where(
                    OperationalMapObject.map_view_id == new_map_id,
                    OperationalMapObject.object_type == "node",
                    OperationalMapObject.binding_key == f"anchor:{node.id}",
                )
            )).first()
            if existing is None:
                map_object = OperationalMapObject(
                    map_view_id=new_map_id,
                    object_type="node",
                    label=node.name,
                    x=200,
                    y=200,
                    width=160,
                    height=96,
                    z_index=0,
                    node_site_id=node.node_id,
                    binding_key=f"anchor:{node.id}",
                )
                db.add(map_object)

router = APIRouter(prefix="/api")


@router.get("/nodes/ping-status")
async def nodes_ping_status(db: AsyncSession = Depends(get_db)) -> list[dict[str, object]]:
    from app.main import ping_snapshot_by_node
    nodes = (await db.scalars(select(Node).order_by(Node.id))).all()
    result = []
    for node in nodes:
        snap = ping_snapshot_by_node.get(node.id, {})
        result.append({
            "id": node.id,
            "ping_enabled": node.ping_enabled,
            "ping_state": snap.get("state", "unknown") if node.ping_enabled else "disabled",
            "latency_ms": snap.get("latency_ms"),
            "avg_latency_ms": snap.get("avg_latency_ms"),
            "ping_ok": bool(snap.get("ping_ok", False)),
            "consecutive_misses": snap.get("consecutive_misses", 0),
        })
    return result


@router.get("/nodes")
async def list_nodes(db: AsyncSession = Depends(get_db)) -> list[dict[str, object]]:
    from app.main import node_dashboard_backend, serialize_node
    nodes = (await db.scalars(select(Node).order_by(Node.name))).all()
    cached = node_dashboard_backend.get_cached_payload(60)
    anchor_rows_by_id = {
        int(row["id"]): row
        for row in (cached.get("anchors") or [])
        if isinstance(row, dict) and row.get("id") is not None
    }
    result = []
    for node in nodes:
        cached_row = anchor_rows_by_id.get(node.id, {})
        health = {
            "status": cached_row.get("status", "unknown"),
            "latency_ms": cached_row.get("latency_ms"),
            "last_checked": node.last_checked,
            "web_ok": bool(cached_row.get("web_ok", False)),
            "ssh_ok": bool(cached_row.get("ssh_ok", False)),
            "ping_ok": bool(cached_row.get("ping_ok", False)),
            "ping_state": cached_row.get("ping_state", "unknown"),
            "ping_avg_ms": cached_row.get("ping_avg_ms"),
        }
        result.append(serialize_node(node, health))
    return result


@router.post("/nodes/refresh")
async def refresh_all_nodes(db: AsyncSession = Depends(get_db)) -> list[dict[str, object]]:
    from app.main import refresh_nodes
    try:
        nodes = (await db.scalars(select(Node).order_by(Node.name))).all()
        return await refresh_nodes(nodes, db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"/api/nodes/refresh failed: {exc}") from exc


@router.post("/nodes/{node_id}/telemetry")
async def node_telemetry(node_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    from app.main import get_node_or_404, request_node_telemetry
    node = await get_node_or_404(node_id, db)
    return await request_node_telemetry(node)


@router.get("/nodes/{node_id}/config")
async def node_config(node_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    from app.main import get_node_or_404, seeker_detail_cache
    node = await get_node_or_404(node_id, db)
    detail = seeker_detail_cache.get(node.id)
    if not detail:
        return {
            "status": "error",
            "message": "Config not available in cache yet",
            "config_summary": {},
            "mates": [],
            "static_routes": [],
        }
    return {
        "status": "ok",
        "config_summary": detail.get("config_summary", {}),
        "mates": detail.get("mates", []),
        "static_routes": detail.get("static_routes", []),
    }


@router.get("/nodes/{node_id}/stats")
async def node_stats(node_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    from app.main import get_node_or_404, seeker_detail_cache
    from app.seeker_api import normalize_bwv_stats
    node = await get_node_or_404(node_id, db)
    detail = seeker_detail_cache.get(node.id)
    if not detail:
        return {
            "status": "error",
            "message": "Stats not available in cache yet",
            "normalized": {},
            "tunnels": [],
            "channels": [],
        }
    return {
        "status": "ok",
        "normalized": normalize_bwv_stats(detail.get("raw", {}).get("bwv_stats", {}) or {}),
        "active_sites": detail.get("active_sites", []),
        "tunnels": detail.get("tunnels", []),
        "channels": detail.get("channels", []),
    }


@router.get("/nodes/{node_id}/routes")
async def node_routes(node_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    from app.main import get_node_or_404, seeker_detail_cache
    node = await get_node_or_404(node_id, db)
    detail = seeker_detail_cache.get(node.id)
    if not detail:
        return {
            "status": "error",
            "static_routes": [],
            "learnt_routes": [],
            "errors": {
                "config": "Routes not available in cache yet",
                "routes": "Routes not available in cache yet",
            },
        }
    return {
        "status": "ok",
        "static_routes": detail.get("static_routes", []),
        "learnt_routes": detail.get("learnt_routes", []),
        "errors": detail.get("errors", {}),
    }


@router.get("/nodes/{node_id}/detail")
async def node_detail(
    node_id: int,
    window_seconds: int = Query(default=60),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    from app.main import (
        apply_windowed_detail_summary,
        get_node_or_404,
        node_dashboard_backend,
        normalize_node_dashboard_window,
        refresh_seeker_detail_for_node,
        seeker_detail_cache,
    )
    node = await get_node_or_404(node_id, db)
    detail = seeker_detail_cache.get(node.id)
    if not detail:
        detail = await refresh_seeker_detail_for_node(node)
    detail_dict = dict(detail)
    detail_site_id = (
        detail_dict.get("config_summary", {}).get("site_id")
        if isinstance(detail_dict.get("config_summary"), dict)
        else None
    )
    window_metrics = node_dashboard_backend.get_row_window_metrics(
        "anchor",
        node_dashboard_backend._anchor_pin_key(node.id),
        normalize_node_dashboard_window(window_seconds),
    )
    if detail_site_id:
        window_metrics["site_id"] = detail_site_id
    return apply_windowed_detail_summary(detail_dict, window_metrics=window_metrics)


@router.get("/nodes/{node_id}/bwvstats/phase1")
async def node_bwvstats_phase1(node_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    from app.main import get_node_or_404
    node = await get_node_or_404(node_id, db)
    return await collect_bwvstats_phase1(node, emit_logs=True)


@router.get("/nodes/{node_id}/bwvstats/phase1/raw")
async def node_bwvstats_phase1_raw(node_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    from app.main import get_node_or_404
    node = await get_node_or_404(node_id, db)
    return {
        "status": "ok",
        "node_id": node.id,
        "snapshots": get_raw_bwvstats_snapshots(node.id),
    }


@router.post("/nodes/flush-all")
async def flush_all_nodes(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    from app.main import (
        node_dashboard_backend,
        ping_samples_by_node,
        ping_snapshot_by_node,
        seeker_detail_cache,
    )
    node_count = len((await db.scalars(select(Node))).all())
    discovered_count = len((await db.scalars(select(DiscoveredNode))).all())

    await db.execute(delete(DiscoveredNodeObservation))
    await db.execute(delete(NodeRelationship))
    await db.execute(delete(DiscoveredNode))
    await db.execute(delete(Node))
    await db.execute(delete(TopologyEditorState))
    await db.commit()

    ping_samples_by_node.clear()
    ping_snapshot_by_node.clear()
    seeker_detail_cache.clear()
    node_dashboard_backend.discovered_node_cache.clear()
    node_dashboard_backend.discovered_ping_cache.clear()
    node_dashboard_backend.anchor_metric_history.clear()
    node_dashboard_backend.discovered_metric_history.clear()
    node_dashboard_backend.node_dashboard_cache.update({"anchors": [], "discovered": [], "summary": {}})
    node_dashboard_backend.mark_projection_dirty()

    return {
        "deleted_nodes": node_count,
        "deleted_discovered_nodes": discovered_count,
        "status": "ok",
    }


@router.post("/nodes", status_code=status.HTTP_201_CREATED)
async def create_node(node_data: NodeCreate, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    from app.main import serialize_node
    node = Node(**node_data.model_dump())
    db.add(node)
    await db.commit()
    await db.refresh(node)
    await sync_node_map_object(node, None, node.topology_map_id, db)
    await db.commit()
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
    result = serialize_node(node, pending_health)
    await state_manager.publish_topology_change("node_created", id=node.id)
    return result


@router.put("/nodes/{node_id}")
async def update_node(
    node_id: int,
    node_data: NodeUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    from app.main import get_node_or_404, node_dashboard_backend, serialize_node
    node = await get_node_or_404(node_id, db)

    old_map_id = node.topology_map_id
    for field, value in node_data.model_dump().items():
        setattr(node, field, value)
    new_map_id = node.topology_map_id
    await sync_node_map_object(node, old_map_id, new_map_id, db)

    await db.commit()
    await db.refresh(node)
    cached = node_dashboard_backend.get_cached_payload(60)
    cached_row = next(
        (r for r in (cached.get("anchors") or []) if isinstance(r, dict) and r.get("id") == node.id),
        {},
    )
    health = {
        "status": cached_row.get("status", "unknown"),
        "latency_ms": cached_row.get("latency_ms"),
        "last_checked": node.last_checked,
        "web_ok": bool(cached_row.get("web_ok", False)),
        "ssh_ok": bool(cached_row.get("ssh_ok", False)),
        "ping_ok": bool(cached_row.get("ping_ok", False)),
        "ping_state": cached_row.get("ping_state", "unknown"),
        "ping_avg_ms": cached_row.get("ping_avg_ms"),
    }
    return serialize_node(node, health)


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(node_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    from app.main import (
        get_node_or_404,
        node_dashboard_backend,
        ping_samples_by_node,
        ping_snapshot_by_node,
        seeker_detail_cache,
    )
    node = await get_node_or_404(node_id, db)
    deleted_node_id = node.id

    # Clean up related records before deleting the node (FK constraints)
    await db.execute(delete(ChartSample).where(ChartSample.node_id == deleted_node_id))

    map_objects = (await db.scalars(
        select(OperationalMapObject).where(
            OperationalMapObject.binding_key == f"anchor:{deleted_node_id}",
        )
    )).all()
    for obj in map_objects:
        await db.delete(obj)

    topo_links = (await db.scalars(
        select(TopologyLink).where(
            (TopologyLink.source_entity_id == f"node-{deleted_node_id}")
            | (TopologyLink.target_entity_id == f"node-{deleted_node_id}")
            | (TopologyLink.status_node_id == deleted_node_id)
        )
    )).all()
    for link in topo_links:
        await db.delete(link)

    await db.delete(node)
    await db.commit()
    seeker_detail_cache.pop(deleted_node_id, None)
    ping_samples_by_node.pop(deleted_node_id, None)
    ping_snapshot_by_node.pop(deleted_node_id, None)
    remaining_nodes = (await db.scalars(select(Node).order_by(Node.name))).all()
    await node_dashboard_backend.refresh_cache(db, remaining_nodes)
    await state_manager.publish_topology_change("node_deleted", id=deleted_node_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
