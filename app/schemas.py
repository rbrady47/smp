from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NodeBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    node_id: str | None = Field(default=None, max_length=64)
    host: str = Field(..., min_length=1, max_length=255)
    web_port: int = Field(default=443, ge=1, le=65535)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    location: str = Field(..., min_length=1, max_length=255)
    topology_map_id: int | None = None
    enabled: bool = True
    notes: str | None = None
    api_username: str | None = Field(default=None, max_length=255)
    api_password: str | None = Field(default=None, max_length=255)
    api_use_https: bool = False
    ping_enabled: bool = True
    ping_interval_seconds: int = Field(default=15, ge=1, le=300)
    charts_enabled: bool = True


class NodeCreate(NodeBase):
    pass


class NodeUpdate(NodeBase):
    pass


class DnPromoteRequest(BaseModel):
    """Payload for promoting a Discovered Node to an Anchor Node."""
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    host: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    web_port: int = Field(default=443, ge=1, le=65535)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    api_username: str = Field(..., min_length=1, max_length=255)
    api_password: str = Field(..., min_length=1, max_length=255)
    api_use_https: bool = False
    topology_map_id: int | None = 0
    ping_enabled: bool = True
    ping_interval_seconds: int = Field(default=15, ge=1, le=300)
    charts_enabled: bool = True
    notes: str | None = None


class ServiceCheckBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    service_type: str = Field(default="url", pattern="^(url|dns)$")
    target: str = Field(..., min_length=1, max_length=512)
    enabled: bool = True
    notes: str | None = None


class ServiceCheckCreate(ServiceCheckBase):
    pass


class NodeDashboardAnchorRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    host: str
    web_port: int
    ssh_port: int
    web_scheme: str
    ssh_username: str | None = None
    site: str
    status: str
    web_ok: bool
    ssh_ok: bool
    ping_enabled: bool = True
    ping_ok: bool
    ping_state: str
    ping_avg_ms: int | None = None
    consecutive_misses: int = 0
    latency_ms: int | None = None
    avg_latency_ms: int | None = None
    latest_latency_ms: int | None = None
    rtt_baseline_ms: int | None = None
    rtt_deviation_pct: float | None = None
    rtt_state: str | None = None
    avg_tx_bps: int | None = None
    avg_rx_bps: int | None = None
    refresh_window_seconds: int | None = None
    tx_bps: int = 0
    rx_bps: int = 0
    wan_tx_bps: int = 0
    wan_rx_bps: int = 0
    lan_tx_bps: int = 0
    lan_rx_bps: int = 0
    lan_tx_total: str = "--"
    lan_rx_total: str = "--"
    wan_tx_total: str = "--"
    wan_rx_total: str = "--"
    cpu_avg: float | None = None
    version: str = "--"
    sites_up: int = 0
    sites_total: int = 0
    wan_up: int = 0
    wan_total: int = 0
    last_seen: str | None = None
    row_type: Literal["anchor"]
    pin_key: str
    detail_url: str
    site_id: str
    site_name: str
    unit: str
    last_ping_up: str | None = None
    discovered_parent_site_id: str | None = None
    discovered_parent_name: str | None = None
    discovered_level: int = 1
    topology_map_id: int | None = None
    tx_display: str = "--"
    rx_display: str = "--"


class NodeDashboardDiscoveredRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_type: Literal["discovered"]
    pin_key: str
    detail_url: str
    site_id: str
    site_name: str
    host: str
    location: str = "--"
    unit: str = "--"
    version: str = "--"
    discovered_level: int = 2
    discovered_parent_site_id: str | None = None
    discovered_parent_name: str | None = None
    surfaced_by_names: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    avg_latency_ms: int | None = None
    latest_latency_ms: int | None = None
    rtt_baseline_ms: int | None = None
    rtt_deviation_pct: float | None = None
    rtt_state: str | None = None
    avg_tx_bps: int | None = None
    avg_rx_bps: int | None = None
    refresh_window_seconds: int | None = None
    tx_bps: int = 0
    rx_bps: int = 0
    tx_display: str = "--"
    rx_display: str = "--"
    web_ok: bool = False
    ssh_ok: bool = False
    ping: str = "Down"
    last_seen: str | None = None
    last_ping_up: str | None = None
    ping_down_since: str | None = None
    detail: dict[str, object] = Field(default_factory=dict)
    probed_at: str | None = None
    level: int = 2
    surfaced_by_site_id: str | None = None
    surfaced_by_name: str | None = None


class NodeDashboardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchors: list[NodeDashboardAnchorRow]
    discovered: list[NodeDashboardDiscoveredRow]


class TopologyDiscoveryAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_node_id: int
    site_id: str
    site_name: str
    location: str | None = None
    unit: str | None = None
    topology_map_id: int | None = None
    status: str
    latency_ms: int | None = None
    rtt_state: str | None = None


class TopologyDiscoveryDiscoveredNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str
    site_name: str
    host: str | None = None
    version: str | None = None
    location: str | None = None
    unit: str | None = None
    discovered_level: int = 2
    surfaced_by_site_id: str | None = None
    surfaced_by_name: str | None = None
    surfaced_by_names: list[str] = Field(default_factory=list)
    resolved_unit: str | None = None
    unit_source: str | None = None
    ping: str = "Down"
    web_ok: bool = False
    ssh_ok: bool = False
    rtt_state: str | None = None
    latency_ms: int | float | None = None


class TopologyDiscoveryRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_site_id: str
    target_site_id: str
    relationship_kind: str
    source_row_type: Literal["anchor", "discovered"]
    target_row_type: Literal["anchor", "discovered"]
    source_name: str | None = None
    target_name: str | None = None
    target_unit: str | None = None
    target_location: str | None = None
    discovered_level: int | None = None


class TopologyDiscoverySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_discovered: int = 0
    total_relationships: int = 0
    by_location: dict[str, int] = Field(default_factory=dict)
    by_unit: dict[str, int] = Field(default_factory=dict)
    by_location_unit: dict[str, int] = Field(default_factory=dict)
    by_relationship_kind: dict[str, int] = Field(default_factory=dict)
    by_unit_source: dict[str, int] = Field(default_factory=dict)


class TopologyDiscoveryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchors: list[TopologyDiscoveryAnchor]
    discovered: list[TopologyDiscoveryDiscoveredNode]
    relationships: list[TopologyDiscoveryRelationship]
    summary: TopologyDiscoverySummary


class TopologyEditorStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layout_overrides: dict[str, dict[str, object]] = Field(default_factory=dict)
    state_log_layout: dict[str, object] | None = None
    link_anchor_assignments: dict[str, dict[str, str | None]] = Field(default_factory=dict)
    demo_mode: Literal["off", "all-up", "all-down", "mix"] = "off"


class TopologyEditorStatePayload(TopologyEditorStateUpdate):
    model_config = ConfigDict(extra="forbid")

    scope: str
    exists: bool = False
    updated_at: str | None = None


TopologyLinkType = Literal["solid", "dotted"]
TopologyAnchorKey = Literal["n", "ne", "e", "se", "s", "sw", "w", "nw"]


class TopologyLinkCreate(BaseModel):
    source_entity_id: str = Field(..., min_length=1, max_length=128)
    target_entity_id: str = Field(..., min_length=1, max_length=128)
    source_anchor: TopologyAnchorKey = "e"
    target_anchor: TopologyAnchorKey = "w"
    link_type: TopologyLinkType = "solid"
    status_node_id: int | None = None


class TopologyLinkUpdate(BaseModel):
    source_anchor: TopologyAnchorKey | None = None
    target_anchor: TopologyAnchorKey | None = None
    link_type: TopologyLinkType | None = None
    status_node_id: int | None = None


class TopologyLinkRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    source_entity_id: str
    target_entity_id: str
    source_anchor: str
    target_anchor: str
    link_type: str
    status_node_id: int | None = None


class MainDashboardNodeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pin_key: str
    row_type: Literal["anchor", "discovered"]
    id: int | None = None
    name: str | None = None
    site_id: str
    site_name: str
    host: str
    site: str | None = None
    status: str
    web_ok: bool = False
    ssh_ok: bool = False
    ping_ok: bool = False
    ping_state: str = "down"
    latency_ms: int | None = None
    tx_display: str = "--"
    rx_display: str = "--"
    unit: str = "--"
    location: str = "--"
    version: str = "--"
    sites_up: int | str = "--"
    sites_total: int | str = "--"
    cpu_avg: float | None = None
    detail_url: str
    web_port: int = 443
    ssh_port: int = 22
    web_scheme: str = "http"
    ssh_username: str | None = None
    last_seen: str | None = None


class MainDashboardNodeWatchlistPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[MainDashboardNodeSummary]


MapViewType = Literal["global", "unit", "custom"]
MapObjectType = Literal["node", "submap", "label"]
MapObjectBindingSlot = Literal["primary_status", "secondary_text", "badge", "hover"]
MapLinkBindingSlot = Literal["line_status", "label", "hover"]
MapBindingSourceType = Literal["node"]
MapLinkBindingSourceSide = Literal["source", "target", "relationship"]


class OperationalMapViewBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    slug: str = Field(..., min_length=1, max_length=120)
    map_type: MapViewType = "custom"
    parent_map_id: int | None = None
    background_image_url: str | None = Field(default=None, max_length=512)
    canvas_width: int = Field(default=1920, ge=320, le=10000)
    canvas_height: int = Field(default=1080, ge=240, le=10000)
    default_zoom: int = Field(default=100, ge=10, le=400)
    notes: str | None = None


class OperationalMapViewCreate(OperationalMapViewBase):
    pass


class OperationalMapViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=120)
    map_type: MapViewType | None = None
    parent_map_id: int | None = None
    background_image_url: str | None = Field(default=None, max_length=512)
    canvas_width: int | None = Field(default=None, ge=320, le=10000)
    canvas_height: int | None = Field(default=None, ge=240, le=10000)
    default_zoom: int | None = Field(default=None, ge=10, le=400)
    notes: str | None = None


class OperationalMapViewRead(OperationalMapViewBase):
    model_config = ConfigDict(extra="forbid")

    id: int


class OperationalMapObjectBase(BaseModel):
    object_type: MapObjectType
    label: str | None = Field(default=None, max_length=255)
    x: int = Field(default=0, ge=0, le=10000)
    y: int = Field(default=0, ge=0, le=10000)
    width: int = Field(default=160, ge=24, le=4000)
    height: int = Field(default=96, ge=24, le=4000)
    z_index: int = Field(default=0, ge=0, le=100000)
    node_site_id: str | None = Field(default=None, max_length=64)
    binding_key: str | None = Field(default=None, max_length=128)
    child_map_view_id: int | None = None
    connection_points: list[str] = Field(default_factory=list)
    style: dict[str, object] = Field(default_factory=dict)


class OperationalMapObjectCreate(OperationalMapObjectBase):
    map_view_id: int


class OperationalMapObjectUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=255)
    x: int | None = Field(default=None, ge=0, le=10000)
    y: int | None = Field(default=None, ge=0, le=10000)
    width: int | None = Field(default=None, ge=24, le=4000)
    height: int | None = Field(default=None, ge=24, le=4000)
    z_index: int | None = Field(default=None, ge=0, le=100000)
    node_site_id: str | None = Field(default=None, max_length=64)
    binding_key: str | None = Field(default=None, max_length=128)
    child_map_view_id: int | None = None
    connection_points: list[str] | None = None
    style: dict[str, object] | None = None


class OperationalMapObjectRead(OperationalMapObjectBase):
    model_config = ConfigDict(extra="forbid")

    id: int
    map_view_id: int


class OperationalMapObjectBindingBase(BaseModel):
    slot: MapObjectBindingSlot
    source_type: MapBindingSourceType = "node"
    field_name: str = Field(..., min_length=1, max_length=64)
    display_mode: str | None = Field(default=None, max_length=32)
    settings: dict[str, object] = Field(default_factory=dict)


class OperationalMapObjectBindingCreate(OperationalMapObjectBindingBase):
    object_id: int


class OperationalMapObjectBindingRead(OperationalMapObjectBindingBase):
    model_config = ConfigDict(extra="forbid")

    id: int
    object_id: int


class OperationalMapLinkBase(BaseModel):
    source_object_id: int
    source_port: str = Field(..., min_length=1, max_length=64)
    target_object_id: int
    target_port: str = Field(..., min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=255)
    points: list[dict[str, int]] = Field(default_factory=list)
    style: dict[str, object] = Field(default_factory=dict)


class OperationalMapLinkCreate(OperationalMapLinkBase):
    map_view_id: int


class OperationalMapLinkUpdate(BaseModel):
    source_port: str | None = Field(default=None, min_length=1, max_length=64)
    target_port: str | None = Field(default=None, min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=255)
    points: list[dict[str, int]] | None = None
    style: dict[str, object] | None = None


class OperationalMapLinkRead(OperationalMapLinkBase):
    model_config = ConfigDict(extra="forbid")

    id: int
    map_view_id: int


class OperationalMapLinkBindingBase(BaseModel):
    slot: MapLinkBindingSlot
    source_side: MapLinkBindingSourceSide = "target"
    field_name: str = Field(..., min_length=1, max_length=64)
    display_mode: str | None = Field(default=None, max_length=32)
    settings: dict[str, object] = Field(default_factory=dict)


class OperationalMapLinkBindingCreate(OperationalMapLinkBindingBase):
    link_id: int


class OperationalMapLinkBindingRead(OperationalMapLinkBindingBase):
    model_config = ConfigDict(extra="forbid")

    id: int
    link_id: int


class OperationalMapAvailableNodeRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["anchor", "discovered"]
    site_id: str
    display_name: str
    binding_key: str
    location: str | None = None
    unit: str | None = None
    status: str = "unknown"
    discovered_level: int | None = None


class OperationalMapViewDetailPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    map_view: OperationalMapViewRead
    objects: list[OperationalMapObjectRead]
    object_bindings: list[OperationalMapObjectBindingRead]
    links: list[OperationalMapLinkRead]
    link_bindings: list[OperationalMapLinkBindingRead]
    available_nodes: list[OperationalMapAvailableNodeRead] = Field(default_factory=list)
    available_submaps: list[OperationalMapViewRead] = Field(default_factory=list)
    object_binding_catalog: dict[str, list[str]] = Field(default_factory=dict)
    link_binding_catalog: dict[str, list[str]] = Field(default_factory=dict)


