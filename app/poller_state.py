"""Centralized mutable state for all polling loops.

Replaces the 11 global dicts that were scattered across main.py.
A single PollerState instance is created during app lifespan and
passed to every poller and service function that needs shared state.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.node_dashboard_backend import NodeDashboardBackend


@dataclass
class PollerState:
    """All mutable in-memory state, owned by the application lifespan."""

    # --- AN ping state (keyed by Node.id) ---
    ping_samples_by_node: dict[int, deque[int]] = field(default_factory=dict)
    ping_snapshot_by_node: dict[int, dict[str, object]] = field(default_factory=dict)
    consecutive_misses_by_node: dict[int, int] = field(default_factory=dict)
    next_ping_at_by_node: dict[int, float] = field(default_factory=dict)

    # --- DN ping state (keyed by site_id string) ---
    dn_ping_samples: dict[str, deque[int]] = field(default_factory=dict)
    dn_ping_snapshots: dict[str, dict[str, object]] = field(default_factory=dict)
    dn_consecutive_misses: dict[str, int] = field(default_factory=dict)
    dn_next_ping_at: dict[str, float] = field(default_factory=dict)

    # --- Seeker API cache (keyed by Node.id) ---
    seeker_detail_cache: dict[int, dict[str, object]] = field(default_factory=dict)

    # --- Service check cache (keyed by ServiceCheck.id) ---
    service_status_cache: dict[int, dict[str, object]] = field(default_factory=dict)

    # --- Dashboard backend (set during init, not by dataclass default) ---
    dashboard_backend: NodeDashboardBackend | None = field(default=None, repr=False)

    # --- Background task handles ---
    ping_monitor_task: asyncio.Task | None = field(default=None, repr=False)
    seeker_poll_task: asyncio.Task | None = field(default=None, repr=False)
    site_name_resolution_task: asyncio.Task | None = field(default=None, repr=False)
    dn_seeker_poll_task: asyncio.Task | None = field(default=None, repr=False)
    service_poll_task: asyncio.Task | None = field(default=None, repr=False)
    node_dashboard_poll_task: asyncio.Task | None = field(default=None, repr=False)
