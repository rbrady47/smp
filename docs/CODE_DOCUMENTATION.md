# SMP Code Documentation

> Current state as of 2026-04-08 — branch `async_SQLAlchemy_refactor`

---

## Architecture Overview

SMP is a FastAPI application with modular route modules and a vanilla JS frontend. Backend logic lives in `app/`, routes are split into `app/routes/`, the single-page frontend is `static/js/app.js` + `static/css/style.css`, and HTML is served via Jinja2 templates.

In Docker, an Nginx reverse proxy terminates HTTP/2 + TLS on port 8443 and proxies to uvicorn over HTTP/1.1 internally. This eliminates HTTP/1.1 head-of-line blocking caused by the SSE connection occupying a keep-alive slot. Self-signed cert auto-generated on first boot.

```
Browser ──HTTP/2+TLS──> Nginx (:8443) ──HTTP/1.1──> Uvicorn (:8000)

Browser ──HTTP──> FastAPI (app/main.py)
                    ├── Route modules (app/routes/*.py)
                    │     ├── pages.py     — HTML page routes
                    │     ├── nodes.py     — /api/nodes CRUD
                    │     ├── services.py  — /api/services CRUD + dashboard
                    │     ├── dashboard.py — /api/dashboard, /api/node-dashboard
                    │     ├── topology.py  — /api/topology, links, editor-state
                    │     ├── maps.py      — /api/topology/maps CRUD
                    │     ├── discovery.py  — /api/discovered-nodes, submap discovery
                    │     ├── stream.py    — SSE endpoints
                    │     ├── charts.py   — /api/nodes/{id}/chart-stats
                    │     ├── system.py    — /api/status
                    │     └── pages.py    — /charts (Charts UI page)
                    ├── Background tasks (ping, Seeker polling, service checks)
                    ├── Redis pub/sub (state_manager.py)
                    └── Async SQLAlchemy (AsyncSession) ──> PostgreSQL (psycopg async)
```

### Data Flow

1. **AN Seeker polling** (10s, fast path): `seeker_polling_loop()` → `refresh_seeker_detail_for_node()` — single login session (1 login + 3 requests) per node, up to 20 concurrent; applies already-known site names from cache; results written to `seeker_detail_cache[node.id]`
1b. **Site name resolution** (30s, slow path): `site_name_resolution_loop()` → `resolve_site_name_map()` — probes remote tunnel peers for their site names and patches cached detail in-place
2. **DN Seeker polling** (5s): `dn_seeker_polling_loop()` → `probe_discovered_node_detail()` → `discovered_node_cache[site_id]`
3. **Ping monitoring** (5s): `ping_monitor_loop()` → `ping_snapshots[node.id]` / `dn_ping_snapshots[site_id]`
4. **Service checks** (30s): `service_polling_loop()` → DB updates
5. **Dashboard projection** (5s): `node_dashboard_polling_loop()` → combines caches into dashboard payload
6. **Frontend refresh** (user-selected): topology structure + ping status via timer; submap discovery loaded once on page load only (cached in localStorage)
7. **Charts polling** (60s): `charts_polling_loop()` → `get_bwv_chart_stats(startTime=0, entries=30)` per node → most recent 30 seconds of raw per-second data → bulk insert to `chart_samples` (ON CONFLICT DO NOTHING for dedup). No cursor tracking — `startTime=0` lets the Seeker auto-compute the window.
8. **Chart-stats API bucketing**: `/chart-stats` endpoint aggregates raw samples into 5-minute buckets server-side, emitting min/max/avg rows per bucket. Tunnel/channel JSON pre-parsed. 7-day view: ~6K rows to browser instead of 302K. Summary endpoint (`/chart-summary`) uses raw samples directly for accurate reporting.

---

## Performance Anti-Patterns (CRITICAL)

These patterns caused severe production performance problems. They are documented here as mandatory constraints for all future development.

### 1. NEVER publish per-node SSE events in a loop

**Wrong:** Iterating over N nodes and calling `publish_node_state()` for each one in the dashboard poller.

**Right:** Build a single batched snapshot and publish it once via `publish_dashboard_snapshot()`. Per-node publishing with N nodes generates N events/second per connected browser, exhausting EventSource connections and flooding the frontend.

**Where enforced:** `app/pollers/dashboard.py` — `_publish_dashboard_to_redis()` publishes a single snapshot via `publish_dashboard_snapshot()`. The `_DASHBOARD_SSE_PUBLISH_INTERVAL = 10.0` constant ensures publishing only happens every 10s, not on every 1s poller tick.

