from __future__ import annotations

import json

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DiscoveredNode,
    Node,
    OperationalMapLink,
    OperationalMapLinkBinding,
    OperationalMapObject,
    OperationalMapObjectBinding,
    OperationalMapView,
)
from app.schemas import (
    OperationalMapAvailableNodeRead,
    OperationalMapLinkBindingCreate,
    OperationalMapLinkBindingRead,
    OperationalMapLinkCreate,
    OperationalMapLinkRead,
    OperationalMapLinkUpdate,
    OperationalMapObjectBindingCreate,
    OperationalMapObjectBindingRead,
    OperationalMapObjectCreate,
    OperationalMapObjectRead,
    OperationalMapObjectUpdate,
    OperationalMapViewCreate,
    OperationalMapViewDetailPayload,
    OperationalMapViewRead,
    OperationalMapViewUpdate,
)


OBJECT_BINDING_FIELD_CATALOG = {
    "primary_status": ["ping", "status"],
    "secondary_text": ["site_name", "unit", "version"],
    "badge": ["ping", "status"],
    "hover": ["tx_bps", "rx_bps", "latency_ms", "ping", "version"],
}
LINK_BINDING_FIELD_CATALOG = {
    "line_status": ["latency_ms", "ping"],
    "label": ["latency_ms", "tx_bps", "rx_bps"],
    "hover": ["latency_ms", "tx_bps", "rx_bps", "ping"],
}


