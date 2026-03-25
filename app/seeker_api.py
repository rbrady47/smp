from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Mapping
import urllib.parse

import httpx

from app.models import Node

logger = logging.getLogger(__name__)

SEEKER_API_VERIFY_TLS = False
SEEKER_API_TIMEOUT_SECONDS = 10.0


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_or_none(values: Any) -> Any | None:
    if isinstance(values, list) and values:
        return values[0]
    return None


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


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return str(value)
    return str(value)


def _format_rate(value: Any) -> str:
    parsed = _safe_float(value)
    if parsed is None:
        return "--"
    if parsed >= 1_000_000:
        return f"{parsed / 1_000_000:.1f} Mbps"
    if parsed >= 1_000:
        return f"{parsed / 1_000:.1f} Kbps"
    return f"{parsed:.0f} bps"


def _format_bitmap(value: Any) -> str:
    parsed = _safe_int(value)
    if parsed is None:
        return "--"
    return f"{parsed:04b}"


def _format_rtt_ms(value: Any) -> str:
    parsed = _safe_float(value)
    if parsed is None:
        return "--"
    milliseconds = parsed / 1000.0
    if milliseconds >= 10:
        return f"{milliseconds:.2f} ms"
    if milliseconds >= 1:
        return f"{milliseconds:.3f} ms"
    return f"{milliseconds:.3f} ms"


def _decode_tunnel_bits(value: Any) -> list[bool]:
    parsed = _safe_int(value) or 0
    bit_order = [1, 2, 4, 8]
    return [bool(parsed & bit) for bit in bit_order]


def _parse_enabled_mask(value: Any) -> int:
    if isinstance(value, list):
        parts = [str(item).strip() for item in value]
    else:
        text = _stringify(value)
        parts = [part.strip() for part in text.split(",")] if text else []
    mask = 0
    bit_order = [1, 2, 4, 8]
    for index, bit in enumerate(bit_order):
        part = parts[index] if index < len(parts) else "0"
        if part == "1":
            mask |= bit
    return mask