### 2. NEVER fetch per-submap endpoints on a timer

**Wrong:** Calling `/api/topology/maps/{id}/discovery` for every submap inside `refreshTopologyStructure()` or `refreshTopologyPage()` timer callbacks.

**Right:** Fetch submap discovery once on page load, cache the results in `_submapDnCountCache` (localStorage). Timer/SSE callbacks must not re-fetch per-submap endpoints. With S submaps and a T-second timer, this creates O(S/T) requests per second — scaling linearly with both submap count and timer frequency.

**Where enforced:** `static/js/app.js` — submap discovery fetched once at page load only.

### 3. NEVER publish SSE events from a GET endpoint

**Wrong:** Publishing `dn_discovered` events for ALL peers inside the `GET /api/discovered-nodes` or `GET /api/topology/maps/{id}/discovery` handler.

**Right:** Only publish events for genuinely new data (e.g., `newly_created_site_ids` set). When a GET endpoint publishes events, the frontend receives the event, re-fetches the same endpoint, which publishes more events — creating an infinite feedback loop.

**Where enforced:** `app/routes/discovery.py` — `dn_discovered` events only fire for peers in `newly_created_site_ids`.

### 4. SSE connections must be guarded

**Wrong:** Relying on the browser's built-in EventSource auto-reconnect (retries immediately, no backoff, no duplicate guard).

**Right:** Guard `connectNodeStateStream()` with a `readyState !== CLOSED` check before opening. On `onerror`, explicitly close the connection and reconnect manually after a delay (currently 10s). Never call `connectNodeStateStream()` from timer callbacks — connect once at `DOMContentLoaded`.

**Where enforced:** `static/js/app.js` — `connectNodeStateStream()` has readyState guard and manual 10s reconnect.

### 5. Seeker rate fields are Bytes/s

All Seeker API rate fields (`txChanRate`, `rxChanRate`, `txTotRateIf`, `rxTotRateIf`) return values in **Bytes per second**. The `netIfSpeed` field confirms this (100000 = 100 Mbps = 12500000 Bytes/s when the unit is Bytes). Multiply by 8 to convert to bits/s for display.

**Where enforced:** `app/seeker_api.py` — `normalize_bwv_stats()` and `_format_rate()` apply the x8 conversion.

### 6. NEVER use synchronous DB operations on the async event loop

**Wrong:** Using `create_engine()` + `Session` in `async def` route handlers or pollers. Every `db.scalars()`, `db.commit()`, `db.execute()` blocks the entire event loop — no HTTP requests served during DB I/O.

**Right:** Use `create_async_engine()` + `AsyncSession`. All DB calls must be awaited: `await db.scalars()`, `await db.commit()`, etc. Pollers use `async with AsyncSessionLocal() as db:` context managers.

**Where enforced:** `app/db.py` exports only `async_engine`, `AsyncSessionLocal`, and async `get_db()`. The sync `SessionLocal` has been removed. Alembic creates its own sync engine independently.

**History:** The original sync DB layer caused 15-20 second intermittent page load delays with 7 pollers executing 10+ blocking DB calls per second on a single-threaded uvicorn worker.

### 7. Seeker API: use single-session login

**Wrong:** Making 3 separate `seeker_post_bwv()` calls per node per cycle (each one logs in independently). With 20+ concurrent polls, this triggers Seeker rate limiting on the login endpoint.

**Right:** Use `seeker_fetch_all(node)` which performs 1 login + 3 sequential data requests on the same httpx session. The old `_seeker_post_bwv()` is kept only for single-request use cases (site-name resolution, DN probing).

**Where enforced:** `app/pollers/seeker.py` — `refresh_seeker_detail_for_node()` calls `seeker_fetch_all()`.

---

## Backend Files

### `app/db.py`

Async database layer. Exports:
- `DATABASE_URL` — from environment variable
- `Base` — SQLAlchemy `DeclarativeBase` for all models
- `async_engine` — `create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_size=30, max_overflow=20, pool_timeout=10, pool_recycle=3600)`
- `AsyncSessionLocal` — `async_sessionmaker(expire_on_commit=False)` producing `AsyncSession` instances
- `get_db()` — async generator dependency for FastAPI route handlers

**Connection pool:** 30 base + 20 overflow = 50 max connections. 7 pollers use ~7-10 at peak, leaving 40+ for web requests. `pool_timeout=10` means requests fail fast instead of hanging 30s. `pool_recycle=3600` prevents stale PostgreSQL connections.

