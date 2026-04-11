# Seeker API Reference

Complete inventory of all Seeker API calls SMP makes, the raw fields returned, and how SMP normalizes them.

Last updated: 2026-04-06

---

## Authentication

**Function:** `login_to_seeker()` in `app/seeker_api.py`

**Request:** POST to `/acct/login/` or `/acct/login`

```
Content-Type: application/x-www-form-urlencoded

userName={username}&pass={password}&isAjaxReq=1
```

**Response fields:**

| Field | Purpose |
|-------|---------|
| `rc` | Return code — must be `null`, `0`, or `"0"` for success |
| `transId` | Session token for subsequent requests |
| `transIdRefresh` | Refresh token for subsequent requests |

**Notes:**
- Base URL: `https://{host}:{web_port}` or `http://...` based on `api_use_https`
- Only the operator-configured scheme is tried (no fallback to the other scheme)
- Path candidates tried in order: `/acct/login/`, `/acct/login`

---

## Data Requests

All data requests use the same POST format with different `data0` payloads:

```
Content-Type: application/x-www-form-urlencoded
User-Agent: curl/7.55.1
X-Requested-With: XMLHttpRequest

userName={username}&transId={trans_id}&transIdRefresh={trans_id_refresh}&isAjaxReq=1&reqType=bwv&data0={json_payload}
```

Path candidates tried in order: `/acct/`, `/acct`

TLS verification is disabled (`verify=False`) for self-signed Seeker certs.

---

## Request 1: Config (bwvCfg)

**data0:** `{"reqType":"bwvCfg"}`

**Function:** `get_bwv_cfg()` → `_seeker_post_bwv()`

**Normalized by:** `normalize_bwv_cfg()`

### Config Fields

| Raw Field (variants tried) | SMP Field | Notes |
|---|---|---|
| `siteId`, `siteID`, `localSiteId` | `site_id` | Local site identifier |
| `siteName`, `name`, `localSiteName` | `site_name` | Falls back to node.location |
| `mgmtIp`, `managementIp`, `mgmtAddr` | `mgmt_ip` | Falls back to node.host |
| `enclaveId`, `encId`, `l3vpnId` | `enclave_id` | Enclave/L3VPN ID |
| `nodeType`, `platformType`, `deviceType` | `node_type` | Platform type |
| `platform`, `platformName`, `hwPlatform` | `platform` | Hardware platform name |
| `pkgVersion`, `version`, `swVersion` | `version` | Normalized via `_normalize_version()` |
| `lic.Expires`, `licenseExpires`, `licenseExpiration`, `licExpires` | `license_expires` | Normalized via `_normalize_license_date()` |
| `ethIf`, `ethIfs`, `interfaces`, `ethernetInterfaces` | `interfaces` | List of interface names |
| `nMates`, `numMates`, `mateCount` | `n_mates` | Count; falls back to len(mates) |
| `siteDisable` | — | Comma-separated "0"/"1" for disabled mate indexes |

### Mate Extraction (`extract_mates_from_cfg`)

Mates are extracted in priority order:

1. **From list keys:** `mates`, `mateCfg`, `mateConfigs`, `mateConfig`
   - Per mate: `siteId`/`mateSiteId`/`remoteSiteId` → `mate_site_id`
   - `ip`/`mateIp`/`mgmtIp`/`addr` → `mate_ip`
   - `mode`/`txMode`/`actTxMode` → `mode`
   - `enclaveId`/`l3vpn`/`notes` → `enclave`
   - `en_tx{0-3}`, `en_rx{0-3}` (comma-separated mask) → `configured_mask`
   - Index in `siteDisable` → `disabled`

2. **From flat keys:** `mate`, `mate0`, `mate1`, `mate2`, `mate3`
   - Format: `"ip:siteId:::enclave"`
   - Also reads `txMode`, `txMode0`, etc. and `en_tx0`, `en_rx0`, etc.

3. **From discovery lists:** `discSites`, `sites`
   - Format per item: space-separated `"siteId ip..."`

### Static Routes Extraction (`extract_static_routes_from_cfg`)

From `ipRoutes`, `staticRoutes`, `routes`, `routeCfg` lists:

| Raw Field | SMP Field |
|---|---|
| `prefix`, `subnet`, `network` | `prefix` |
| `name`, `subnetName`, `label` | `name` |
| `nextHop`, `gateway` | `next_hop` |
| `siteId`, `sid` | `site_id` |
| `metric`, `cost` | `metric` |

