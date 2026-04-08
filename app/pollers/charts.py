"""Charts poller — collects per-second traffic counters from Seeker nodes.

Polls the ``bwvChartStats`` endpoint every 60 seconds for each enabled
anchor node.  Parsed entries are bulk-inserted into the ``chart_samples``
table for weekly reporting.  Duplicate (node_id, timestamp) rows are
silently skipped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.models import ChartSample, Node
from app.seeker_api import get_bwv_chart_stats

if TYPE_CHECKING:
    from app.poller_state import PollerState

logger = logging.getLogger(__name__)

CHARTS_POLL_INTERVAL_SECONDS = 60.0
CHARTS_POLL_CONCURRENCY = 10
CHARTS_ENTRIES_PER_REQUEST = 30     # most recent 30 seconds of raw data per poll


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_log_entries(
    log_entries_str: str,
    node_id: int,
    *,
    decimated: bool = False,
) -> list[dict]:
    """Parse the newline-delimited ``logEntries`` string into row dicts.

    Each line has the form::

        <epoch>,c0t:4487,c0r:4016,...,ut:8078:33,ur:6719:41

    When *decimated* is True (df > 0 was used), lines alternate
    min/max: first line = min, second line = max, third = min, etc.
    The Seeker gives each line a different timestamp.

    Returns a list of dicts ready for ``ChartSample`` insertion.
    """
    rows: list[dict] = []
    if not log_entries_str:
        return rows

    line_index = 0

    for line in log_entries_str.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        parts = line.split(",")
        if not parts:
            continue

        ts = _safe_int(parts[0])
        if ts is None:
            continue

        user_tx_bytes: int | None = None
        user_tx_pkts: int | None = None
        user_rx_bytes: int | None = None
        user_rx_pkts: int | None = None
        channels: list[dict] = []
        tunnels: list[dict] = []

        for field in parts[1:]:
            field = field.strip()
            if not field:
                continue

            if field.startswith("ut:"):
                # ut:8078:33  → bytes:packets
                ut_parts = field[3:].split(":")
                user_tx_bytes = _safe_int(ut_parts[0])
                user_tx_pkts = _safe_int(ut_parts[1]) if len(ut_parts) > 1 else None
            elif field.startswith("ur:"):
                ur_parts = field[3:].split(":")
                user_rx_bytes = _safe_int(ur_parts[0])
                user_rx_pkts = _safe_int(ur_parts[1]) if len(ur_parts) > 1 else None
            else:
                # Channel: c<N>t:<val> or c<N>r:<val>
                ch_match = re.match(r"^c(\d+)(t|r):(\d+)$", field)
                if ch_match:
                    ch_idx = int(ch_match.group(1))
                    direction = ch_match.group(2)
                    val = int(ch_match.group(3))
                    # Find or create channel entry
                    ch_entry = next((c for c in channels if c["ch"] == ch_idx), None)
                    if ch_entry is None:
                        ch_entry = {"ch": ch_idx, "tx": 0, "rx": 0}
                        channels.append(ch_entry)
                    if direction == "t":
                        ch_entry["tx"] = val
                    else:
                        ch_entry["rx"] = val
                    continue

                # Tunnel: s<M>_<N>t:<val>, s<M>_<N>r:<val>, s<M>_<N>d:<val>
                tun_match = re.match(r"^s(\d+)_(\d+)(t|r|d):(\d+)$", field)
                if tun_match:
                    site_idx = int(tun_match.group(1))
                    tun_idx = int(tun_match.group(2))
                    direction = tun_match.group(3)
                    val = int(tun_match.group(4))
                    tun_entry = next(
                        (t for t in tunnels if t["site"] == site_idx and t["tunnel"] == tun_idx),
                        None,
                    )
                    if tun_entry is None:
                        tun_entry = {"site": site_idx, "tunnel": tun_idx, "tx": 0, "rx": 0, "delay_us": 0}
                        tunnels.append(tun_entry)
                    if direction == "t":
                        tun_entry["tx"] = val
                    elif direction == "r":
                        tun_entry["rx"] = val
                    else:
                        tun_entry["delay_us"] = val

        # Determine sample_type
        if decimated:
            sample_type = "min" if line_index % 2 == 0 else "max"
            line_index += 1
        else:
            sample_type = "raw"

        rows.append({
            "node_id": node_id,
            "timestamp": ts,
            "sample_type": sample_type,
            "user_tx_bytes": user_tx_bytes,
            "user_tx_pkts": user_tx_pkts,
            "user_rx_bytes": user_rx_bytes,
            "user_rx_pkts": user_rx_pkts,
            "channel_data": json.dumps(channels) if channels else None,
            "tunnel_data": json.dumps(tunnels) if tunnels else None,
            "created_at": datetime.now(timezone.utc),
        })

    return rows


def _insert_rows(rows: list[dict], node_name: str, node_id: int) -> None:
    """Bulk insert chart sample rows, skipping duplicates."""
    if not rows:
        return
    db = SessionLocal()
    try:
        stmt = pg_insert(ChartSample).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_chart_samples_node_ts_type",
        )
        db.execute(stmt)
        db.commit()
        logger.debug(
            "Inserted %d chart samples for node %s (id=%d)",
            len(rows), node_name, node_id,
        )
    except Exception:
        logger.exception("Failed to insert chart samples for node %s (id=%d)", node_name, node_id)
        db.rollback()
    finally:
        db.close()


async def _poll_node_chart_stats(
    ps: PollerState,
    node: Node,
) -> None:
    """Fetch and store chart stats for a single node.

    Single fetch: startTime=0 (Seeker auto-computes now - entries),
    entries=30. Returns the most recent 30 seconds of raw per-second
    data. No cursor tracking needed — duplicates handled by ON CONFLICT.
    """
    result = await get_bwv_chart_stats(
        node,
        start_time=0,
        entries=CHARTS_ENTRIES_PER_REQUEST,
    )

    if result.get("status") != "ok":
        logger.warning(
            "Chart stats fetch failed for node %s (id=%d): %s",
            node.name, node.id, result.get("message", "unknown error"),
        )
        return

    raw = result.get("raw") or {}
    log_entries_str = raw.get("logEntries", "")

    if log_entries_str:
        rows = parse_log_entries(log_entries_str, node.id)
        await asyncio.to_thread(_insert_rows, rows, node.name, node.id)


async def charts_polling_loop(ps: PollerState) -> None:
    """Poll bwvChartStats for all enabled anchor nodes every 60s."""
    logger.info(
        "Charts poller started: interval=%.0fs, concurrency=%d",
        CHARTS_POLL_INTERVAL_SECONDS, CHARTS_POLL_CONCURRENCY,
    )
    while True:
        t0 = time.monotonic()
        try:
            def _query_nodes():
                db = SessionLocal()
                try:
                    return db.scalars(select(Node).order_by(Node.id)).all()
                finally:
                    db.close()

            nodes = await asyncio.to_thread(_query_nodes)

            enabled_nodes = [
                node for node in nodes
                if node.enabled and node.api_username and node.api_password and node.charts_enabled
            ]

            if enabled_nodes:
                sem = asyncio.Semaphore(CHARTS_POLL_CONCURRENCY)

                async def _poll_with_limit(node: Node) -> None:
                    async with sem:
                        await asyncio.wait_for(
                            _poll_node_chart_stats(ps, node), timeout=30.0,
                        )

                results = await asyncio.gather(
                    *(_poll_with_limit(node) for node in enabled_nodes),
                    return_exceptions=True,
                )
                for node, result in zip(enabled_nodes, results):
                    if isinstance(result, Exception):
                        logger.warning(
                            "Chart stats poll failed for node %s (id=%d): %s",
                            node.name, node.id, result,
                        )

            elapsed = time.monotonic() - t0
            logger.info(
                "Charts poll cycle completed in %.1fs for %d nodes",
                elapsed, len(enabled_nodes),
            )
        except Exception:
            logger.exception("Charts polling loop iteration failed")

        await asyncio.sleep(CHARTS_POLL_INTERVAL_SECONDS)
