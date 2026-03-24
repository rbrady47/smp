def _first_or_none(values: object) -> object | None:
    if isinstance(values, list) and values:
        return values[0]
    return None


def _safe_int(value: object) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_bwv_stats(data: dict) -> dict:
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