Or parsed from space-separated format: `"prefix mask {skip} nextHop metric name"`

---

## Request 2: Stats (bwvStats)

**data0:** `{"reqType":"bwvStats"}`

**Function:** `get_bwv_stats()` → `_seeker_post_bwv()`

**Normalized by:** `normalize_bwv_stats()`

### Summary Stats

| Raw Field | SMP Field | Type | Unit Conversion |
|---|---|---|---|
| `txTotRateIf` | `tx_bps`, `wan_tx_bps` | int | **Bytes/s × 8 → bits/s** |
| `rxTotRateIf` | `rx_bps`, `wan_rx_bps` | int | **Bytes/s × 8 → bits/s** |
| `txChanRate` | `wan_tx_bps_channels` | list of ints | Sum × 8 (tunnel payload only) |
| `rxChanRate` | `wan_rx_bps_channels` | list of ints | Sum × 8 (tunnel payload only) |
| `userRate[0]` | `lan_tx_bps` | int | **Bytes/s × 8 → bits/s** |
| `userRate[1]` | `lan_rx_bps` | int | **Bytes/s × 8 → bits/s** |
| `totUserBytes[0]` | `lan_tx_total` | string | Pre-formatted ("284.1G") |
| `totUserBytes[1]` | `lan_rx_total` | string | Pre-formatted ("284.1G") |
| `totChanBytesTx` | `wan_tx_total` | list of strings | Summed via `_sum_seeker_bytes_list()` |
| `totChanBytesRx` | `wan_rx_total` | list of strings | Summed via `_sum_seeker_bytes_list()` |
| `cpuCoreUtil` | `cpu_avg` | list of ints | Average of per-core utilization % |
| `chanWanDelay[0]` | `latency_ms` | int | ms (first channel) |
| `nDiscSites` | `discovered_sites` | int | Count of discovered sites |
| `isActive` | `is_active` | "1"/"0" | "1" → True |

> **CRITICAL:** All Seeker rate fields are **Bytes per second**. Multiply by 8 for bits/s.
> `netIfSpeed: [100000]` = 100 Mbps confirms this — 100,000 Bytes/s × 8 = 800,000 bps ≠ 100 Mbps.
> Actually `netIfSpeed` is in kbps (100,000 kbps = 100 Mbps), but rate fields like `txTotRateIf` are Bytes/s.

### Tunnel Extraction (`extract_tunnels_from_stats`)

Per tunnel index (count derived from mate config):

| Raw Field | SMP Field | Notes |
|---|---|---|
| `matePingOk[index]` | `ping` | "1" → "Up", "0" → "Down" |
| `matePingRTT[index]` | `rtt_ms` | Microseconds → ms via `_format_rtt_ms()` |
| `txRate[index]` or `txRate[index][0]` | `tx_rate` | Bytes/s → formatted via `_format_rate()` |
| `rxRate[index]` or `rxRate[index][0]` | `rx_rate` | Bytes/s → formatted via `_format_rate()` |
| `rxTunnelLock[index]` | `tunnel_up_bitmap` | 4-bit binary format |
| `mateTunnelFeedback` (comma-sep) | `feedback_bitmap` | 4-bit binary format |
| `wrState[index]` | `wr_state` | Write state |
| `rdState[index]` | `rd_state` | Read state |
| Mate config `en_tx`/`en_rx` | `configured_mask` | Determines tunnel health per-tunnel |

Tunnel health computed by `_build_tunnel_health()`: combines local/remote lock bits + configured mask → `["up", "down", "off", "mismatch"]` per sub-tunnel.

### Channel Extraction (`extract_channels_from_stats`)

Per channel index:

| Raw Field | SMP Field | Notes |
|---|---|---|
| `wanUp[index]` | `wan_up` | "1" → "Up", else "Down" |
| `chanWanDelay[index]` | `wan_delay_ms` | WAN latency per channel |
| `chanPublicIp[index]` | `public_ip` | Public IP address |
| `txChanRate[index]` | `tx_rate` | Bytes/s → formatted via `_format_rate()` |
| `rxChanRate[index]` | `rx_rate` | Bytes/s → formatted via `_format_rate()` |
| `ethIfLink[index]` | `link_state` | Ethernet link state |

### Active Sites Extraction (`extract_active_sites_from_stats`)

