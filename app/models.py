from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    web_port: Mapped[int] = mapped_column(Integer, nullable=False, default=443)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    include_in_topology: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    topology_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topology_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_use_https: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ping_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    ping_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=15, server_default="15")
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ServiceCheck(Base):
    __tablename__ = "service_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    service_type: Mapped[str] = mapped_column(String(32), nullable=False, default="url")
    target: Mapped[str] = mapped_column(String(512), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class DiscoveredNode(Base):
    __tablename__ = "discovered_nodes"

    site_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    site_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    discovered_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discovered_parent_site_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    discovered_parent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    surfaced_by_names_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class DiscoveredNodeObservation(Base):
    __tablename__ = "discovered_node_observations"

    site_id: Mapped[str] = mapped_column(String(64), ForeignKey("discovered_nodes.site_id"), primary_key=True, index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tx_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rx_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tx_display: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rx_display: Mapped[str | None] = mapped_column(String(64), nullable=True)
    web_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ssh_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ping: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ping_up: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ping_down_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class NodeRelationship(Base):
    __tablename__ = "node_relationships"

    source_site_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_site_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    relationship_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_row_type: Mapped[str] = mapped_column(String(16), nullable=False, default="anchor")
    target_row_type: Mapped[str] = mapped_column(String(16), nullable=False, default="discovered")
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    discovered_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class TopologyLink(Base):
    __tablename__ = "topology_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_anchor: Mapped[str] = mapped_column(String(8), nullable=False, default="e")
    target_anchor: Mapped[str] = mapped_column(String(8), nullable=False, default="w")
    link_type: Mapped[str] = mapped_column(String(16), nullable=False, default="solid")
    status_node_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class TopologyEditorState(Base):
    __tablename__ = "topology_editor_state"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    layout_overrides_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_log_layout_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    link_anchor_assignments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo_mode_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class OperationalMapView(Base):
    __tablename__ = "operational_map_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    map_type: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")
    parent_map_id: Mapped[int | None] = mapped_column(ForeignKey("operational_map_views.id"), nullable=True, index=True)
    background_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    canvas_width: Mapped[int] = mapped_column(Integer, nullable=False, default=1920)
    canvas_height: Mapped[int] = mapped_column(Integer, nullable=False, default=1080)
    default_zoom: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class OperationalMapObject(Base):
    __tablename__ = "operational_map_objects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    map_view_id: Mapped[int] = mapped_column(ForeignKey("operational_map_views.id"), nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    x: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    y: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=160)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=96)
    z_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    node_site_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    binding_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    child_map_view_id: Mapped[int | None] = mapped_column(ForeignKey("operational_map_views.id"), nullable=True, index=True)
    connection_points_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    style_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class OperationalMapLink(Base):
    __tablename__ = "operational_map_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    map_view_id: Mapped[int] = mapped_column(ForeignKey("operational_map_views.id"), nullable=False, index=True)
    source_object_id: Mapped[int] = mapped_column(ForeignKey("operational_map_objects.id"), nullable=False, index=True)
    source_port: Mapped[str] = mapped_column(String(64), nullable=False)
    target_object_id: Mapped[int] = mapped_column(ForeignKey("operational_map_objects.id"), nullable=False, index=True)
    target_port: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    style_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    points_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class OperationalMapObjectBinding(Base):
    __tablename__ = "operational_map_object_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    object_id: Mapped[int] = mapped_column(ForeignKey("operational_map_objects.id"), nullable=False, index=True)
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="node")
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class OperationalMapLinkBinding(Base):
    __tablename__ = "operational_map_link_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("operational_map_links.id"), nullable=False, index=True)
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    source_side: Mapped[str] = mapped_column(String(32), nullable=False, default="target")
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
