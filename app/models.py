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
