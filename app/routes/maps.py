"""Operational Map routes — /api/topology/maps CRUD, objects, links, bindings."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas import (
    OperationalMapLinkBindingCreate,
    OperationalMapLinkCreate,
    OperationalMapLinkUpdate,
    OperationalMapObjectBindingCreate,
    OperationalMapObjectCreate,
    OperationalMapObjectUpdate,
    OperationalMapViewCreate,
    OperationalMapViewUpdate,
)
import app.operational_map_service as operational_map_service
from app import state_manager

router = APIRouter(prefix="/api")


@router.get("/topology/maps")
async def list_map_views(db: AsyncSession = Depends(get_db)) -> list[dict[str, object]]:
    return await operational_map_service.list_map_views(db)


@router.post("/topology/maps", status_code=status.HTTP_201_CREATED)
async def create_map_view(
    payload: OperationalMapViewCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    result = await operational_map_service.create_map_view(payload, db)
    await state_manager.publish_topology_change("map_created", id=result.get("id"))
    return result


@router.get("/topology/maps/{map_view_id}")
async def get_map_view_detail(
    map_view_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    return await operational_map_service.get_map_view_detail(map_view_id, db)


@router.put("/topology/maps/{map_view_id}")
async def update_map_view(
    map_view_id: int,
    payload: OperationalMapViewUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    return await operational_map_service.update_map_view(map_view_id, payload, db)


@router.delete("/topology/maps/{map_view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_map_view(
    map_view_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    await operational_map_service.delete_map_view(map_view_id, db)
    await state_manager.publish_topology_change("map_deleted", id=map_view_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Map objects ---


@router.post("/topology/maps/{map_view_id}/objects", status_code=status.HTTP_201_CREATED)
async def create_map_object(
    map_view_id: int,
    payload: OperationalMapObjectCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if payload.map_view_id != map_view_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="map_view_id in body must match URL")
    return await operational_map_service.create_map_object(payload, db)


@router.put("/topology/maps/objects/{object_id}")
async def update_map_object(
    object_id: int,
    payload: OperationalMapObjectUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    return await operational_map_service.update_map_object(object_id, payload, db)


@router.delete("/topology/maps/objects/{object_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_map_object(
    object_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    await operational_map_service.delete_map_object(object_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Object bindings ---


@router.post("/topology/maps/objects/{object_id}/bindings", status_code=status.HTTP_201_CREATED)
async def create_map_object_binding(
    object_id: int,
    payload: OperationalMapObjectBindingCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if payload.object_id != object_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="object_id in body must match URL")
    return await operational_map_service.create_map_object_binding(payload, db)


@router.delete("/topology/maps/objects/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_map_object_binding(
    binding_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    await operational_map_service.delete_map_object_binding(binding_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Map links ---


@router.post("/topology/maps/{map_view_id}/links", status_code=status.HTTP_201_CREATED)
async def create_map_link(
    map_view_id: int,
    payload: OperationalMapLinkCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if payload.map_view_id != map_view_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="map_view_id in body must match URL")
    return await operational_map_service.create_map_link(payload, db)


@router.put("/topology/maps/links/{link_id}")
async def update_map_link(
    link_id: int,
    payload: OperationalMapLinkUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    return await operational_map_service.update_map_link(link_id, payload, db)


@router.delete("/topology/maps/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_map_link(
    link_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    await operational_map_service.delete_map_link(link_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Link bindings ---


@router.post("/topology/maps/links/{link_id}/bindings", status_code=status.HTTP_201_CREATED)
async def create_map_link_binding(
    link_id: int,
    payload: OperationalMapLinkBindingCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if payload.link_id != link_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="link_id in body must match URL")
    return await operational_map_service.create_map_link_binding(payload, db)


@router.delete("/topology/maps/links/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_map_link_binding(
    binding_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    await operational_map_service.delete_map_link_binding(binding_id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
