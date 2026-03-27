from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import json
import logging
from typing import Any

from app.models import Node
from app.seeker_api import get_bwv_stats

logger = logging.getLogger(__name__)

BWVSTATS_PHASE1_ARGS = {
    "adminInfo": "1",
    "routeHits": "1",
    "qos": "1",
}

RAW_BWVSTATS_SNAPSHOTS: dict[int, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=25))


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _iter_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_objects(nested)


def _find_value(data: Any, keys: list[str]) -> Any | None:
    lowered = {key.lower() for key in keys}
    for obj in _iter_objects(data):
        if not isinstance(obj, dict):
            continue
        for key, value in obj.items():
            if str(key).lower() in lowered:
                return value
    return None


def _find_list(data: Any, keys: list[str]) -> list[Any]:
    value = _find_value(data, keys)
    return value if isinstance(value, list) else []


def _find_dict(data: Any, keys: list[str]) -> dict[str, Any]:
    value = _find_value(data, keys)
    return value if isinstance(value, dict) else {}


def _serialize_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def capture_raw_bwvstats_poll(
    *,
    node_id: int,
    request_params: dict[str, Any],
    raw_json: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    snapshot = {
        "node_id": node_id,
        "timestamp": timestamp,
        "request_params": dict(request_params),
        "raw_json": raw_json,
    }
    RAW_BWVSTATS_SNAPSHOTS[node_id].append(snapshot)
    return snapshot


def get_raw_bwvstats_snapshots(node_id: int) -> list[dict[str, Any]]:
    return list(RAW_BWVSTATS_SNAPSHOTS.get(node_id, ()))


def _extract_status(raw: dict[str, Any]) -> str:
    value = _find_value(raw, ["status", "nodeStatus", "isActive"])
    if value is None:
        return "--"
    if str(value) == "1":
        return "active"
    return _stringify(value) or "--"


def _extract_uptime(raw: dict[str, Any]) -> str:
    candidates = [
        _find_value(raw, ["sysUpTimeSecsNoJump"]),
        _find_value(raw, ["sysUpTime"]),
        _find_value(raw, ["uptime"]),
    ]
    for candidate in candidates:
        parsed = _safe_int(candidate)
        if parsed is not None:
            return f"{parsed}s"
        if candidate not in (None, ""):
            return _stringify(candidate)
    return "--"


def _extract_version(raw: dict[str, Any]) -> str:
    for candidate in (
        _find_value(raw, ["pkgVersion"]),
        _find_value(raw, ["version"]),
        _find_value(raw, ["swVersion"]),
    ):
        if candidate not in (None, ""):
            return _stringify(candidate)
    return "--"


def _extract_cpu(raw: dict[str, Any]) -> float | int | None:
    core_values = _find_list(raw, ["cpuCoreUtil"])
    parsed = [_safe_float(item) for item in core_values]
    usable = [item for item in parsed if item is not None]
    if usable:
        average = sum(usable) / len(usable)
        return round(average, 1)

    direct = _find_value(raw, ["cpu", "cpuUtil", "cpuPct"])
    parsed_direct = _safe_float(direct)
    if parsed_direct is None:
        return None
    return round(parsed_direct, 1)


def _extract_memory(raw: dict[str, Any]) -> int | None:
    for candidate in (
        _find_value(raw, ["procMem"]),
        _find_value(raw, ["memory"]),
        _find_value(raw, ["mem"]),
    ):
        parsed = _safe_int(candidate)
        if parsed is not None:
            return parsed
    return None


def normalize_admin_info(raw: dict[str, Any], *, node_id: int | None = None, timestamp: str | None = None) -> dict[str, Any]:
    if not raw:
        logger.warning("bwvStats adminInfo section missing for node %s", node_id)

    snapshot = {
        "node_id": node_id,
        "timestamp": timestamp or _serialize_timestamp(),
        "status": _extract_status(raw),
        "uptime": _extract_uptime(raw),
        "version": _extract_version(raw),
        "cpu": _extract_cpu(raw),
        "memory": _extract_memory(raw),
        "raw": raw,
    }
    return snapshot


def _route_hits_section(raw: dict[str, Any]) -> list[Any]:
    route_hits = _find_list(raw, ["routeHits", "ipRouteHits", "routeHitStats"])
    if route_hits:
        return route_hits

    route_hits_dict = _find_dict(raw, ["routeHits", "ipRouteHits", "routeHitStats"])
    if route_hits_dict:
        return [{"prefix": key, "hit_count": value} for key, value in route_hits_dict.items()]

    logger.warning("bwvStats routeHits section missing")
    return []


def _normalize_route_hit_entry(entry: Any, *, node_id: int | None, timestamp: str) -> dict[str, Any] | None:
    if isinstance(entry, dict):
        prefix = _stringify(
            entry.get("prefix")
            or entry.get("route")
            or entry.get("subnet")
            or entry.get("network")
        )
        hit_count = _safe_int(entry.get("hit_count") or entry.get("hits") or entry.get("count"))
        is_null_route = (
            bool(entry.get("is_null_route"))
            or _stringify(entry.get("nextHop") or entry.get("next_hop")).lower() == "null"
            or prefix == "0.0.0.0/0"
        )
    else:
        text = _stringify(entry).strip()
        if not text:
            return None
        parts = text.split()
        prefix = parts[0] if parts else "--"
        if len(parts) > 1 and "/" not in prefix:
            mask = _safe_int(parts[1])
            if mask is not None:
                prefix = f"{prefix}/{mask}"
        hit_count = None
        for token in reversed(parts):
            parsed = _safe_int(token)
            if parsed is not None:
                hit_count = parsed
                break
        is_null_route = any(token.lower() == "null" for token in parts) or prefix == "0.0.0.0/0"

    if not prefix:
        return None

    return {
        "node_id": node_id,
        "timestamp": timestamp,
        "prefix": prefix or "--",
        "hit_count": hit_count or 0,
        "is_null_route": is_null_route,
        "raw": entry,
    }


def normalize_route_hits(raw: dict[str, Any], *, node_id: int | None = None, timestamp: str | None = None) -> list[dict[str, Any]]:
    route_hits = _route_hits_section(raw)
    normalized: list[dict[str, Any]] = []
    ts = timestamp or _serialize_timestamp()

    for entry in route_hits:
        try:
            parsed = _normalize_route_hit_entry(entry, node_id=node_id, timestamp=ts)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed parsing routeHits entry for node %s: %s", node_id, exc)
            continue
        if parsed:
            normalized.append(parsed)

    return normalized


def _qos_section(raw: dict[str, Any]) -> list[Any]:
    direct = _find_list(raw, ["qos", "qosStats", "qosClasses"])
    if direct:
        return direct

    qos_in = _find_list(raw, ["qosInByteRate"])
    qos_out = _find_list(raw, ["qosOutByteRate"])
    if qos_in or qos_out:
        max_len = max(len(qos_in), len(qos_out))
        rows = []
        for index in range(max_len):
            rows.append(
                {
                    "class_id": index,
                    "tx_bytes": qos_out[index] if index < len(qos_out) else None,
                    "rx_bytes": qos_in[index] if index < len(qos_in) else None,
                }
            )
        return rows

    logger.warning("bwvStats qos section missing")
    return []


def _flatten_numeric_total(value: Any) -> int:
    if isinstance(value, list):
        total = 0
        for item in value:
            total += _flatten_numeric_total(item)
        return total
    parsed = _safe_int(value)
    return parsed or 0


def _normalize_qos_entry(
    entry: Any,
    *,
    node_id: int | None,
    timestamp: str,
    class_index: int,
) -> dict[str, Any]:
    if isinstance(entry, dict):
        dscp = entry.get("dscp")
        class_id = entry.get("class_id", class_index)
        class_name = entry.get("class_name") or entry.get("name") or entry.get("label")
        tx_packets = _safe_int(entry.get("tx_packets") or entry.get("packets"))
        tx_bytes = _flatten_numeric_total(entry.get("tx_bytes") or entry.get("bytes"))
        drops = _safe_int(entry.get("drops"))
    else:
        class_id = class_index
        class_name = None
        dscp = None
        tx_packets = None
        tx_bytes = _flatten_numeric_total(entry)
        drops = None

    return {
        "node_id": node_id,
        "timestamp": timestamp,
        "dscp": _safe_int(dscp) if dscp is not None else None,
        "class_id": _safe_int(class_id) if _safe_int(class_id) is not None else class_index,
        "class_name": _stringify(class_name) or "--",
        "tx_packets": tx_packets,
        "tx_bytes": tx_bytes,
        "drops": drops,
        "raw": entry,
    }


def normalize_qos(raw: dict[str, Any], *, node_id: int | None = None, timestamp: str | None = None) -> list[dict[str, Any]]:
    qos_entries = _qos_section(raw)
    normalized: list[dict[str, Any]] = []
    ts = timestamp or _serialize_timestamp()

    for index, entry in enumerate(qos_entries):
        try:
            normalized.append(_normalize_qos_entry(entry, node_id=node_id, timestamp=ts, class_index=index))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed parsing qos entry %s for node %s: %s", index, node_id, exc)

    return normalized


def build_bwvstats_phase1_example(
    *,
    node_id: int,
    timestamp: str,
    admin_info: dict[str, Any],
    route_hits: list[dict[str, Any]],
    qos_stats: list[dict[str, Any]],
) -> dict[str, Any]:
    top_routes = sorted(route_hits, key=lambda item: item.get("hit_count", 0), reverse=True)[:5]
    qos_summary = sorted(qos_stats, key=lambda item: item.get("tx_bytes", 0), reverse=True)[:5]

    return {
        "node_id": node_id,
        "timestamp": timestamp,
        "admin_summary": {
            "status": admin_info.get("status"),
            "uptime": admin_info.get("uptime"),
            "version": admin_info.get("version"),
            "cpu": admin_info.get("cpu"),
            "memory": admin_info.get("memory"),
        },
        "top_route_hits": [
            {
                "prefix": route.get("prefix"),
                "hit_count": route.get("hit_count"),
                "is_null_route": route.get("is_null_route"),
            }
            for route in top_routes
        ],
        "qos_summary": [
            {
                "class_id": item.get("class_id"),
                "class_name": item.get("class_name"),
                "tx_bytes": item.get("tx_bytes"),
                "drops": item.get("drops"),
            }
            for item in qos_summary
        ],
    }


async def collect_bwvstats_phase1(node: Node, *, emit_logs: bool = False) -> dict[str, Any]:
    timestamp = _serialize_timestamp()
    request_params = dict(BWVSTATS_PHASE1_ARGS)
    result = await get_bwv_stats(node, extra_args=request_params, emit_logs=emit_logs)
    raw_json = result.get("raw") if result.get("status") == "ok" and isinstance(result.get("raw"), dict) else {}

    raw_snapshot = capture_raw_bwvstats_poll(
        node_id=node.id,
        request_params=request_params,
        raw_json=raw_json,
        timestamp=timestamp,
    )

    if result.get("status") != "ok":
        return {
            "status": "error",
            "message": result.get("message", "Unable to collect bwvStats phase 1 data"),
            "node_id": node.id,
            "timestamp": timestamp,
            "request_params": request_params,
            "raw_snapshot": raw_snapshot,
            "error": result.get("error"),
        }

    admin_info = normalize_admin_info(raw_json, node_id=node.id, timestamp=timestamp)
    route_hits = normalize_route_hits(raw_json, node_id=node.id, timestamp=timestamp)
    qos_stats = normalize_qos(raw_json, node_id=node.id, timestamp=timestamp)
    example = build_bwvstats_phase1_example(
        node_id=node.id,
        timestamp=timestamp,
        admin_info=admin_info,
        route_hits=route_hits,
        qos_stats=qos_stats,
    )

    logger.info("bwvStats phase 1 example for node %s: %s", node.id, json.dumps(example))

    return {
        "status": "ok",
        "node_id": node.id,
        "timestamp": timestamp,
        "request_params": request_params,
        "raw_snapshot": raw_snapshot,
        "node_stats_snapshot": admin_info,
        "route_hit_stats": route_hits,
        "qos_stats": qos_stats,
        "example_output": example,
    }