def _load_json_object(raw_value: str | None) -> dict[str, object]:
    if not raw_value:
        return {}
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _load_json_string_list(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _load_json_points(raw_value: str | None) -> list[dict[str, int]]:
    if not raw_value:
        return []
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    points: list[dict[str, int]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        x = item.get("x")
        y = item.get("y")
        if isinstance(x, int) and isinstance(y, int):
            points.append({"x": x, "y": y})
    return points


def _dump_json(value: object) -> str:
    return json.dumps(value)


def _serialize_map_view(view: OperationalMapView) -> dict[str, object]:
    return OperationalMapViewRead.model_validate(
        {
            "id": view.id,
            "name": view.name,
            "slug": view.slug,
            "map_type": view.map_type,
            "parent_map_id": view.parent_map_id,
            "background_image_url": view.background_image_url,
            "canvas_width": view.canvas_width,
            "canvas_height": view.canvas_height,
            "default_zoom": view.default_zoom,
            "notes": view.notes,
        }
    ).model_dump()


def _serialize_map_object(map_object: OperationalMapObject) -> dict[str, object]:
    return OperationalMapObjectRead.model_validate(
        {
            "id": map_object.id,
            "map_view_id": map_object.map_view_id,
            "object_type": map_object.object_type,
            "label": map_object.label,
            "x": map_object.x,
            "y": map_object.y,
            "width": map_object.width,
            "height": map_object.height,
            "z_index": map_object.z_index,
            "node_site_id": map_object.node_site_id,
            "binding_key": map_object.binding_key,
            "child_map_view_id": map_object.child_map_view_id,
            "connection_points": _load_json_string_list(map_object.connection_points_json),
            "style": _load_json_object(map_object.style_json),
        }
    ).model_dump()


def _serialize_map_link(link: OperationalMapLink) -> dict[str, object]:
    return OperationalMapLinkRead.model_validate(
        {
            "id": link.id,
            "map_view_id": link.map_view_id,
            "source_object_id": link.source_object_id,
            "source_port": link.source_port,
            "target_object_id": link.target_object_id,
            "target_port": link.target_port,
            "label": link.label,
            "points": _load_json_points(link.points_json),
            "style": _load_json_object(link.style_json),
        }
    ).model_dump()


def _serialize_object_binding(binding: OperationalMapObjectBinding) -> dict[str, object]:
    return OperationalMapObjectBindingRead.model_validate(
        {
            "id": binding.id,
            "object_id": binding.object_id,
            "slot": binding.slot,
            "source_type": binding.source_type,
            "field_name": binding.field_name,
            "display_mode": binding.display_mode,
            "settings": _load_json_object(binding.settings_json),
        }
    ).model_dump()


def _serialize_link_binding(binding: OperationalMapLinkBinding) -> dict[str, object]:
    return OperationalMapLinkBindingRead.model_validate(
        {
            "id": binding.id,
            "link_id": binding.link_id,
            "slot": binding.slot,
            "source_side": binding.source_side,
            "field_name": binding.field_name,
            "display_mode": binding.display_mode,
            "settings": _load_json_object(binding.settings_json),
        }
    ).model_dump()


def _serialize_available_node(payload: dict[str, object]) -> dict[str, object]:
    return OperationalMapAvailableNodeRead.model_validate(payload).model_dump()


def _get_map_view_or_404(map_view_id: int, db: Session) -> OperationalMapView:
    map_view = db.get(OperationalMapView, map_view_id)
    if map_view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operational map view not found")
    return map_view


def _get_map_object_or_404(object_id: int, db: Session) -> OperationalMapObject:
    map_object = db.get(OperationalMapObject, object_id)
    if map_object is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operational map object not found")
    return map_object


def _get_map_link_or_404(link_id: int, db: Session) -> OperationalMapLink:
    link = db.get(OperationalMapLink, link_id)
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operational map link not found")
    return link


def _get_object_binding_or_404(binding_id: int, db: Session) -> OperationalMapObjectBinding:
    binding = db.get(OperationalMapObjectBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operational map object binding not found")
    return binding


def _get_link_binding_or_404(binding_id: int, db: Session) -> OperationalMapLinkBinding:
    binding = db.get(OperationalMapLinkBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operational map link binding not found")
    return binding


def _ensure_unique_map_slug(slug: str, db: Session, *, exclude_map_view_id: int | None = None) -> None:
    stmt = select(OperationalMapView).where(OperationalMapView.slug == slug)
    existing = db.scalars(stmt).first()
    if existing is None:
        return
    if exclude_map_view_id is not None and existing.id == exclude_map_view_id:
        return
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Operational map slug already exists")


def _validate_parent_map(parent_map_id: int | None, current_map_view_id: int | None, db: Session) -> None:
    if parent_map_id is None:
        return
    if current_map_view_id is not None and parent_map_id == current_map_view_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Operational map cannot parent itself")
    _get_map_view_or_404(parent_map_id, db)


def _resolve_node_binding_key(node_site_id: str, db: Session) -> str:
    node_site_id = node_site_id.strip()
    if not node_site_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Node site ID is required")

    anchor_stmt = select(Node).where(Node.node_id == node_site_id)
    anchor = db.scalars(anchor_stmt).first()
    if anchor is not None:
        return f"anchor:{anchor.id}"

    if node_site_id.isdigit():
        anchor_by_id = db.get(Node, int(node_site_id))
        if anchor_by_id is not None:
            return f"anchor:{anchor_by_id.id}"

    discovered = db.get(DiscoveredNode, node_site_id)
    if discovered is not None:
        return f"discovered:{discovered.site_id}"

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Assigned node site ID was not found")


def _validate_map_object_payload(
    map_view_id: int,
    object_type: str,
    node_site_id: str | None,
    child_map_view_id: int | None,
    db: Session,
) -> str | None:
    _get_map_view_or_404(map_view_id, db)

    if object_type == "node":
        if not node_site_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Node objects require a node site ID")
        return _resolve_node_binding_key(node_site_id, db)

    if node_site_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only node objects can assign a node site ID")

    if object_type == "submap":
        if child_map_view_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Submap objects require a child map view")
        if child_map_view_id == map_view_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Submap objects cannot drill into the same map")
        _get_map_view_or_404(child_map_view_id, db)
    elif child_map_view_id is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only submap objects can set a child map view")

    return None


def _validate_object_binding_payload(binding: OperationalMapObjectBindingCreate, map_object: OperationalMapObject) -> None:
    allowed_fields = OBJECT_BINDING_FIELD_CATALOG.get(binding.slot, [])
    if binding.field_name not in allowed_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Field '{binding.field_name}' is not allowed for object binding slot '{binding.slot}'",
        )
    if map_object.object_type != "node":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only node objects currently support status bindings")


def _validate_link_binding_payload(binding: OperationalMapLinkBindingCreate) -> None:
    allowed_fields = LINK_BINDING_FIELD_CATALOG.get(binding.slot, [])
    if binding.field_name not in allowed_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Field '{binding.field_name}' is not allowed for link binding slot '{binding.slot}'",
        )


def _list_available_nodes(db: Session) -> list[dict[str, object]]:
    available_nodes: list[dict[str, object]] = []

    anchors = db.scalars(select(Node).order_by(Node.name, Node.id)).all()
    for anchor in anchors:
        site_id = anchor.node_id or str(anchor.id)
        available_nodes.append(
            _serialize_available_node(
                {
                    "source_type": "anchor",
                    "site_id": site_id,
                    "display_name": anchor.name,
                    "binding_key": f"anchor:{anchor.id}",
                    "location": anchor.location,
                    "unit": anchor.topology_unit,
                    "status": "unknown",
                    "discovered_level": None,
                }
            )
        )

    discovered_nodes = db.scalars(select(DiscoveredNode).order_by(DiscoveredNode.site_name, DiscoveredNode.site_id)).all()
    for discovered in discovered_nodes:
        available_nodes.append(
            _serialize_available_node(
                {
                    "source_type": "discovered",
                    "site_id": discovered.site_id,
                    "display_name": discovered.site_name or discovered.site_id,
                    "binding_key": f"discovered:{discovered.site_id}",
                    "location": discovered.location,
                    "unit": discovered.unit,
                    "status": "unknown",
                    "discovered_level": discovered.discovered_level,
                }
            )
        )

    return available_nodes


def _list_available_submaps(current_map_view_id: int, db: Session) -> list[dict[str, object]]:
    submaps = db.scalars(
        select(OperationalMapView).where(OperationalMapView.id != current_map_view_id).order_by(OperationalMapView.name, OperationalMapView.id)
    ).all()
    return [_serialize_map_view(submap) for submap in submaps]


def list_map_views(db: Session) -> list[dict[str, object]]:
    views = db.scalars(select(OperationalMapView).order_by(OperationalMapView.name, OperationalMapView.id)).all()
    return [_serialize_map_view(view) for view in views]


def create_map_view(payload: OperationalMapViewCreate, db: Session) -> dict[str, object]:
    _ensure_unique_map_slug(payload.slug, db)
    _validate_parent_map(payload.parent_map_id, None, db)
    map_view = OperationalMapView(**payload.model_dump())
    db.add(map_view)
    db.commit()
    db.refresh(map_view)
    return _serialize_map_view(map_view)


def update_map_view(map_view_id: int, payload: OperationalMapViewUpdate, db: Session) -> dict[str, object]:
    map_view = _get_map_view_or_404(map_view_id, db)
    updates = payload.model_dump(exclude_unset=True)
    if "slug" in updates:
        _ensure_unique_map_slug(str(updates["slug"]), db, exclude_map_view_id=map_view_id)
    if "parent_map_id" in updates:
        _validate_parent_map(updates["parent_map_id"], map_view_id, db)
    for field, value in updates.items():
        setattr(map_view, field, value)
    db.commit()
    db.refresh(map_view)
    return _serialize_map_view(map_view)


def delete_map_view(map_view_id: int, db: Session) -> None:
    map_view = _get_map_view_or_404(map_view_id, db)

    child_map_stmt = select(OperationalMapView).where(OperationalMapView.parent_map_id == map_view_id)
    if db.scalars(child_map_stmt).first() is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Operational map has child submaps")

    object_stmt = select(OperationalMapObject).where(OperationalMapObject.map_view_id == map_view_id)
    objects = db.scalars(object_stmt).all()
    object_ids = [map_object.id for map_object in objects]

    if object_ids:
        link_stmt = select(OperationalMapLink).where(OperationalMapLink.map_view_id == map_view_id)
        links = db.scalars(link_stmt).all()
        for link in links:
            link_binding_stmt = select(OperationalMapLinkBinding).where(OperationalMapLinkBinding.link_id == link.id)
            for binding in db.scalars(link_binding_stmt).all():
                db.delete(binding)
            db.delete(link)

        for object_id in object_ids:
            object_binding_stmt = select(OperationalMapObjectBinding).where(OperationalMapObjectBinding.object_id == object_id)
            for binding in db.scalars(object_binding_stmt).all():
                db.delete(binding)

        for map_object in objects:
            db.delete(map_object)

    db.delete(map_view)
    db.commit()


def create_map_object(payload: OperationalMapObjectCreate, db: Session) -> dict[str, object]:
    resolved_binding_key = _validate_map_object_payload(
        payload.map_view_id,
        payload.object_type,
        payload.node_site_id,
        payload.child_map_view_id,
        db,
    )
    map_object = OperationalMapObject(
        map_view_id=payload.map_view_id,
        object_type=payload.object_type,
        label=payload.label,
        x=payload.x,
        y=payload.y,
        width=payload.width,
        height=payload.height,
        z_index=payload.z_index,
        node_site_id=payload.node_site_id,
        binding_key=resolved_binding_key or payload.binding_key,
        child_map_view_id=payload.child_map_view_id,
        connection_points_json=_dump_json(payload.connection_points),
        style_json=_dump_json(payload.style),
    )
    db.add(map_object)
    db.commit()
    db.refresh(map_object)
    return _serialize_map_object(map_object)


def update_map_object(object_id: int, payload: OperationalMapObjectUpdate, db: Session) -> dict[str, object]:
    map_object = _get_map_object_or_404(object_id, db)
    updates = payload.model_dump(exclude_unset=True)

    new_node_site_id = updates["node_site_id"] if "node_site_id" in updates else map_object.node_site_id
    new_child_map_view_id = updates["child_map_view_id"] if "child_map_view_id" in updates else map_object.child_map_view_id
    resolved_binding_key = _validate_map_object_payload(
        map_object.map_view_id,
        map_object.object_type,
        new_node_site_id,
        new_child_map_view_id,
        db,
    )

    for field, value in updates.items():
        if field == "connection_points":
            map_object.connection_points_json = _dump_json(value or [])
        elif field == "style":
            map_object.style_json = _dump_json(value or {})
        else:
            setattr(map_object, field, value)

    if map_object.object_type == "node":
        map_object.binding_key = resolved_binding_key

    db.commit()
    db.refresh(map_object)
    return _serialize_map_object(map_object)


def delete_map_object(object_id: int, db: Session) -> None:
    map_object = _get_map_object_or_404(object_id, db)

    dependent_link_stmt = select(OperationalMapLink).where(
        (OperationalMapLink.source_object_id == object_id) | (OperationalMapLink.target_object_id == object_id)
    )
    dependent_links = db.scalars(dependent_link_stmt).all()
    for link in dependent_links:
        binding_stmt = select(OperationalMapLinkBinding).where(OperationalMapLinkBinding.link_id == link.id)
        for binding in db.scalars(binding_stmt).all():
            db.delete(binding)
        db.delete(link)

    binding_stmt = select(OperationalMapObjectBinding).where(OperationalMapObjectBinding.object_id == object_id)
    for binding in db.scalars(binding_stmt).all():
        db.delete(binding)

    db.delete(map_object)
    db.commit()


def create_map_object_binding(payload: OperationalMapObjectBindingCreate, db: Session) -> dict[str, object]:
    map_object = _get_map_object_or_404(payload.object_id, db)
    _validate_object_binding_payload(payload, map_object)
    binding = OperationalMapObjectBinding(
        object_id=payload.object_id,
        slot=payload.slot,
        source_type=payload.source_type,
        field_name=payload.field_name,
        display_mode=payload.display_mode,
        settings_json=_dump_json(payload.settings),
    )
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return _serialize_object_binding(binding)


def delete_map_object_binding(binding_id: int, db: Session) -> None:
    binding = _get_object_binding_or_404(binding_id, db)
    db.delete(binding)
    db.commit()


def create_map_link(payload: OperationalMapLinkCreate, db: Session) -> dict[str, object]:
    _get_map_view_or_404(payload.map_view_id, db)
    source_object = _get_map_object_or_404(payload.source_object_id, db)
    target_object = _get_map_object_or_404(payload.target_object_id, db)
    if source_object.map_view_id != payload.map_view_id or target_object.map_view_id != payload.map_view_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Links must connect objects in the same map view")

    link = OperationalMapLink(
        map_view_id=payload.map_view_id,
        source_object_id=payload.source_object_id,
        source_port=payload.source_port,
        target_object_id=payload.target_object_id,
        target_port=payload.target_port,
        label=payload.label,
        style_json=_dump_json(payload.style),
        points_json=_dump_json(payload.points),
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return _serialize_map_link(link)


def update_map_link(link_id: int, payload: OperationalMapLinkUpdate, db: Session) -> dict[str, object]:
    link = _get_map_link_or_404(link_id, db)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if field == "points":
            link.points_json = _dump_json(value or [])
        elif field == "style":
            link.style_json = _dump_json(value or {})
        else:
            setattr(link, field, value)
    db.commit()
    db.refresh(link)
    return _serialize_map_link(link)


def delete_map_link(link_id: int, db: Session) -> None:
    link = _get_map_link_or_404(link_id, db)
    binding_stmt = select(OperationalMapLinkBinding).where(OperationalMapLinkBinding.link_id == link_id)
    for binding in db.scalars(binding_stmt).all():
        db.delete(binding)
    db.delete(link)
    db.commit()


def create_map_link_binding(payload: OperationalMapLinkBindingCreate, db: Session) -> dict[str, object]:
    _get_map_link_or_404(payload.link_id, db)
    _validate_link_binding_payload(payload)
    binding = OperationalMapLinkBinding(
        link_id=payload.link_id,
        slot=payload.slot,
        source_side=payload.source_side,
        field_name=payload.field_name,
        display_mode=payload.display_mode,
        settings_json=_dump_json(payload.settings),
    )
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return _serialize_link_binding(binding)


def delete_map_link_binding(binding_id: int, db: Session) -> None:
    binding = _get_link_binding_or_404(binding_id, db)
    db.delete(binding)
    db.commit()


def get_map_view_detail(map_view_id: int, db: Session) -> dict[str, object]:
    map_view = _get_map_view_or_404(map_view_id, db)
    objects = db.scalars(
        select(OperationalMapObject).where(OperationalMapObject.map_view_id == map_view_id).order_by(OperationalMapObject.z_index, OperationalMapObject.id)
    ).all()
    object_ids = [map_object.id for map_object in objects]
    links = db.scalars(select(OperationalMapLink).where(OperationalMapLink.map_view_id == map_view_id).order_by(OperationalMapLink.id)).all()
    link_ids = [link.id for link in links]

    object_bindings = []
    if object_ids:
        object_bindings = db.scalars(
            select(OperationalMapObjectBinding).where(OperationalMapObjectBinding.object_id.in_(object_ids)).order_by(OperationalMapObjectBinding.id)
        ).all()

    link_bindings = []
    if link_ids:
        link_bindings = db.scalars(
            select(OperationalMapLinkBinding).where(OperationalMapLinkBinding.link_id.in_(link_ids)).order_by(OperationalMapLinkBinding.id)
        ).all()

    return OperationalMapViewDetailPayload.model_validate(
        {
            "map_view": _serialize_map_view(map_view),
            "objects": [_serialize_map_object(map_object) for map_object in objects],
            "object_bindings": [_serialize_object_binding(binding) for binding in object_bindings],
            "links": [_serialize_map_link(link) for link in links],
            "link_bindings": [_serialize_link_binding(binding) for binding in link_bindings],
            "available_nodes": _list_available_nodes(db),
            "available_submaps": _list_available_submaps(map_view_id, db),
            "object_binding_catalog": OBJECT_BINDING_FIELD_CATALOG,
            "link_binding_catalog": LINK_BINDING_FIELD_CATALOG,
        }
    ).model_dump()
