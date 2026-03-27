from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
