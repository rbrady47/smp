from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TopologyLevel = Literal[0, 1]
TopologyUnit = Literal["AGG", "DIV HQ", "1BCT", "2BCT", "3BCT", "CAB/DIVARTY", "Sustainment"]


class NodeBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    node_id: str | None = Field(default=None, max_length=64)
    host: str = Field(..., min_length=1, max_length=255)
    web_port: int = Field(default=443, ge=1, le=65535)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    location: str = Field(..., min_length=1, max_length=255)
    include_in_topology: bool = False
    topology_level: TopologyLevel | None = 0
    topology_unit: TopologyUnit | None = "AGG"
    enabled: bool = True
    notes: str | None = None
    api_username: str | None = Field(default=None, max_length=255)
    api_password: str | None = Field(default=None, max_length=255)
    api_use_https: bool = False


class NodeCreate(NodeBase):
    pass


class NodeUpdate(NodeBase):
    pass


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
    ping_ok: bool
    ping_state: str
    ping_avg_ms: int | None = None
    latency_ms: int | None = None
    tx_bps: int = 0
    rx_bps: int = 0
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
    include_in_topology: bool = False
    topology_level: int | None = None
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
    topology_level: int | None = None
    status: str
    include_in_topology: bool = False


class TopologyDiscoveryDiscoveredNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str
    site_name: str
    location: str | None = None
    unit: str | None = None
    discovered_level: int = 2
    surfaced_by_site_id: str | None = None
    surfaced_by_name: str | None = None
    ping: str = "Down"
    web_ok: bool = False
    ssh_ok: bool = False


class TopologyDiscoverySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_discovered: int = 0
    by_location: dict[str, int] = Field(default_factory=dict)
    by_unit: dict[str, int] = Field(default_factory=dict)
    by_location_unit: dict[str, int] = Field(default_factory=dict)


class TopologyDiscoveryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchors: list[TopologyDiscoveryAnchor]
    discovered: list[TopologyDiscoveryDiscoveredNode]
    summary: TopologyDiscoverySummary


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