Combines mate config with runtime stats:

| Raw Field | SMP Field | Notes |
|---|---|---|
| `discSites`/`sites` | discovered_map | Space-separated `"siteId ip state mask"` |
| `matePingOk[index]` | `ping` | Per-mate ping status |
| `matePingRTT[index]` | `ping_rtt_ms` | Per-mate RTT |
| `txRate[index]` | `tx_rate` | Per-mate TX rate |
| `rxRate[index]` | `rx_rate` | Per-mate RX rate |
| `rxTunnelLock[index]` | `tunnel_bitmap` | Tunnel lock bitmap |
| `mateTunnelFeedback[index]` | `feedback_bitmap` | Feedback bitmap |
| `actTxMode[index]` | `tx_mode` | Active TX mode |

---

## Request 3: Learnt Routes (bwvStats + flag)

**data0:** `{"reqType":"bwvStats","learntRoutes":"1"}`

**Function:** `get_bwv_stats(node, extra_args={"learntRoutes": "1"})`

**Extracted by:** `extract_learnt_routes_from_stats()`

From `learntIpRoutes`, `learntRoutes`, `learnedRoutes`, `routesLearnt` lists:

| Raw Field | SMP Field |
|---|---|
| `prefix`, `subnet`, `network` | `prefix` |
| `name`, `subnetName` | `name` (or from `subnetNames` dict) |
| `nextHop`, `gateway` | `next_hop` |
| `siteId`, `sid` | `site_id` |
| `metric`, `cost` | `metric` |

Type is always `"dynamic"`.

Or parsed from space-separated format with `sid` keyword: `"prefix mask nextHop ... sid {site_id} ... metric {metric}"`

---

## Bulk Fetch Optimization

**Function:** `seeker_fetch_all()` in `app/seeker_api.py`

Does **1 login + 3 sequential data requests** on the same `httpx.AsyncClient` session:

1. bwvCfg (config)
2. bwvStats (stats)
3. bwvStats + learntRoutes (learnt routes)

**Previously:** 3 separate logins (6 HTTP round-trips). **Now:** 4 round-trips. Eliminates concurrent login rate-limiting on the Seeker.

**Called by:** `load_node_detail()` in `app/pollers/seeker.py` — every 10 seconds per node.

---

## Remote Site Name Probe

**Function:** `resolve_site_name_map()` in `app/seeker_api.py`

**Purpose:** Resolve unknown tunnel-peer site names by probing the remote Seeker for its config.

**How it works:**
1. For each tunnel with unknown `site_name` and `ping == "up"`:
2. Creates a transient Node pointing at the peer's IP, using the anchor's credentials
3. Calls `get_bwv_cfg()` on the remote Seeker
4. Extracts `site_id` + `site_name` from remote config response
5. Caches result in `REMOTE_SITE_CFG_CACHE` keyed by `(site_ip, web_port, api_use_https, username)`

**Called by:** `site_name_resolution_loop()` in `app/pollers/seeker.py` — every 30 seconds (background, not on the critical poll path).

---

## Discovered Node Probes

**Function:** `probe_discovered_node_detail()` in `app/node_dashboard_backend.py`

**Purpose:** Probe discovered nodes (found via tunnel analysis) for their Seeker data.

**How it works:**
- Creates a transient Node with `host={site_ip}`, inheriting port/credentials from the anchor node
- Makes the same 3 requests: bwvCfg, bwvStats, bwvStats+learntRoutes
- Uses `get_bwv_cfg()` and `get_bwv_stats()` individually (not `seeker_fetch_all`)

**Called by:** `dn_seeker_polling_loop()` in `app/pollers/dn_seeker.py` — every 5 seconds.

---

## Normalization & Formatting Functions