def _normalize_version(value: Any) -> str:
    text = _stringify(value).strip()
    if not text:
        return "--"
    text = re.split(r",?\s*Built:\s*.+$", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    text = re.split(r",?\s*Build\s+.+$", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return text or "--"


def _normalize_license_date(value: Any) -> str:
    text = _stringify(value).strip()
    if not text:
        return "--"
    match = re.search(r"(\d{4}/\d{2}/\d{2})", text)
    if match:
        return match.group(1)
    return text


def _format_prefix(prefix: Any, mask: Any) -> str:
    prefix_text = _stringify(prefix).strip()
    mask_value = _safe_int(mask)
    if not prefix_text:
        return "--"
    if mask_value is None:
        return prefix_text
    return f"{prefix_text}/{mask_value}"


def _extract_disabled_site_indexes(data: dict[str, Any]) -> set[int]:
    for obj in _iter_objects(data):
        if not isinstance(obj, dict):
            continue
        raw = obj.get("siteDisable")
        if raw is None:
            continue
        parts = [part.strip() for part in _stringify(raw).split(",")]
        return {index for index, part in enumerate(parts) if part == "1"}
    return set()


def _build_tunnel_health(local_value: Any, remote_value: Any, configured_mask: int = 0b1111) -> list[str]:
    local_bits = _decode_tunnel_bits(local_value)
    remote_bits = _decode_tunnel_bits(remote_value)
    configured_bits = _decode_tunnel_bits(configured_mask)
    statuses: list[str] = []

    for configured, local_on, remote_on in zip(configured_bits, local_bits, remote_bits):
        if not configured:
            statuses.append("off")
        elif local_on and remote_on:
            statuses.append("up")
        elif local_on != remote_on:
            statuses.append("mismatch")
        else:
            statuses.append("down")

    return statuses


def _get_indexed(values: Any, index: int) -> Any | None:
    if isinstance(values, list) and 0 <= index < len(values):
        return values[index]
    if isinstance(values, str):
        parts = [part.strip() for part in values.split(",")]
        if 0 <= index < len(parts):
            return parts[index]
    return None


def _normalize_site_name(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _build_base_url(node: Node) -> str:
    scheme = "https" if node.api_use_https else "http"
    return f"{scheme}://{node.host}:{node.web_port}"


def _mask_value(key: str, value: Any) -> str:
    lowered = key.lower()
    if lowered in {"transid", "transidrefresh", "pass", "password"}:
        return "***"
    return _stringify(value)


def _safe_json_loads(text: str) -> dict[str, Any] | list[Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


async def login_to_seeker(
    node: Node,
    *,
    client: httpx.AsyncClient | None = None,
    emit_logs: bool = False,
) -> dict[str, Any]:
    if not node.api_username or not node.api_password:
        return {
            "status": "error",
            "rc": None,
            "message": "Node API credentials are not configured",
        }

    owns_client = client is None
    request_client = client
    if request_client is None:
        request_client = httpx.AsyncClient(
            base_url=_build_base_url(node),
            timeout=SEEKER_API_TIMEOUT_SECONDS,
            verify=SEEKER_API_VERIFY_TLS,
        )

    login_headers = {"Content-Type": "application/x-www-form-urlencoded"}
    login_body = (
        f"userName={urllib.parse.quote_plus(str(node.api_username))}"
        f"&pass={urllib.parse.quote_plus(str(node.api_password))}"
        "&isAjaxReq=1"
    )

    try:
        if emit_logs:
            logger.warning("BV login URL for node %s: %s/acct/login/", node.name, _build_base_url(node))
        response = await request_client.post("/acct/login/", headers=login_headers, content=login_body)
        response.raise_for_status()
        payload = _safe_json_loads(response.text)
        if emit_logs:
            logger.warning("BV login response body for node %s: %s", node.name, response.text)
        if not isinstance(payload, dict):
            return {
                "status": "error",
                "rc": None,
                "message": "Seeker login returned non-JSON",
                "error": {
                    "kind": "non_json",
                    "raw_response": response.text,
                },
            }
        if payload.get("rc") not in (None, 0, "0") or not payload.get("transId") or not payload.get("transIdRefresh"):
            return {
                "status": "error",
                "rc": payload.get("rc"),
                "message": "Seeker login failed",
                "raw": payload,
                "error": {
                    "kind": "login_failed",
                    "raw_response": response.text,
                },
            }
        return {
            "status": "ok",
            "rc": 0,
            "trans_id": payload.get("transId"),
            "trans_id_refresh": payload.get("transIdRefresh"),
            "raw": payload,
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "status": "error",
            "rc": None,
            "message": f"Seeker login failed: {exc}",
            "error": {
                "kind": "http_error",
                "raw_response": None,
            },
        }
    finally:
        if owns_client:
            await request_client.aclose()


async def _seeker_post_bwv(
    node: Node,
    data0: Mapping[str, Any],
    *,
    emit_logs: bool = False,
) -> dict[str, Any]:
    async with httpx.AsyncClient(
        base_url=_build_base_url(node),
        timeout=SEEKER_API_TIMEOUT_SECONDS,
        verify=SEEKER_API_VERIFY_TLS,
    ) as client:
        login_result = await login_to_seeker(node, client=client, emit_logs=emit_logs)
        if login_result.get("status") != "ok":
            return login_result

        serialized_data0 = json.dumps(dict(data0), separators=(",", ":"), ensure_ascii=True)
        form_fields = {
            "userName": str(node.api_username),
            "transId": str(login_result["trans_id"]),
            "transIdRefresh": str(login_result["trans_id_refresh"]),
            "isAjaxReq": "1",
            "reqType": "bwv",
            "data0": serialized_data0,
        }
        encoded_body = urllib.parse.urlencode(form_fields)
        headers = {
            "User-Agent": "curl/7.55.1",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            if emit_logs:
                safe_fields = {key: _mask_value(key, value) for key, value in form_fields.items() if key != "data0"}
                logger.warning("BV request URL for node %s: %s/acct/", node.name, _build_base_url(node))
                logger.warning("BV request form fields for node %s: %s", node.name, safe_fields)
                logger.warning("BV request data0 for node %s: %s", node.name, serialized_data0)

            response = await client.post("/acct/", headers=headers, content=encoded_body)
            response.raise_for_status()
            response_text = response.text
            payload = _safe_json_loads(response_text)
            if not isinstance(payload, dict):
                return {
                    "status": "error",
                    "rc": None,
                    "message": "Seeker returned non-JSON",
                    "error": {
                        "kind": "non_json",
                        "raw_response": response_text,
                    },
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            rc = payload.get("rc")
            if rc not in (None, 0, "0"):
                if emit_logs:
                    logger.warning("BV raw response for node %s rc=%s: %s", node.name, rc, response_text)
                return {
                    "status": "error",
                    "rc": _safe_int(rc) if _safe_int(rc) is not None else rc,
                    "message": f"Seeker request failed (rc={rc})",
                    "raw": payload,
                    "error": {
                        "kind": "rc_error",
                        "raw_response": response_text,
                    },
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            return {
                "status": "ok",
                "rc": 0,
                "raw": payload,
                "error": None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        except httpx.HTTPError as exc:
            return {
                "status": "error",
                "rc": None,
                "message": f"Seeker request failed: {exc}",
                "error": {
                    "kind": "http_error",
                    "raw_response": None,
                },
            }


async def get_bwv_stats(
    node: Node,
    *,
    extra_args: dict[str, Any] | None = None,
    emit_logs: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reqType": "bwvStats",
    }
    if extra_args:
        payload.update(extra_args)
    return await _seeker_post_bwv(node, payload, emit_logs=emit_logs)


async def get_bwv_cfg(node: Node, *, emit_logs: bool = False) -> dict[str, Any]:
    return await _seeker_post_bwv(node, {"reqType": "bwvCfg"}, emit_logs=emit_logs)


def normalize_bwv_stats(data: dict[str, Any]) -> dict[str, Any]:
    cpu = data.get("cpuCoreUtil", [])
    cpu_values = []
    if isinstance(cpu, list):
        for item in cpu:
            parsed = _safe_int(item)
            if parsed is not None:
                cpu_values.append(parsed)

    cpu_avg = sum(cpu_values) / len(cpu_values) if cpu_values else None
    return {
        "latency_ms": _safe_int(_first_or_none(data.get("chanWanDelay", []))),
        "tx_bps": _safe_int(data.get("txTotRateIf")) or 0,
        "rx_bps": _safe_int(data.get("rxTotRateIf")) or 0,
        "cpu_avg": cpu_avg,
        "discovered_sites": _safe_int(data.get("nDiscSites")) or 0,
        "is_active": data.get("isActive") == "1",
    }


def normalize_bwv_cfg(data: dict[str, Any], node: Node | None = None) -> dict[str, Any]:
    mgmt_ip = _find_value(data, ["mgmtIp", "managementIp", "mgmtAddr"])
    site_id = _find_value(data, ["siteId", "siteID", "localSiteId"])
    site_name = _find_value(data, ["siteName", "name", "localSiteName"])
    enclave_id = _find_value(data, ["enclaveId", "encId", "l3vpnId"])
    node_type = _find_value(data, ["nodeType", "platformType", "deviceType"])
    platform_name = _find_value(data, ["platform", "platformName", "hwPlatform"])
    version = _find_value(data, ["pkgVersion", "version", "swVersion"])
    license_block = data.get("lic") if isinstance(data.get("lic"), dict) else {}
    license_expires = _find_value(license_block, ["Expires"]) or _find_value(
        data, ["licenseExpires", "licenseExpiration", "licExpires"]
    )
    interfaces = _find_list(data, ["ethIf", "ethIfs", "interfaces", "ethernetInterfaces"])
    n_mates = _find_value(data, ["nMates", "numMates", "mateCount"])
    mates = extract_mates_from_cfg(data)
    if n_mates is None:
        n_mates = len(mates)

    return {
        "site_id": _stringify(site_id) or "--",
        "site_name": _normalize_site_name(site_name, node.location if node else "--"),
        "mgmt_ip": _stringify(mgmt_ip) or (node.host if node else "--"),
        "n_mates": _safe_int(n_mates) or len(mates),
        "enclave_id": _stringify(enclave_id) or "--",
        "interfaces": [_stringify(item) for item in interfaces],
        "node_type": _stringify(node_type) or "--",
        "platform": _stringify(platform_name) or "--",
        "version": _normalize_version(version),
        "license_expires": _normalize_license_date(license_expires),
    }


def extract_mates_from_cfg(data: dict[str, Any]) -> list[dict[str, Any]]:
    mates = []
    disabled_indexes = _extract_disabled_site_indexes(data)
    raw_mates = _find_list(data, ["mates", "mateCfg", "mateConfigs", "mateConfig"])

    if raw_mates:
        for index, mate in enumerate(raw_mates):
            if not isinstance(mate, dict):
                continue
            mates.append(
                {
                    "mate_index": index,
                    "mate_site_id": _stringify(
                        mate.get("siteId") or mate.get("mateSiteId") or mate.get("remoteSiteId")
                    )
                    or "--",
                    "mate_ip": _stringify(
                        mate.get("ip") or mate.get("mateIp") or mate.get("mgmtIp") or mate.get("addr")
                    )
                    or "--",
                    "mode": _stringify(mate.get("mode") or mate.get("txMode") or mate.get("actTxMode")) or "--",
                    "enclave": _stringify(mate.get("enclaveId") or mate.get("l3vpn") or mate.get("notes")) or "--",
                    "configured_mask": 0b1111,
                    "disabled": index in disabled_indexes,
                }
            )

    if mates:
        return mates

    flat_candidates = []
    for obj in _iter_objects(data):
        if not isinstance(obj, dict):
            continue
        for key, value in obj.items():
            key_text = str(key)
            if key_text == "mate" or (key_text.startswith("mate") and key_text[4:].isdigit()):
                flat_candidates.append((key_text, value, obj))

    if flat_candidates:
        def mate_sort_key(item: tuple[str, Any, dict[str, Any]]) -> int:
            key_text = item[0]
            if key_text == "mate":
                return 0
            return int(key_text[4:])

        for key_text, value, owner in sorted(flat_candidates, key=mate_sort_key):
            raw_mate = _stringify(value)
            parts = raw_mate.split(":")
            mate_ip = parts[0] if len(parts) > 0 else "--"
            mate_site_id = parts[1] if len(parts) > 1 else "--"
            enclave = parts[4] if len(parts) > 4 else "--"
            mate_index = 0 if key_text == "mate" else int(key_text[4:])
            tx_mode_key = "txMode" if key_text == "mate" else f"txMode{key_text[4:]}"
            suffix = "0" if key_text == "mate" else key_text[4:]
            en_tx_key = f"en_tx{suffix}"
            en_rx_key = f"en_rx{suffix}"
            configured_mask = _parse_enabled_mask(owner.get(en_tx_key)) | _parse_enabled_mask(owner.get(en_rx_key))
            mates.append(
                {
                    "mate_index": mate_index,
                    "mate_site_id": mate_site_id,
                    "mate_ip": mate_ip,
                    "mode": _stringify(owner.get(tx_mode_key)) or "--",
                    "enclave": enclave,
                    "configured_mask": configured_mask,
                    "disabled": mate_index in disabled_indexes,
                }
            )

    if mates:
        return mates

    discovered = _find_list(data, ["discSites", "sites"])
    for index, item in enumerate(discovered):
        parts = _stringify(item).split()
        mate_site_id = parts[0] if parts else "--"
        mate_ip = parts[1] if len(parts) > 1 else "--"
        mates.append(
            {
                "mate_index": index,
                "mate_site_id": mate_site_id,
                "mate_ip": mate_ip,
                "mode": "--",
                "enclave": "--",
                "configured_mask": 0b1111,
                "disabled": index in disabled_indexes,
            }
        )
    return mates


def extract_static_routes_from_cfg(data: dict[str, Any]) -> list[dict[str, Any]]:
    routes = []
    raw_routes = _find_list(data, ["ipRoutes", "staticRoutes", "routes", "routeCfg"])
    for route in raw_routes:
        if isinstance(route, dict):
            routes.append(
                {
                    "prefix": _stringify(route.get("prefix") or route.get("subnet") or route.get("network")) or "--",
                    "name": _stringify(route.get("name") or route.get("subnetName") or route.get("label")) or "--",
                    "next_hop": _stringify(route.get("nextHop") or route.get("gateway")) or "--",
                    "site_id": _stringify(route.get("siteId") or route.get("sid")) or "--",
                    "type": "static",
                    "metric": _stringify(route.get("metric") or route.get("cost")) or "--",
                }
            )
        else:
            tokens = _stringify(route).split()
            prefix = _format_prefix(tokens[0] if len(tokens) > 0 else None, tokens[1] if len(tokens) > 1 else None)
            next_hop = tokens[3] if len(tokens) > 3 and tokens[3].lower() != "null" else "--"
            metric = tokens[4] if len(tokens) > 4 else "--"
            name = tokens[-1] if tokens else "--"
            routes.append(
                {
                    "prefix": prefix,
                    "name": name,
                    "next_hop": next_hop,
                    "site_id": "--",
                    "type": "static",
                    "metric": metric,
                }
            )
    return routes


def extract_learnt_routes_from_stats(data: dict[str, Any]) -> list[dict[str, Any]]:
    routes = []
    raw_routes = _find_list(data, ["learntIpRoutes", "learntRoutes", "learnedRoutes", "routesLearnt"])
    subnet_names = data.get("subnetNames") if isinstance(data.get("subnetNames"), dict) else {}
    for index, route in enumerate(raw_routes):
        if isinstance(route, dict):
            routes.append(
                {
                    "prefix": _stringify(route.get("prefix") or route.get("subnet") or route.get("network")) or "--",
                    "name": _stringify(route.get("name") or route.get("subnetName") or subnet_names.get(f"n{index}")) or "--",
                    "next_hop": _stringify(route.get("nextHop") or route.get("gateway")) or "--",
                    "site_id": _stringify(route.get("siteId") or route.get("sid")) or "--",
                    "type": "dynamic",
                    "metric": _stringify(route.get("metric") or route.get("cost")) or "--",
                }
            )
        else:
            tokens = _stringify(route).split()
            sid = "--"
            metric = "--"
            if "sid" in tokens:
                sid_index = tokens.index("sid")
                if sid_index + 1 < len(tokens):
                    sid = tokens[sid_index + 1]
                if sid_index + 3 < len(tokens):
                    metric = tokens[sid_index + 3]
            routes.append(
                {
                    "prefix": _format_prefix(tokens[0] if len(tokens) > 0 else None, tokens[1] if len(tokens) > 1 else None),
                    "name": _stringify(subnet_names.get(f"n{index}")) or "--",
                    "next_hop": tokens[2] if len(tokens) > 2 else "--",
                    "site_id": sid,
                    "type": "dynamic",
                    "metric": metric,
                }
            )
    return routes


def extract_tunnels_from_stats(data: dict[str, Any], mates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mates_by_index = {
        _safe_int(mate.get("mate_index")): mate
        for mate in mates
        if _safe_int(mate.get("mate_index")) is not None
    }
    tunnel_count = max(
        len(_as_list(data.get("rxTunnelLock"))),
        len(_as_list(data.get("matePingOk"))),
        len(_as_list(data.get("matePingRTT"))),
        len(_as_list(data.get("txRate"))),
        len(_as_list(data.get("rxRate"))),
        len(_as_list(data.get("wrState"))),
        len(_as_list(data.get("rdState"))),
    )
    feedback_values = [part.strip() for part in _stringify(data.get("mateTunnelFeedback")).split(",") if part.strip()]
    act_tx_modes = [part.strip() for part in _stringify(data.get("actTxMode")).split(",") if part.strip()]
    tunnels = []

    for index in range(tunnel_count):
        mate = mates_by_index.get(index, {})
        if mate.get("disabled"):
            continue
        tx_rate = _get_indexed(data.get("txRate"), index)
        rx_rate = _get_indexed(data.get("rxRate"), index)
        ping_up = str(_get_indexed(data.get("matePingOk"), index)) == "1"
        tunnel_health = _build_tunnel_health(
            _get_indexed(data.get("rxTunnelLock"), index),
            feedback_values[index] if index < len(feedback_values) else None,
            mate.get("configured_mask", 0b1111),
        )
        enabled_tunnel_states = [status for status in tunnel_health if status != "off"]
        all_enabled_tunnels_down = bool(enabled_tunnel_states) and all(
            status == "down" for status in enabled_tunnel_states
        )
        suppress_runtime_metrics = (not ping_up) and all_enabled_tunnels_down
        tunnels.append(
            {
                "mate_index": index,
                "site_name": f"Site {mate.get('mate_site_id', index)}",
                "mate_site_id": mate.get("mate_site_id", f"Mate {index}"),
                "mate_ip": mate.get("mate_ip", "--"),
                "tunnel_up_bitmap": _format_bitmap(_get_indexed(data.get("rxTunnelLock"), index)),
                "feedback_bitmap": _format_bitmap(feedback_values[index] if index < len(feedback_values) else None),
                "tunnel_health": tunnel_health,
                "tx_rate": "--"
                if suppress_runtime_metrics
                else _format_rate(_first_or_none(tx_rate) if isinstance(tx_rate, list) else tx_rate),
                "rx_rate": "--"
                if suppress_runtime_metrics
                else _format_rate(_first_or_none(rx_rate) if isinstance(rx_rate, list) else rx_rate),
                "rtt_ms": "--" if suppress_runtime_metrics else _format_rtt_ms(_get_indexed(data.get("matePingRTT"), index)),
                "ping": "Up" if ping_up else "Down",
                "wr_state": _stringify(_get_indexed(data.get("wrState"), index)) or "--",
                "rd_state": _stringify(_get_indexed(data.get("rdState"), index)) or "--",
            }
        )

    return tunnels


def extract_channels_from_stats(data: dict[str, Any]) -> list[dict[str, Any]]:
    channel_count = max(
        len(_as_list(data.get("wanUp"))),
        len(_as_list(data.get("chanWanDelay"))),
        len(_as_list(data.get("txChanRate"))),
        len(_as_list(data.get("rxChanRate"))),
        len(_as_list(data.get("chanPublicIp"))),
    )
    channels = []
    for index in range(channel_count):
        channels.append(
            {
                "channel": index,
                "wan_up": "Up" if str(_get_indexed(data.get("wanUp"), index)) == "1" else "Down",
                "wan_delay_ms": _get_indexed(data.get("chanWanDelay"), index) or "--",
                "public_ip": _get_indexed(data.get("chanPublicIp"), index) or "--",
                "tx_rate": _format_rate(_get_indexed(data.get("txChanRate"), index)),
                "rx_rate": _format_rate(_get_indexed(data.get("rxChanRate"), index)),
                "link_state": _get_indexed(data.get("ethIfLink"), index) if _get_indexed(data.get("ethIfLink"), index) is not None else "--",
            }
        )
    return channels


def extract_active_sites_from_stats(data: dict[str, Any], mates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mates_by_index = {
        _safe_int(mate.get("mate_index")): mate
        for mate in mates
        if _safe_int(mate.get("mate_index")) is not None
    }
    discovered_sites = _find_list(data, ["discSites", "sites"])
    discovered_map: dict[str, dict[str, str]] = {}
    for index, item in enumerate(discovered_sites):
        parts = _stringify(item).split()
        if not parts:
            continue
        site_id = parts[0]
        discovered_map[site_id] = {
            "site_id": site_id,
            "ip": parts[1] if len(parts) > 1 else "--",
            "state": parts[2] if len(parts) > 2 else "--",
            "mask": parts[3] if len(parts) > 3 else "--",
            "name": f"Site {site_id}",
            "index_hint": str(index),
        }

    active_site_count = max(
        len(mates),
        len(_as_list(data.get("matePingOk"))),
        len(_as_list(data.get("matePingRTT"))),
        len(_as_list(data.get("txRate"))),
        len(_as_list(data.get("rxRate"))),
        len(_as_list(data.get("rxTunnelLock"))),
    )
    feedback_values = [part.strip() for part in _stringify(data.get("mateTunnelFeedback")).split(",") if part.strip()]
    tx_modes = [part.strip() for part in _stringify(data.get("actTxMode")).split(",") if part.strip()]
    active_sites: list[dict[str, Any]] = []

    for index in range(active_site_count):
        mate = mates_by_index.get(index, {})
        if mate.get("disabled"):
            continue
        site_id = _stringify(mate.get("mate_site_id")) or _stringify(index)
        discovered = discovered_map.get(site_id, {})
        tx_rate = _get_indexed(data.get("txRate"), index)
        rx_rate = _get_indexed(data.get("rxRate"), index)
        ping_ok = str(_get_indexed(data.get("matePingOk"), index)) == "1"
        ping_rtt = _get_indexed(data.get("matePingRTT"), index)

        active_sites.append(
            {
                "site_index": index,
                "site_id": site_id or "--",
                "site_name": discovered.get("name") or f"Site {site_id}",
                "site_ip": mate.get("mate_ip") or discovered.get("ip") or "--",
                "ping": "Up" if ping_ok else "Down",
                "ping_rtt_ms": f"{ping_rtt} ms" if ping_rtt not in (None, "", "--") else "--",
                "tx_rate": _format_rate(_first_or_none(tx_rate) if isinstance(tx_rate, list) else tx_rate),
                "rx_rate": _format_rate(_first_or_none(rx_rate) if isinstance(rx_rate, list) else rx_rate),
                "tunnel_bitmap": _format_bitmap(_get_indexed(data.get("rxTunnelLock"), index)),
                "feedback_bitmap": _format_bitmap(feedback_values[index] if index < len(feedback_values) else None),
                "tx_mode": tx_modes[index] if index < len(tx_modes) else "--",
                "notes": f"Enclave {mate.get('enclave', '--')} · L3 {discovered.get('mask', '--')} · State {discovered.get('state', '--')}",
            }
        )

    return active_sites


def build_detail_payload(
    node: Node,
    *,
    node_health: dict[str, Any],
    cfg_result: dict[str, Any] | None,
    stats_result: dict[str, Any] | None,
    learnt_routes_result: dict[str, Any] | None,
) -> dict[str, Any]:
    cfg_raw = cfg_result.get("raw") if cfg_result and cfg_result.get("status") == "ok" else {}
    stats_raw = stats_result.get("raw") if stats_result and stats_result.get("status") == "ok" else {}
    learnt_raw = learnt_routes_result.get("raw") if learnt_routes_result and learnt_routes_result.get("status") == "ok" else {}

    cfg_summary = normalize_bwv_cfg(cfg_raw, node)
    normalized_stats = normalize_bwv_stats(stats_raw)
    mates = extract_mates_from_cfg(cfg_raw)
    static_routes = extract_static_routes_from_cfg(cfg_raw)
    learnt_routes = extract_learnt_routes_from_stats(learnt_raw)
    tunnels = extract_tunnels_from_stats(stats_raw, mates)
    channels = extract_channels_from_stats(stats_raw)
    active_sites = extract_active_sites_from_stats(stats_raw, mates)

    section_errors = {
        "config": None if cfg_result and cfg_result.get("status") == "ok" else (cfg_result or {}).get("message"),
        "stats": None if stats_result and stats_result.get("status") == "ok" else (stats_result or {}).get("message"),
        "routes": None
        if learnt_routes_result and learnt_routes_result.get("status") == "ok"
        else (learnt_routes_result or {}).get("message"),
    }

    summary = {
        "name": node.name,
        "host": node.host,
        "site": node.location,
        "status": node_health.get("status"),
        "web_ok": node_health.get("web_ok"),
        "ssh_ok": node_health.get("ssh_ok"),
        "ping_ok": node_health.get("ping_ok"),
        "latency_ms": node_health.get("latency_ms"),
        "tx_bps": normalized_stats.get("tx_bps", 0),
        "rx_bps": normalized_stats.get("rx_bps", 0),
        "site_count": normalized_stats.get("discovered_sites", len(mates)),
        "mate_count": len([mate for mate in mates if not mate.get("disabled")]),
        "active_site_count": len(active_sites),
        "wan_count": len(channels),
        "cpu_avg": normalized_stats.get("cpu_avg"),
        "version": cfg_summary.get("version", "--"),
        "health_state": "Healthy" if node_health.get("ping_ok") else "Degraded",
    }

    return {
        "node": {
            "id": node.id,
            "name": node.name,
            "host": node.host,
            "location": node.location,
            "status": node_health.get("status"),
            "web_ok": node_health.get("web_ok"),
            "ssh_ok": node_health.get("ssh_ok"),
            "last_refresh": node.last_checked.isoformat() if node.last_checked else None,
            "last_telemetry_pull": (stats_result or {}).get("fetched_at"),
        },
        "node_summary": summary,
        "config_summary": cfg_summary,
        "active_sites": active_sites,
        "tunnels": tunnels,
        "channels": channels,
        "static_routes": static_routes,
        "learnt_routes": learnt_routes,
        "errors": section_errors,
        "raw": {
            "bwv_cfg": cfg_raw if cfg_raw else {},
            "bwv_stats": stats_raw if stats_raw else {},
            "bwv_stats_learnt_routes": learnt_raw if learnt_raw else {},
        },
    }