Alembic uses its own sync engine via `engine_from_config()` — it imports only `DATABASE_URL` and `Base`.

### `app/main.py` (~280 lines)

Thin application entry point. Creates a `PollerState` instance, initializes the `NodeDashboardBackend`, starts/stops background polling loops via a FastAPI lifespan context manager, and mounts route modules. Startup uses `async_engine` for `Base.metadata.create_all` via `conn.run_sync()`. Also exports backward-compatible wrapper functions so route modules can import directly from `app.main`.

**Startup sequence:** Lifespan sets a 40-thread `ThreadPoolExecutor` as the default executor (for ping/TCP/nslookup), warms caches from Redis, then starts 7 pollers with staggered delays (0s to 5s) to prevent thundering herd.

### `app/state_manager.py`

Multi-channel Redis pub/sub layer. Publishes state changes to 4 channels:

| Channel | Events | Publishers |
|---------|--------|-----------|
| `smp:node-updates` | `node_update`, `dn_update`, `node_offline` | Dashboard poller |
| `smp:services` | `service_update` | Service poller |
| `smp:discovery` | `dn_discovered`, `dn_removed` | Discovery routes |
| `smp:topology-structure` | `structure_changed` | Node/link/map CRUD routes |

Key functions: `update_node_state()`, `update_dn_state()`, `publish_service_state()`, `publish_discovery_event()`, `publish_topology_change()`, `publish_dashboard_snapshot()`, `subscribe_channels()`.

**`publish_dashboard_snapshot(payload)`** — publishes a single batched SSE event containing the full dashboard state. Used by `_publish_dashboard_to_redis()` instead of per-node events.

**Logging:** All Redis failure logs use `logger.warning()` (not debug) so connection/write problems are visible at the default log level.

### SSE Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/stream/events?channels=...` | Unified SSE — subscribes to specified channels (defaults to all) |
| `GET /api/stream/node-states` | Legacy — node state changes only |
| `GET /api/node-dashboard/stream` | Legacy — poll-based dashboard snapshot |

**Keep-alive behavior:** Fallback (non-Redis) generators check for data changes every 1s. When no change is detected, they emit a keep-alive comment and sleep 15s (was 1s). This reduces TCP writes by 15x for idle connections and prevents buffer congestion when browser tabs are throttled.

**Frontend SSE lifecycle:** The `visibilitychange` listener disconnects SSE when the tab goes to background and reconnects immediately on focus. The `beforeunload` handler ensures clean teardown on full-page navigation. Reconnect uses exponential backoff (2s base, 30s cap) instead of a fixed 10s delay.

### `app/poller_state.py`

`PollerState` dataclass holding all mutable in-memory state: 11 cache dicts (ping, seeker, services, DN ping) + circuit breaker state (`node_failure_counts`, `node_backoff_until`) + 7 task handles + dashboard backend reference. A single instance (`_ps`) is created at module load in `main.py` and passed to every poller and service function.

### `app/pollers/` (6 files)

Background polling loops, each receiving `PollerState` as first parameter. All DB access uses `async with AsyncSessionLocal() as db:` — fully non-blocking:

| Module | Loop function | Interval | Purpose |
|--------|--------------|----------|---------|
| `ping.py` | `ping_monitor_loop(ps)` | 1s tick | ICMP probes for ANs + DNs |
| `seeker.py` | `seeker_polling_loop(ps)` | 10s (+jitter) | Seeker API polling per AN (fast path — single-session login + config/stats/routes); concurrency capped at `SEEKER_POLL_CONCURRENCY = 20`; circuit breaker skips nodes in exponential backoff (30s→300s) |
| `seeker.py` | `site_name_resolution_loop(ps)` | 30s (10s delay) | Remote site-name probes for unknown tunnel peers (slow path) |
| `dn_seeker.py` | `dn_seeker_polling_loop(ps)` | 5s (10s delay) | DN Seeker API probing |
| `services.py` | `service_polling_loop(ps)` | 30s | HTTP/DNS service checks |
| `dashboard.py` | `node_dashboard_polling_loop(ps)` | 1s | Projection build + Redis publish (SSE snapshot every 10s via slim payload) |