| Function | Input | Output | Purpose |
|---|---|---|---|
| `_format_rate()` | Bytes/s | "X.X Mbps" or "--" | Convert Bytes/s × 8 → bits/s with unit |
| `_format_rtt_ms()` | Microseconds | "X.XXX ms" or "--" | Convert RTT to milliseconds |
| `_format_bitmap()` | 4-bit int | "XXXX" (binary) or "--" | Format tunnel/feedback bitmaps |
| `_normalize_version()` | Version string | Clean version or "--" | Strip build metadata |
| `_normalize_license_date()` | License string | "YYYY/MM/DD" or "--" | Extract license expiration |
| `_normalize_site_name()` | Site name + fallback | Clean name or fallback | Use site name or location |
| `_parse_seeker_bytes_str()` | "284.1G" etc. | float (bytes) | Parse formatted byte strings |
| `_sum_seeker_bytes_list()` | list of byte strings | Formatted total or "--" | Sum and re-format |
| `_build_tunnel_health()` | lock/feedback/mask bits | ["up"/"down"/"off"/"mismatch"] | Per-tunnel health |
| `_parse_enabled_mask()` | "1,0,1,1" | 4-bit int | Convert enable string to bitmask |
| `_extract_disabled_site_indexes()` | Config dict | set[int] | Find disabled mate indexes |

---

## Polling Cadence Summary

| Poller | Interval | What it does |
|---|---|---|
| `seeker_polling_loop` | 10s | Config + stats + routes per AN via `seeker_fetch_all()` |
| `site_name_resolution_loop` | 30s | Probes unknown tunnel peers for site names |
| `dn_seeker_polling_loop` | 5s | Probes discovered nodes via individual API calls |
| `ping_monitor_loop` | 1s tick | ICMP probes (not Seeker API) |
| `node_dashboard_polling_loop` | 1s | Projection build from caches (no Seeker API calls) |

**Concurrency:** `SEEKER_POLL_CONCURRENCY = 20` — asyncio semaphore caps simultaneous Seeker sessions.

---

## Complete Raw Field Index

### Config Response (bwvCfg)

**Site identity:** `siteId`, `siteID`, `localSiteId`, `siteName`, `name`, `localSiteName`

**Network:** `mgmtIp`, `managementIp`, `mgmtAddr`, `ethIf`, `ethIfs`, `interfaces`, `ethernetInterfaces`

**Device:** `nodeType`, `platformType`, `deviceType`, `platform`, `platformName`, `hwPlatform`

**Software:** `pkgVersion`, `version`, `swVersion`

**Licensing:** `lic` (dict with `Expires`), `licenseExpires`, `licenseExpiration`, `licExpires`

**Enclave:** `enclaveId`, `encId`, `l3vpnId`

**Mates:** `nMates`, `numMates`, `mateCount`, `mates`, `mateCfg`, `mateConfigs`, `mateConfig`, `mate`, `mate0`–`mate3`

**Mate fields:** `siteId`, `mateSiteId`, `remoteSiteId`, `ip`, `mateIp`, `addr`, `mode`, `txMode`, `actTxMode`, `enclaveId`, `l3vpn`, `notes`, `en_tx0`–`en_tx3`, `en_rx0`–`en_rx3`

**Disable flags:** `siteDisable`

**Routes:** `ipRoutes`, `staticRoutes`, `routes`, `routeCfg`

**Route fields:** `prefix`, `subnet`, `network`, `name`, `subnetName`, `label`, `nextHop`, `gateway`, `siteId`, `sid`, `metric`, `cost`

### Stats Response (bwvStats)

**CPU:** `cpuCoreUtil`

**WAN rates:** `txTotRateIf`, `rxTotRateIf`, `txChanRate`, `rxChanRate`

**WAN totals:** `totChanBytesTx`, `totChanBytesRx`

**LAN rates:** `userRate`

**LAN totals:** `totUserBytes`

**Tunnels:** `rxTunnelLock`, `matePingOk`, `matePingRTT`, `txRate`, `rxRate`, `wrState`, `rdState`, `mateTunnelFeedback`, `actTxMode`

**Channels:** `wanUp`, `chanWanDelay`, `chanPublicIp`, `ethIfLink`, `netIfSpeed`, `totNetIfSpeed`

**Discovery:** `discSites`, `sites`, `nDiscSites`

**State:** `isActive`

**Packet rates:** `txTotPktRate`, `rxTotPktRate`, `userPktRate`, `userSubnetPktRate`, `userSubnetRate`

**QoS:** `qosInByteRate`, `qosOutByteRate`

### Learnt Routes Response (bwvStats + learntRoutes)

**Route lists:** `learntIpRoutes`, `learntRoutes`, `learnedRoutes`, `routesLearnt`

**Route fields:** `prefix`, `subnet`, `network`, `name`, `subnetName`, `nextHop`, `gateway`, `siteId`, `sid`, `metric`, `cost`

**Subnet names:** `subnetNames` (dict mapping `n{index}` → name)
