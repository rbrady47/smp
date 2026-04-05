"""Service poller — HTTP/DNS service checks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import subprocess
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ServiceCheck
from app import state_manager

if TYPE_CHECKING:
    from app.poller_state import PollerState

logger = logging.getLogger(__name__)

SERVICE_POLL_INTERVAL_SECONDS = 30.0
SERVICE_CHECK_TIMEOUT_SECONDS = 5.0


def build_service_snapshot(
    service: ServiceCheck,
    *,
    status: str,
    message: str,
    latency_ms: int | None = None,
    http_status: int | None = None,
    resolved_addresses: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": service.id,
        "name": service.name,
        "service_type": service.service_type,
        "target": service.target,
        "enabled": service.enabled,
        "status": status,
        "message": message,
        "latency_ms": latency_ms,
        "http_status": http_status,
        "resolved_addresses": resolved_addresses or [],
        "last_checked": datetime.now(timezone.utc).isoformat(),
    }


async def check_url_service(service: ServiceCheck) -> dict[str, object]:
    target = service.target.strip()
    if not target:
        return build_service_snapshot(service, status="failed", message="Missing URL target")

    parsed = urlparse(target)
    if not parsed.scheme or not parsed.netloc:
        return build_service_snapshot(service, status="failed", message="Invalid URL")

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            verify=False,
            timeout=SERVICE_CHECK_TIMEOUT_SECONDS,
        ) as client:
            response = await client.get(target)
    except httpx.TimeoutException:
        return build_service_snapshot(service, status="failed", message="Timed out")
    except httpx.HTTPError as exc:
        return build_service_snapshot(service, status="failed", message=str(exc))

    latency_ms = round((time.perf_counter() - start) * 1000)
    if response.status_code < 400 or response.status_code == 403:
        status_value = "healthy"
        message = f"HTTP {response.status_code}"
    elif response.status_code < 500:
        status_value = "degraded"
        message = f"HTTP {response.status_code}"
    else:
        status_value = "failed"
        message = f"HTTP {response.status_code}"

    return build_service_snapshot(
        service,
        status=status_value,
        message=message,
        latency_ms=latency_ms,
        http_status=response.status_code,
    )


async def check_dns_service(service: ServiceCheck) -> dict[str, object]:
    target = service.target.strip()
    if not target:
        return build_service_snapshot(service, status="failed", message="Missing DNS target")

    if "|" in target:
        dns_server, probe_name = [part.strip() for part in target.split("|", 1)]
    else:
        parts = target.split()
        dns_server = parts[0]
        probe_name = " ".join(parts[1:]).strip()

    if not dns_server:
        return build_service_snapshot(service, status="failed", message="Missing DNS server")

    lookup_name = probe_name or "."

    start = time.perf_counter()
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            ["nslookup", "-type=ns", lookup_name, dns_server],
            capture_output=True,
            text=True,
            timeout=SERVICE_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        return build_service_snapshot(service, status="failed", message=str(exc))

    output = f"{completed.stdout}\n{completed.stderr}".strip()
    latency_ms = round((time.perf_counter() - start) * 1000)

    if completed.returncode != 0:
        return build_service_snapshot(
            service,
            status="failed",
            message=output or "nslookup failed",
            latency_ms=latency_ms,
        )

    failure_markers = ("timed out", "non-existent domain", "server failed", "refused")
    lowered_output = output.lower()
    if any(marker in lowered_output for marker in failure_markers):
        return build_service_snapshot(
            service,
            status="failed",
            message=output or "Resolver did not answer successfully",
            latency_ms=latency_ms,
        )

    resolved_lines = [line.strip() for line in output.splitlines() if line.strip().lower().startswith(("address:", "addresses:", "internet address"))]
    resolved_addresses = []
    for line in resolved_lines:
        _, _, raw_value = line.partition(":")
        if raw_value:
            resolved_addresses.extend([value.strip() for value in raw_value.split(",") if value.strip()])

    message = f"Resolved {lookup_name} via {dns_server}"
    if resolved_addresses:
        message = f"{message} ({', '.join(resolved_addresses[:3])})"

    return build_service_snapshot(
        service,
        status="healthy",
        message=message,
        latency_ms=latency_ms,
        resolved_addresses=resolved_addresses,
    )


async def check_service(service: ServiceCheck) -> dict[str, object]:
    if not service.enabled:
        return build_service_snapshot(service, status="disabled", message="Check disabled")

    if service.service_type == "dns":
        return await check_dns_service(service)

    return await check_url_service(service)


async def service_polling_loop(ps: PollerState) -> None:
    while True:
        db = SessionLocal()
        try:
            services = db.scalars(select(ServiceCheck).order_by(ServiceCheck.service_type, ServiceCheck.name, ServiceCheck.id)).all()
        finally:
            db.close()

        if services:
            results = await asyncio.gather(*(check_service(service) for service in services), return_exceptions=True)
            for service, result in zip(services, results):
                if isinstance(result, Exception):
                    snapshot = build_service_snapshot(
                        service,
                        status="failed",
                        message="Service check failed",
                    )
                else:
                    snapshot = result
                ps.service_status_cache[service.id] = snapshot
                await state_manager.publish_service_state(service.id, snapshot)

        await asyncio.sleep(SERVICE_POLL_INTERVAL_SECONDS)


def merge_service_payload(ps: PollerState, service: ServiceCheck) -> dict[str, object]:
    cached = ps.service_status_cache.get(service.id)
    payload = {
        "id": service.id,
        "name": service.name,
        "service_type": service.service_type,
        "target": service.target,
        "enabled": service.enabled,
        "notes": service.notes,
        "created_at": service.created_at.isoformat() if service.created_at else None,
    }
    if cached:
        payload.update(cached)
    else:
        payload.update(
            {
                "status": "disabled" if not service.enabled else "unknown",
                "message": "Pending first check" if service.enabled else "Check disabled",
                "latency_ms": None,
                "http_status": None,
                "resolved_addresses": [],
                "last_checked": None,
            }
        )
    return payload


def summarize_service_statuses(services: list[dict[str, object]]) -> dict[str, int]:
    summary = {
        "total": len(services),
        "healthy": 0,
        "degraded": 0,
        "failed": 0,
        "disabled": 0,
        "unknown": 0,
    }
    for service in services:
        status_value = str(service.get("status") or "unknown")
        summary[status_value] = summary.get(status_value, 0) + 1
    return summary