`dashboard.py` details:
- `_publish_dashboard_to_redis()` uses `get_cached_payload()` (windowed) instead of raw cache, so `rtt_state` recovers correctly from yellow
- Publishes a single batched snapshot via `publish_dashboard_snapshot()` — NOT per-node events (see Anti-Pattern #1)
- `_DASHBOARD_SSE_PUBLISH_INTERVAL = 10.0` — only publishes when 10s have elapsed since last publish
- `_slim_anchor()` / `_slim_dn()` strip payloads to ~16 dynamic fields for SSE (was 50+)

Also contains stateless helpers: `ping_host`, `check_tcp_port`, `compute_node_status`, `summarize_dashboard_node`, `merge_service_payload`, etc.

### `app/services/node_health.py`

Node-level business logic: `serialize_node` (sync), `refresh_nodes` (async), `get_node_or_404` (async), `request_node_telemetry` (async). Functions that take `PollerState` + `AsyncSession` + `Node` and return serialized dicts. All DB-touching functions are async.

### `app/routes/` (10 files)

Route modules split by domain. Each creates an `APIRouter` and is included in `main.py` via `app.include_router()`. Route handlers use deferred imports (`from app.main import ...`) to access shared state (caches, backend instances).

| Module | Prefix | Routes |
|--------|--------|--------|
| `pages.py` | `/` | HTML page routes (10 routes including `/health`) |
| `system.py` | `/api` | `/api/status`, `/api/health`, `POST /api/diag` (diagnostic code execution — see `docs/DIAG_CODES.md`) |
| `nodes.py` | `/api` | `/api/nodes` CRUD, detail, refresh, telemetry, bwvstats, flush-all |
| `services.py` | `/api` | `/api/services` CRUD, `/api/dashboard/services` |
| `dashboard.py` | `/api` | `/api/dashboard/nodes`, `/api/node-dashboard` |
| `topology.py` | `/api` | `/api/topology`, links CRUD, editor-state |
| `maps.py` | `/api` | `/api/topology/maps` CRUD, objects, links, bindings |
| `discovery.py` | `/api` | `/api/discovered-nodes`, submap discovery (events only for new peers — see Anti-Pattern #3), DN promotion (`POST /api/discovered-nodes/{site_id}/promote`) |
| `stream.py` | `/api` | SSE endpoints (`/api/stream/node-states`, `/api/node-dashboard/stream`) |

**Constants (lines ~85-95):**
- `PING_INTERVAL_SECONDS = 5.0` — ping burst cycle
- `SEEKER_POLL_INTERVAL_SECONDS = 10.0` — AN Seeker API poll
- `SEEKER_POLL_CONCURRENCY = 20` — max concurrent AN polls (asyncio semaphore)
- `DN_SEEKER_POLL_INTERVAL_SECONDS = 5.0` — DN Seeker API poll
- `SERVICE_POLL_INTERVAL_SECONDS = 30.0` — service check cycle

**Global caches:**
- `seeker_detail_cache: dict[int, dict]` — keyed by `Node.id`, stores full Seeker API response (config_summary, tunnels, channels, routes, node_summary)
- `ping_snapshots: dict[int, dict]` — keyed by `Node.id`, stores AN ping state
- `dn_ping_snapshots: dict[str, dict]` — keyed by site_id string, stores DN ping state
- `node_dashboard_backend` — singleton `NodeDashboardBackend` instance

**Background tasks:**

| Function | Interval | Purpose |
|----------|----------|---------|
| `seeker_polling_loop()` | 10s | Polls AN Seeker APIs via single-session login; backfills `node_id` from config; concurrency limited to 20 |
| `dn_seeker_polling_loop()` | 5s (10s initial delay) | Polls DN Seeker APIs from DB + in-memory cache |
| `ping_monitor_loop()` | 5s | Pings all ANs and DNs, updates snapshots |
| `service_polling_loop()` | 30s | Runs HTTP/DNS service checks |
| `node_dashboard_polling_loop()` | 5s | Refreshes dashboard projection cache |

**Key endpoints:**

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/nodes` | GET/POST | List/create nodes |
| `/api/nodes/{id}` | GET/PUT/DELETE | Node CRUD |
| `/api/nodes/ping-status` | GET | Lightweight ping cache read |
| `/api/node-dashboard` | GET | Dashboard payload (anchors + discovered) |
| `/api/topology` | GET | Main topology payload |
| `/api/topology/discovery` | GET | Main map discovery (uses Pydantic `TopologyDiscoveryPayload`) |
| `/api/topology/maps/{id}` | GET | Submap detail |
| `/api/topology/maps/{id}/discovery` | GET | **Submap discovery** — the core endpoint for DN discovery and link building |
| `/api/topology/editor-state` | GET/PUT | Persist layout overrides |
| `/api/topology/links` | GET/POST/PUT/DELETE | Authored topology links |
| `/nodes/discovered/{site_id}` | GET | DN detail page (HTML) |
| `/topology` | GET | Topology page (HTML) |
| `/topology/maps/{id}` | GET | Submap page (HTML) |
| `/api/stream/node-states` | GET | **SSE stream** — real-time node state updates (Redis push or polling fallback) |

**Submap discovery endpoint (`get_submap_discovery`, ~line 1868):**

This is the most complex endpoint. It:
1. Loads placed anchor nodes for the submap
2. Builds `inventory_site_ids` exclusion set from ALL registered nodes (DB + seeker cache + host-based reverse matching)
3. Pre-seeds `seen_site_ids` from `discovered_nodes` DB for known peers
4. Iterates AN tunnel data (`seeker_detail_cache`):
   - Uses `_tunnel_row_exists()` to include all S&T entries (up or down)
   - Discovers NEW peers only from UP tunnels (`_tunnel_row_is_eligible()`)
   - Creates AN→DN links for all tunnel entries
5. Second-hop DN→DN discovery from `discovered_node_cache` tunnel data
6. Persists discovered nodes and observations to DB
7. Returns `discovered_peers`, `discovery_links`, `saved_positions`

**`probe_discovered_node_detail()` (~line 1235):**
Delegates to `node_dashboard_backend.probe_discovered_node_detail()`. Probes a DN's Seeker API to get its config, tunnels, and health. Result is cached in `discovered_node_cache` with `detail.tunnels` for DN-DN link discovery.

---

### `app/node_dashboard_backend.py` (~950 lines)

Central state management class. Singleton instance in `main.py`.

**Class: `NodeDashboardBackend`**

Key attributes:
- `discovered_node_cache: dict[str, dict]` — DN data keyed by site_id
- `discovered_probe_ttl_seconds = 5.0` — how often to re-probe each DN
- `discovered_probe_inflight: set[str]` — prevents duplicate concurrent probes

Key methods:
- `probe_discovered_node_detail()` — full Seeker API probe for a DN (config, stats, routes), stores result with `detail` key containing tunnels
- `merge_cached_discovered_node()` — merges new data into cache, marks projection dirty
- `_normalize_discovered_row()` — validates against `NodeDashboardDiscoveredRow` schema, auto-fills `row_type`, `pin_key`, `detail_url` defaults
- `get_cached_discovered_node()` — read from cache
- `build_projection()` / `build_payload()` — builds dashboard data for API responses

---

### `app/node_discovery_service.py` (~296 lines)

Discovery logic for finding new nodes from tunnel data.

Key functions:
- `_tunnel_row_is_eligible(row)` — strict check: requires `ping == "up"` AND (tunnel bitmap has "1" OR tunnel_health has "up"). Used for discovering NEW peers only.
- `_tunnel_row_exists(row)` — loose check: only requires valid `mate_site_id` and `mate_ip`. Used for creating links to already-known peers (up or down).
- `refresh_discovered_inventory()` — main discovery loop, processes AN tunnel data to find/update DN candidates

---

### `app/schemas.py` (~495 lines)

Pydantic models with `ConfigDict(extra="forbid")` for strict validation.

Key schemas:
- `NodeDashboardAnchorRow` — AN row for dashboard (includes WAN/LAN metrics)
- `NodeDashboardDiscoveredRow` — DN row for dashboard (requires `row_type`, `pin_key`, `detail_url`)
- `TopologyDiscoveryDiscoveredNode` — DN for main map discovery (includes `rtt_state`, `latency_ms`)
- `TopologyDiscoveryPayload` — full discovery response for main map (validated via Pydantic)

---

### `app/seeker_api.py` (~1073 lines)

Seeker device API integration over HTTPS.

Key functions:
- `seeker_fetch_all(node)` — **primary entry point for polling**: 1 login + 3 sequential data requests (config, stats, routes) on a single httpx session. See Anti-Pattern #6.
- `_build_candidate_base_urls(node)` — returns only the operator-configured scheme (no scheme fallback — previously tried both http/https, causing `http://host:443` errors)
- `normalize_bwv_stats(raw)` — extracts WAN/LAN tx/rx rates, CPU, site count. Rate fields are Bytes/s; applies x8 conversion for bits/s (see Anti-Pattern #5). Produces `wan_tx_bps_channels` / `wan_rx_bps_channels` (sum of per-channel rates).
- `_format_rate(value)` — formats rate with x8 Bytes-to-bits conversion
- `build_detail_payload(node, ...)` — assembles full node detail from config + stats + routes
- `build_dashboard_link_status(tunnels)` — computes link health from tunnel data
- `_seeker_post_bwv(node, body)` — single-request convenience wrapper, kept for site-name resolution and DN probing only
- Helper functions: `_make_bwv_request_body()`, `_bwv_error()`, `_post_one_bwv()`

---

### `app/topology.py` (~351 lines)

Topology data construction and status normalization.

Key functions:
- `normalize_topology_location(location)` — maps aliases to canonical location names
- `topology_status_from_rtt_state(rtt_state)` — maps RTT states to topology health
- `build_topology_discovery_payload(...)` — builds `TopologyDiscoveryPayload` for main map (NOT submaps — those return plain dicts from `get_submap_discovery`)

---

### `app/operational_map_service.py` (~621 lines)

SNMPc-style authored map CRUD. Manages views (canvases), objects (nodes/labels/submaps), links (connections), and bindings (live data display).

---

### `app/topology_editor_state_service.py` (~71 lines)

Persists topology editor state (layout overrides, link anchor assignments, demo mode) to/from `topology_editor_state` DB table.

---

### `app/redis_client.py` (~60 lines)

Async Redis connection with lazy initialization and graceful fallback. Reads `REDIS_URL` from environment (default `redis://localhost:6379/0`). If Redis is unavailable at startup, sets `_unavailable = True` and all subsequent calls return `None` — the app continues with in-memory caches.

Key functions:
- `get_redis()` — returns shared async Redis connection or `None`
- `close_redis()` — shuts down pool on app shutdown
- `redis_available()` — live ping check

---

### `app/state_manager.py` (~170 lines)

Dual-write state layer that publishes node state to Redis for SSE push. All operations are no-ops if Redis is unavailable.

Key design:
- Redis keys: `smp:node:{node_id}` (ANs), `smp:dn:{site_id}` (DNs)
- Values: JSON-serialized state dicts with 30s TTL (2x poll interval)
- Pub/sub channel: `smp:node-updates`
- Published events: `node_update`, `dn_update`, `node_offline`

Key functions:
- `update_node_state(node_id, state)` — SET + PUBLISH for AN state change
- `update_dn_state(site_id, state)` — SET + PUBLISH for DN state change
- `publish_offline(node_type, id)` — DELETE + PUBLISH for offline event
- `get_all_node_states()` / `get_all_dn_states()` — SCAN + MGET for bulk reads
- `subscribe_state_changes()` — async iterator yielding pub/sub events

**Data flow:**
```
Background poll loop → in-memory cache → state_manager.update_*() → Redis SET + PUBLISH
                                                                          ↓
SSE endpoint ← subscribe_state_changes() ← Redis pub/sub ← smp:node-updates channel
```

---

### `app/db.py` (~30 lines)

Database setup: SQLAlchemy engine from `DATABASE_URL` env var, `SessionLocal` factory, `get_db()` FastAPI dependency.

---

### `app/models.py` (~215 lines)

SQLAlchemy ORM models using `Mapped[]` type annotations.

Key tables:
| Model | Table | Primary Key | Purpose |
|-------|-------|-------------|---------|
| `Node` | `nodes` | `id` (auto-int) | Registered anchor nodes |
| `DiscoveredNode` | `discovered_nodes` | `site_id` (string) | Auto-discovered nodes |
| `DiscoveredNodeObservation` | `discovered_node_observations` | `site_id` (FK) | DN telemetry snapshots |
| `NodeRelationship` | `node_relationships` | `id` | Discovery topology edges |
| `TopologyLink` | `topology_links` | `id` | Authored topology connections |
| `TopologyEditorState` | `topology_editor_state` | `id` | Layout/state persistence |
| `OperationalMapView` | `operational_map_views` | `id` | Map canvases |
| `OperationalMapObject` | `operational_map_objects` | `id` | Objects on canvases |
| `OperationalMapLink` | `operational_map_links` | `id` | Links between objects |

---

## Frontend

### `static/js/app.js` (~8088 lines)

Single monolithic JS file. No build system, no modules, vanilla JS.

**State management:**
- `topologyState` — central state object for topology page
  - `editMode` — edit mode toggle
  - `pinnedTooltipId` — which entity's tooltip is pinned
  - `pinnedLinkNodeId` — which entity's discovery links are pinned
  - `pinnedLinkTooltipId` — which link's tooltip is pinned
  - `layoutOverrides` — per-entity position/size overrides
  - `linkAnchorAssignments` — per-link anchor point assignments
  - `_prevDnStates` — previous DN states for flash detection
  - `_flashTimers` — active flash animation timers
- `topologyPayload` — current topology data from API
- `topologyDiscoveryPayload` — current discovery data
- `topologyNodeDashboardPayload` — current node dashboard data
- `_submapDnCountCache` — localStorage-backed cache for per-submap DN counts (keyed by `map_view_id`, stores `{dn_up, dn_down, dn_up_names, dn_down_names}`). Survives page refreshes. Populated by discovery endpoint fetches, read at render time.

**SSE connection management:**
- `connectNodeStateStream()` — connects to `/api/stream/events` (all channels) once at `DOMContentLoaded` for all pages
- Guarded: checks `readyState !== CLOSED` before opening a new EventSource to prevent duplicate connections
- Manual reconnect: `onerror` handler closes the connection and reconnects after 10s delay (does NOT rely on EventSource auto-reconnect, which retries immediately and floods the server)
- NOT called from `startTopologyTimers()` — SSE is a one-time connection, not per-timer
- `_updateNodeDetailFromSSE()` — re-fetches full detail and re-renders tunnels/channels tables (debounced via `_detailTableRefreshPending`)
- `applyNodeUpdate()` — clears link stats cache and calls `_throttledLinkTooltipRefresh()` for pinned tooltips
- `_throttledLinkTooltipRefresh()` — setTimeout(5000) gate; only fires when a tooltip is actually pinned, prevents rapid HTTP requests during SSE bursts
- Link stats cache key normalized to String, TTL reduced from 10s to 4s

**DOM cache consistency:**
- `_topologyDomCache` (Map) and `_topologyLinkDomCache` (Map) must stay in sync with the live DOM at all times
- The `loadTopologyPage()` error handler clears both caches whenever it wipes `layer.innerHTML`, preventing detached-reference stalls
- `renderTopologyStage()` includes a defensive `isConnected` sweep: if every cached button is detached (`btn.isConnected === false`), both caches are cleared and the layer is wiped, forcing a full rebuild on the next render pass. The `for...of` loop breaks on the first connected node, making the check O(1) in the common healthy case

**Discovery link visibility system (~lines 5500-5600):**

Links are SVG `<line>` elements rebuilt every render cycle. Visibility is managed via CSS classes:

| Class | State | Opacity | Visibility |
|-------|-------|---------|------------|
| (none) | Hidden (default) | 0 | hidden |
| `is-link-revealed` | Visible | 0.72 | visible |
| `is-link-flashing` | Flash (3s) | 0.85 | visible |
| `is-link-fading` | Fade out (1.6s) | 0 | visible |

Key functions:
- `revealDiscoveryLinksForEntity(entityId)` — show links for one entity
- `hideDiscoveryLinksForEntity(entityId)` — hide links for one entity
- `hideAllDiscoveryLinks()` — hide all
- `revealAllDiscoveryLinks()` — show all (edit mode)
- `flashDiscoveryLinksForEntity(entityId)` — flash→fade animation on state change

**Pre-reveal at creation (lines ~4822-4850):**
When SVG links are created in `drawTopologyLinks()`, links for pinned nodes or edit mode get `is-link-revealed` baked into their class attribute to prevent flicker.

**AP (anchor point) coloring:**
- `getTopologyConnectedAnchorMap()` — best-wins scoring for authored links
- `getDiscoveryWorstAnchorMap()` — worst-wins scoring for discovery links (down > degraded > healthy)
- AP dots use discovery worst status when it's "down" or "degraded"

**Submap rendering:**
- `getTopologySubmapIconMarkup(entity, dnUp, dnDown, dnUpNames, dnDownNames)` — generates data-driven mesh SVG where each dot = one DN (green/red/white), with nearest-neighbor lines and convex hull perimeter
- Cluster scales via `scaleFactor = min(1, 0.35 + (total / 20) * 0.65)` — small counts cluster tight, large counts fill the viewBox
- Radial SVG glow behind mesh scales with cluster dimensions
- DN data read from `_submapDnCountCache` (localStorage), falling back to backend payload
- Submap discovery fetched once on page load only (not on timer/SSE cycles — see Performance Anti-Patterns #2); results cached in `_submapDnCountCache` (localStorage)
- `renameTopologySubmap(entity)` — right-click rename handler (edit mode only), calls `PUT /api/topology/maps/{id}` with new name

**DN hover tooltip:**
- `data-submap-dn-all` attribute on `.topology-node-icon-submap` stores all DN names with `up:`/`down:` prefixes
- `mouseenter` on submap card creates `.topology-submap-dn-tooltip` with inline green/red coloring per entry
- `mouseleave` removes tooltip; stale tooltips cleaned up at start of each render cycle

**Entity interactions:**
- **Hover**: reveals discovery links for that entity; in submaps, fades unconnected nodes (DOM-based via `applyTopologyHoverFocus()`)
- **Click**: pins/unpins tooltip + discovery links; in submaps, fades unconnected nodes (render-baked via `fadedEntityIds` set computed before render loop)
- **Double-click (AN)**: opens HTTPS web session to node
- **Double-click (DN)**: opens HTTPS web session to DN host
- **Right-click (AN/DN)**: opens floating detail panel
- **Right-click (Submap, edit mode)**: rename prompt via `renameTopologySubmap()`
- **Stage click**: clears all pins and hover focus

**Hover focus system:**
- `applyTopologyHoverFocus(entityId)` — DOM-based, adds `is-topology-faded` class to unconnected nodes (used for transient hover)
- `clearTopologyHoverFocus()` — removes all `is-topology-faded` classes
- `fadedEntityIds` set (computed in `renderTopologyStage()`) — bakes fade into HTML class list at render time for pinned state, preventing flash on DOM rebuild
- CSS: `.is-topology-faded` uses `opacity: 0.12`, `filter: saturate(0) brightness(0.4) !important`, `animation: none !important` on element and all children
- Only active inside submap views (`data-map-view-id` present on `#topology-root`)

**DN auto-placement (inside `refreshSubmapDiscovery`):**
- Center-out radial spiral using golden-angle (137.5°) offset per DN
- First DN placed dead center of the stage
- 120px exclusion zone around each AN position
- `minSep` (84px) between DN centers prevents overlap
- Saved DB positions and layout overrides take priority over auto-placement

**Discovery link anchor point assignment:**
- AN→DN links: fixed south (AN) → north (DN)
- DN→DN links: `pickAnchorPointFromSet()` selects from E/SE/S/SW/W based on geometric angle between entity centers
- Operator anchor overrides (from edit mode) still take priority at draw time

**AN tooltips:** Hidden inside submap views; only DN tooltips are shown.

**`refreshSubmapDiscovery(submapViewId)`:**
Called once on page load (not on timer/SSE cycles — see Anti-Pattern #2). Fetches discovery data, builds DN entities, auto-places new DNs using radial spiral, builds discovery link objects (AN→DN and DN→DN with geometry-based AP assignment and deduplication), detects state changes, and triggers flash animations.

---

### `static/css/style.css` (~4904 lines)

Three themes: light, dark, vader. All CSS variables.

**Discovery link CSS (lines ~1354-1385):**
- Base: `visibility: hidden; opacity: 0; animation: none`
- `.is-link-revealed`: `visibility: visible; opacity: 0.72`
- `.is-link-revealed.topology-link-down`: re-enables pulse animation
- `.is-link-flashing`: `visibility: visible; opacity: 0.85; animation: none`
- `.is-link-fading`: `visibility: visible; opacity: 0; animation: none`

**AP dot coloring (lines ~1594-1616):**
- `.is-connected-healthy`: green
- `.is-connected-degraded`: yellow
- `.is-connected-down`: red
- `.is-connected-neutral`: blue

**Submap portal card CSS:**
- `.topology-submap` — rounded-rectangle card with dark background, cyan glow border, hover effects
- `.topology-submap-mesh-line` / `.topology-submap-mesh-node` — mesh icon SVG styling (cyan strokes, white nodes with drop-shadow)
- `.topology-submap-dn-tooltip` — vader-themed hover tooltip (near-black bg, inline green/red text per entry)
- `.topology-submap .topology-node-icon::before` — disabled (prevents inherited blurred circle glow)
- `.topology-submap .topology-node-icon` — `filter: none` (prevents inherited drop-shadow)
- 6 instances of `.topology-node:not(.topology-cluster)` have `:not(.topology-submap)` added to prevent circle styling from overriding submap card shape

---

## Templates

| Template | Route | Purpose |
|----------|-------|---------|
| `topology.html` | `/topology`, `/topology/maps/{id}` | Topology canvas with edit controls, breadcrumbs, refresh selector |
| `dashboard.html` | `/nodes/dashboard` | AN + DN node dashboard |