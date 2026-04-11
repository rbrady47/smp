from __future__ import annotations

from typing import Any

from app.schemas import MainDashboardNodeSummary, MainDashboardNodeWatchlistPayload


def _normalize_watchlist_status(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "").strip().lower()
    if status in {"healthy", "degraded", "offline"}:
        return status

    ping_is_up = str(row.get("ping") or "").strip().lower() == "up"
    web_ok = bool(row.get("web_ok"))
    ssh_ok = bool(row.get("ssh_ok"))
    if web_ok and ssh_ok:
        return "healthy"
    if web_ok or ssh_ok or ping_is_up:
        return "degraded"
    return "offline"


def build_node_watchlist_payload(node_dashboard_payload: dict[str, Any], pin_keys: list[str]) -> dict[str, Any]:
    normalized_keys = [str(value).strip() for value in pin_keys if str(value).strip()]
    pin_order = {pin_key: index for index, pin_key in enumerate(normalized_keys)}
    all_rows = [
        *(node_dashboard_payload.get("anchors") or []),
        *(node_dashboard_payload.get("discovered") or []),
    ]

    projected_rows = []
    for row in all_rows:
        if not isinstance(row, dict):
            continue
        pin_key = str(row.get("pin_key") or "").strip()
        if pin_key not in pin_order:
            continue
        projected_rows.append(
            MainDashboardNodeSummary.model_validate(
                {
                    "pin_key": pin_key,
                    "row_type": str(row.get("row_type") or "anchor"),
                    "id": int(row.get("id")) if row.get("id") is not None else None,
                    "name": str(row.get("name") or "").strip() or None,
                    "site_id": str(row.get("site_id") or ""),
                    "site_name": str(row.get("site_name") or row.get("name") or ""),
                    "host": str(row.get("host") or "--"),
                    "site": str(row.get("site") or row.get("location") or "").strip() or None,
                    "status": _normalize_watchlist_status(row),
                    "web_ok": bool(row.get("web_ok")),
                    "ssh_ok": bool(row.get("ssh_ok")),
                    "ping_ok": bool(row.get("ping_ok") or str(row.get("ping") or "").strip().lower() == "up"),
                    "ping_state": str(row.get("ping_state") or ("good" if str(row.get("ping") or "").strip().lower() == "up" else "down")),
                    "latency_ms": int(row.get("latency_ms")) if isinstance(row.get("latency_ms"), int) else None,
                    "tx_display": str(row.get("tx_display") or "--"),
                    "rx_display": str(row.get("rx_display") or "--"),
                    "unit": str(row.get("unit") or "--"),
                    "location": str(row.get("location") or row.get("site") or "--"),
                    "version": str(row.get("version") or "--"),
                    "sites_up": row.get("sites_up", "--"),
                    "sites_total": row.get("sites_total", "--"),
                    "cpu_avg": float(row.get("cpu_avg")) if isinstance(row.get("cpu_avg"), (int, float)) else None,
                    "detail_url": str(row.get("detail_url") or ""),
                    "web_port": int(row.get("web_port")) if isinstance(row.get("web_port"), int) else 443,
                    "ssh_port": int(row.get("ssh_port")) if isinstance(row.get("ssh_port"), int) else 22,
                    "web_scheme": str(row.get("web_scheme") or "http"),
                    "ssh_username": str(row.get("ssh_username") or "").strip() or None,
                    "last_seen": str(row.get("last_seen") or "").strip() or None,
                }
            ).model_dump()
        )

    projected_rows.sort(key=lambda row: pin_order.get(str(row.get("pin_key") or ""), 999999))

    return MainDashboardNodeWatchlistPayload.model_validate({"nodes": projected_rows}).model_dump()
