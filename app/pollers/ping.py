"""Ping poller — ICMP probes for anchor nodes and discovered nodes."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import platform
import re
import socket
import subprocess
import time
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.db import SessionLocal
from app.models import DiscoveredNode, Node

if TYPE_CHECKING:
    from app.poller_state import PollerState

HEALTH_CHECK_TIMEOUT_SECONDS = 1.0
PING_HISTORY_SAMPLES = 24
PING_INTERVAL_SECONDS = 5.0


def check_tcp_port(host: str, port: int) -> dict[str, int | bool | None]:
    start_time = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=HEALTH_CHECK_TIMEOUT_SECONDS):
            return {
                "reachable": True,
                "latency_ms": round((time.perf_counter() - start_time) * 1000),
            }
    except OSError:
        return {"reachable": False, "latency_ms": None}


def ping_host(host: str) -> dict[str, int | bool | None]:
    system_name = platform.system().lower()
    if system_name == "windows":
        command = ["ping", "-n", "1", "-w", "1000", host]
    else:
        command = ["ping", "-c", "1", "-W", "1", host]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return {"reachable": False, "latency_ms": None}

    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        return {"reachable": False, "latency_ms": None}

    patterns = [
        r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms",
        r"Average = (\d+)\s*ms",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            try:
                return {"reachable": True, "latency_ms": round(float(match.group(1)))}
            except ValueError:
                break

    return {"reachable": False, "latency_ms": None}


def build_ping_snapshot(
    ps: PollerState, node_id: int, ping_result: dict[str, int | bool | None],
) -> dict[str, object]:
    samples = ps.ping_samples_by_node.setdefault(node_id, deque(maxlen=PING_HISTORY_SAMPLES))
    ping_ok = bool(ping_result["reachable"])
    latency_ms = ping_result["latency_ms"] if ping_ok else None

    if ping_ok and latency_ms is not None:
        samples.append(int(latency_ms))

    average_ms = round(sum(samples) / len(samples)) if samples else None

    if ping_ok:
        ps.consecutive_misses_by_node[node_id] = 0
    else:
        ps.consecutive_misses_by_node[node_id] = ps.consecutive_misses_by_node.get(node_id, 0) + 1

    misses = ps.consecutive_misses_by_node[node_id]

    if misses >= 5:
        state = "down"
    elif misses >= 3:
        state = "warn"
    else:
        state = "good"

    snapshot = {
        "ping_ok": ping_ok,
        "latency_ms": latency_ms,
        "avg_latency_ms": average_ms,
        "state": state,
        "consecutive_misses": misses,
        "updated_at": datetime.now(timezone.utc),
    }
    ps.ping_snapshot_by_node[node_id] = snapshot
    return snapshot


def get_ping_snapshot(ps: PollerState, node: Node) -> dict[str, object]:
    snapshot = ps.ping_snapshot_by_node.get(node.id)
    if snapshot is not None:
        return snapshot
    return build_ping_snapshot(ps, node.id, ping_host(node.host))


def build_dn_ping_snapshot(
    ps: PollerState, site_id: str, ping_result: dict[str, int | bool | None],
) -> dict[str, object]:
    samples = ps.dn_ping_samples.setdefault(site_id, deque(maxlen=PING_HISTORY_SAMPLES))
    ping_ok = bool(ping_result["reachable"])
    latency_ms = ping_result["latency_ms"] if ping_ok else None

    if ping_ok and latency_ms is not None:
        samples.append(int(latency_ms))

    average_ms = round(sum(samples) / len(samples)) if samples else None

    if ping_ok:
        ps.dn_consecutive_misses[site_id] = 0
    else:
        ps.dn_consecutive_misses[site_id] = ps.dn_consecutive_misses.get(site_id, 0) + 1

    misses = ps.dn_consecutive_misses[site_id]

    if misses >= 5:
        state = "down"
    elif misses >= 3:
        state = "warn"
    else:
        state = "good"

    snapshot = {
        "ping_ok": ping_ok,
        "latency_ms": latency_ms,
        "avg_latency_ms": average_ms,
        "state": state,
        "consecutive_misses": misses,
        "updated_at": datetime.now(timezone.utc),
    }
    ps.dn_ping_snapshots[site_id] = snapshot
    return snapshot


async def ping_node_single(host: str) -> dict[str, int | bool | None]:
    result = await asyncio.to_thread(ping_host, host)
    return {"reachable": bool(result.get("reachable")), "latency_ms": result.get("latency_ms")}


async def ping_monitor_loop(ps: PollerState) -> None:
    import time as _time

    while True:
        try:
            now = _time.monotonic()
            db = SessionLocal()
            try:
                nodes = db.scalars(select(Node).order_by(Node.id)).all()
            finally:
                db.close()

            pingable = [n for n in nodes if n.enabled and n.ping_enabled]

            due_nodes = []
            for node in pingable:
                deadline = ps.next_ping_at_by_node.get(node.id, 0.0)
                if now >= deadline:
                    due_nodes.append(node)

            if due_nodes:
                burst_results = await asyncio.gather(
                    *(ping_node_single(node.host) for node in due_nodes),
                    return_exceptions=True,
                )
                tick = _time.monotonic()
                for node, result in zip(due_nodes, burst_results):
                    interval = max(node.ping_interval_seconds, 1)
                    ps.next_ping_at_by_node[node.id] = tick + interval
                    if isinstance(result, Exception):
                        build_ping_snapshot(ps, node.id, {"reachable": False, "latency_ms": None})
                    else:
                        build_ping_snapshot(ps, node.id, result)

            db2 = SessionLocal()
            try:
                dns = db2.scalars(
                    select(DiscoveredNode).where(
                        DiscoveredNode.host.isnot(None),
                        DiscoveredNode.map_view_id.isnot(None),
                    )
                ).all()
                dn_due: list[tuple[str, str]] = []
                for dn in dns:
                    if not dn.host:
                        continue
                    deadline = ps.dn_next_ping_at.get(dn.site_id, 0.0)
                    if now >= deadline:
                        dn_due.append((dn.site_id, dn.host))
            finally:
                db2.close()

            if dn_due:
                dn_results = await asyncio.gather(
                    *(ping_node_single(host) for _, host in dn_due),
                    return_exceptions=True,
                )
                tick = _time.monotonic()
                for (site_id, _host), result in zip(dn_due, dn_results):
                    ps.dn_next_ping_at[site_id] = tick + PING_INTERVAL_SECONDS
                    if isinstance(result, Exception):
                        build_dn_ping_snapshot(ps, site_id, {"reachable": False, "latency_ms": None})
                    else:
                        build_dn_ping_snapshot(ps, site_id, result)
        except Exception:
            logging.getLogger(__name__).exception("Ping monitor loop iteration failed")

        await asyncio.sleep(1.0)
