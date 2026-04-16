# Changelog

All notable SMP changes should be documented here in markdown.

The format is intentionally simple so diffs stay readable in version control.

## 2026-04-16 — Topology Map Assignment Redesign

### feat
- Replace skeleton topology with map assignment model (`topology_map_id`)
- Node edit form now uses single Map Assignment dropdown (None/Main Map/<submaps>)
- ANs assigned to submaps are automatic discovery roots
- Orphan nodes on submap deletion

### fix
- Populate Map Assignment dropdown with submaps in all node forms (add node, edit node, topology editor, DN promote)
- Show resolved submap name on node detail page instead of "Submap N"

### refactor
- Remove `include_in_topology`, `topology_level`, `topology_unit` columns
- Remove `build_mock_topology_payload()` skeleton generator and `TOPOLOGY_LOCATIONS/UNITS` constants
- Rewrite topology payload to flat `{entities, links, submaps}` structure
- Remove location/unit filter buttons from frontend

### fix
- Fix SSE `_updateTopologyEntityDOM()` to update button CSS status class

## Unreleased

### Fixed

- **Topology nodes vanish after refresh:** The `loadTopologyPage()` error handler cleared `layer.innerHTML` but left `_topologyDomCache` holding detached DOM references, causing all topology entities to disappear with no recovery path. Now clears both caches in the error handler. Added a defensive `isConnected` sweep in `renderTopologyStage()` that detects fully-detached cache state and resets to clean. Removed dead `refreshTopologyData()` function.
- **SSE listener leak:** Refactored SSE connection to use named handler references (`sseHandlers` dict) with explicit `removeEventListener` cleanup on disconnect. Reconnect uses exponential backoff (2s base, 60s cap, reset on success). Visibility handler adds 500ms debounce to prevent thrash on rapid tab switching.
- **Tab visibility recovery:** Added `handleVisibilityRecovery()` that reconnects dead SSE, resets the "updated ago" baseline, and re-fetches stale page data via `safeStart()` when the user returns to a backgrounded tab. Covers all page contexts (dashboard, topology, services, charts, health, node detail).
- **SSE keepalive (Redis path):** `subscribe_channels()` now yields a `__keepalive__` sentinel every ~30s of pub/sub silence. SSE generators emit `": keep-alive\n\n"` comments so nginx and browsers don't silently drop idle connections.
- **Redis connection limits:** Added `max_connections=20`, `socket_timeout=10`, `socket_connect_timeout=5` to Redis pool.
- **Truncated app.js:** Restored the final ~60 lines of `static/js/app.js` that were lost during the snapshot write process (dashboardRefreshMenu remaining handlers, pointerdown/keydown listeners, conditional timer starts, closing `});`).
- **HTTP/1.1 head-of-line blocking:** Added Nginx HTTP/2 reverse proxy to Docker Compose dev stack. The SSE EventSource connection occupying an HTTP/1.1 keep-alive slot caused 30-50s page-load stalls on subsequent navigation. HTTP/2 stream multiplexing eliminates this entirely. Nginx terminates TLS (self-signed cert, auto-generated on first boot) on port 8443 and proxies to uvicorn internally. No application code changes.
- **SSE idle stall:** Browser tab backgrounding caused TCP buffer congestion from 1Hz keep-alive frames, stalling subsequent navigations by 10-20s. Added `visibilitychange` listener to disconnect SSE when tab is hidden and reconnect immediately when visible. Added `beforeunload` handler for clean teardown on page navigation.
- **SSE reconnect delay:** Replaced hard-coded 10s reconnect delay with exponential backoff (2s, 4s, 8s, cap 30s). First reconnect after a transient error now happens in ~2s instead of 10s.
- **Static asset caching:** Added `StaticCacheMiddleware` setting `Cache-Control: public, max-age=86400` on `/static/` responses. Templates already use cache-busting `?v=` query strings. Eliminates redundant full transfers of `app.js` and `style.css` on every page navigation.
- **SSE keep-alive frequency:** Server-side keep-alive interval increased from 1s to 15s during idle periods (data-changed path still checks every 1s). Reduces TCP writes by 15x for idle connections.

### Performance

- **Connection pool sizing:** `pool_size=30`, `max_overflow=20`, `pool_timeout=10`, `pool_recycle=3600` — eliminates 20-30s request queueing when pollers exhaust the default 5-connection pool.
- **Staggered poller startup:** 7 background pollers now start 0.5-5s apart instead of all at once, preventing thundering herd on interval alignment. Sleep jitter (`random.uniform(0, 1.0)`) prevents re-synchronization.
- **Dedicated thread pool:** 40-thread `ThreadPoolExecutor` for blocking I/O (ping, TCP checks, nslookup) replaces the default 5-thread pool, preventing web request stalls.
- **Circuit breaker:** Unreachable nodes are backed off exponentially (30s→60s→120s→300s max) after failures, freeing semaphore slots for reachable nodes. Tracked via `PollerState.node_failure_counts` / `node_backoff_until`.
- **Reduced seeker timeout:** Per-node poll timeout reduced from 30s to 15s.
- **Removed read-only commits:** Stripped `await db.commit()` from 6 GET-only route handlers (dashboard, topology, nodes), eliminating wasted DB round-trips.
- **Composite chart index:** Added `ix_chart_samples_node_ts_type` on `(node_id, timestamp, sample_type)` for chart query performance.
- **Batched discovery lookups:** Pre-built `all_seeker_details` and `all_dn_cache` dicts in submap discovery for O(1) lookups instead of repeated cache gets. Deferred full-node query in DN detail endpoint.
- **Frontend fetch timeouts:** `apiRequest()` now uses `AbortController` with 15s timeout — pages show error state instead of hanging indefinitely.
- **Discovery polling interval:** Reduced from 30s to 300s (5 min) safety-net; SSE is the primary update mechanism.

### Added

- **Diagnostic Console:** New diag code system on the Diag page (`/health`). Operators type codes like `poller:status`, `cache:stats`, `db:pool` into a console input to query runtime diagnostics. Results display as formatted JSON. History chips allow quick re-runs. 9 starter codes covering pollers, caches, DB pool, Redis, system info, and per-node detail. Catalog in `docs/DIAG_CODES.md`.

### Fixed

- **Link delete tooltip:** Deleting a topology link now clears the pinned link tooltip instead of leaving it stuck on screen.
- **Submap link creation delay:** New links created in submap views now appear immediately instead of waiting for the next polling cycle.
- **Optimistic link operations:** Link add/delete/save now update local data instantly instead of awaiting the slow `/api/topology` refetch. Rollback on API failure.
- **Topology refresh race condition:** Generation counter prevents SSE/timer background fetches from overwriting user-initiated data.

### Changed

- **Dynamic link attachment:** Topology links now connect to the nearest edge of each node icon based on relative position, replacing the 8 fixed anchor point dots. Links smoothly reposition in real-time during node drag. Link creation in edit mode uses an edge zone drag instead of clicking individual anchor points. Applies to both main map and submaps. No backend/DB changes — `source_anchor`/`target_anchor` fields preserved for backward compat.
- **Async SQLAlchemy migration:** Migrated entire database layer from synchronous SQLAlchemy (`create_engine` + `Session`) to async SQLAlchemy 2.0 (`create_async_engine` + `AsyncSession`). All route handlers, service functions, and background pollers now use non-blocking async DB I/O. Eliminates event loop starvation that caused 15-20 second page load delays. Alembic migrations remain sync (own engine). Tests converted to `IsolatedAsyncioTestCase` with `aiosqlite`.

### Added

- **System Health page:** `/health` page with chart data storage usage badge (total rows, estimated size, date range, per-node breakdown), color-coded storage chips (green/yellow/red thresholds), poller interval display, and platform diagnostics. API: `GET /api/health`.
- **Health nav link:** Updated all page navigation from placeholder `#health` to live `/health` route.
- **DN promotion to Anchor Node:** `POST /api/discovered-nodes/{site_id}/promote` converts a Discovered Node into a managed Anchor Node. Operator provides API credentials via a modal form on the DN detail page. The DN record and related data are deleted after successful promotion. New AN is immediately available for polling, charts, and topology.
- **Charts data polling:** 60-second polling loop fetches `bwvChartStats(startTime=0, entries=30)` per node — most recent 30 seconds of raw per-second data each cycle. No cursor tracking needed; duplicates handled by `ON CONFLICT DO NOTHING`. Stored in `chart_samples` table.
- **Server-side 5-minute bucketing:** `GET /api/nodes/{node_id}/chart-stats` aggregates raw samples into 5-minute time buckets before sending to the browser. Each bucket emits 3 rows (min, max, avg) preserving envelope visualization while reducing 7-day payloads from 302K rows to ~6K. Tunnel and channel JSON pre-parsed server-side.
- **Chart stats API:** `GET /api/nodes/{node_id}/chart-stats?start=&end=` returns bucketed samples. `GET /api/nodes/{node_id}/chart-summary?start=&end=` aggregates raw samples for accurate reporting.
- **ChartSample model:** `chart_samples` table with `sample_type` column and unique constraint on `(node_id, timestamp, sample_type)`.
- **get_bwv_chart_stats():** Seeker API function with `df` and `entries` parameters.
- **Charts UI page:** `/charts` page with Chart.js dual-axis graphs. Per-node user throughput, packet counts, WAN channels, and per-site tunnel charts. Envelope bands show min/max range within each 5-minute bucket, rolling average shows trend. Clickable stat badges (Avg TX/RX, Peak TX/RX, Avg Latency). "Smooth"/"Envelope" toggle per chart.
- **PDF export:** Client-side via jsPDF. Chart images + summary report with per-site tunnel table.
- **Charts nav link:** Added "Charts" to navigation bar on all pages.
- **Per-site tunnel charts:** One dual-axis chart per mate site with throughput (left, bps) + latency (right, ms in yellow). All tunnels overlaid with high-contrast color pairs. Up to 4 tunnels supported.
- **Summary report:** Per-site tunnel table with rowspan grouping, user throughput totals, channel averages. All rates in Kbps/Mbps/Gbps, latency in ms. Computed from raw samples for accuracy.
- **charts_enabled toggle:** Per-node boolean to opt in/out of charts data collection. Checkbox on the node management form. Default true.

### Fixed

- **SSE connection flood:** `connectNodeStateStream()` now guards against duplicate connections and uses manual reconnect with 10s delay instead of EventSource auto-reconnect (which retries immediately).
- **Per-submap fetch storm:** Removed per-submap `/api/topology/maps/{id}/discovery` fetches from `refreshTopologyStructure()` and `refreshTopologyPage()` timer callbacks. Submap discovery is loaded once on page load only.
- **Discovery feedback loop:** `dn_discovered` events in `discovery.py` now only fire for genuinely new peers, not all peers on every GET request. Eliminates infinite fetch-event-fetch cycle.
- **Link tooltip flood:** Added `_throttledLinkTooltipRefresh()` with 5s setTimeout so pinned link tooltips don't fire rapid HTTP requests during SSE update bursts.
- **SSE reconnect from topology timers:** Removed `connectNodeStateStream()` from `startTopologyTimers()` — SSE connects once at DOMContentLoaded, not on every timer start.

### Performance

- **Seeker polling fast/slow split:** Moved `resolve_site_name_map` (remote HTTP probes for tunnel-peer site names) out of the 5s seeker polling loop into a separate `site_name_resolution_loop` running every 30s. Poll cycle drops from ~35s to <5s. Site names fill in progressively in the background.
- **Final poll interval set to 10s:** After concurrency improvements, `SEEKER_POLL_INTERVAL_SECONDS` raised from 5s to 10s for a stable, sustainable cadence.
- **Single-session Seeker login:** `seeker_fetch_all()` in `seeker_api.py` now performs 1 login + 3 data requests per poll cycle (was 3 separate logins). Removes scheme fallback — uses only the operator-configured scheme.
- **Seeker poll concurrency:** Added `SEEKER_POLL_CONCURRENCY = 20` asyncio semaphore in `pollers/seeker.py`; scales safely to 300+ nodes.
- **WAN throughput accuracy:** Added `wan_tx_bps_channels` / `wan_rx_bps_channels` (sum of per-channel `txChanRate` / `rxChanRate`) alongside existing `txTotRateIf`-based fields. Channel-sum rates match the Seeker UI; interface-total rates include overhead.

### Fixed

- **Rate unit fix:** Seeker rate fields are Bytes/s, not bits/s — applied ×8 conversion in `_format_rate()` and `normalize_bwv_stats()`. WAN throughput values now display correct magnitudes.
- **Sticky yellow fix:** `_publish_dashboard_to_redis` in `pollers/dashboard.py` now publishes windowed rows so `rtt_state` recovers correctly from yellow instead of staying stuck.
- **SSE live updates — node detail:** Tunnels and channels tables on the node detail page now re-render on incoming SSE events (was static after initial load).
- **SSE live updates — link tooltip:** Link stat tooltip cache is cleared on SSE events and auto-refreshes if the tooltip is currently pinned. Cache TTL reduced from 10s to 4s.
- **App logging visibility:** Added `logging.basicConfig(level=logging.INFO)` to `app/main.py` so application-level INFO logs appear at startup.
- **Redis logging visibility:** Promoted all Redis failure log messages from `debug` to `warning` in `state_manager.py` so connection/write failures are visible at the default log level.

### Added

- **Redis cache warm-up (Phase 5):** Seeker detail cache and service status cache are now persisted to Redis with TTL and restored on process restart. Eliminates the ~15s cold-start delay — dashboard shows data immediately after restart instead of waiting for the first polling cycle.
- **Frontend SSE migration (Phase 4):** All pages now connect to the unified SSE endpoint on load. Node dashboard, services dashboard, main dashboard, and node detail page receive live updates via SSE events instead of `setInterval` polling. Topology structure refresh timer kept as safety net. Added `service_update`, `service_snapshot`, `dn_discovered`, `dn_removed`, and `structure_changed` event handlers to the frontend.
- **Multi-channel Redis pub/sub (Phase 3):** Extended `state_manager.py` with 3 new channels (`smp:services`, `smp:discovery`, `smp:topology-structure`) and corresponding publish functions. Service poller now publishes after each check cycle. Discovery routes publish `dn_discovered`/`dn_removed` events. Topology/node/map CRUD routes publish `structure_changed` events. New unified SSE endpoint `GET /api/stream/events?channels=...` subscribes to any combination of channels. Legacy `/api/stream/node-states` preserved as alias.

### Refactored

- **Poller + service extraction (Phase 2):** Extracted all 5 polling loops, health/service functions, and dashboard logic from `app/main.py` into `app/pollers/` (5 files) and `app/services/node_health.py`. Introduced `app/poller_state.py` — a PollerState dataclass that owns all mutable in-memory state. Converted startup/shutdown to FastAPI lifespan context manager. Main.py reduced from ~1,250 to 221 lines.
- **Route extraction (Phase 1):** Extracted all 56 route handlers from `app/main.py` into 9 modular route files under `app/routes/`. Main.py reduced from 2,612 to ~1,250 lines. No behavior change — all API endpoints, URL paths, and response shapes remain identical.

### Added

- **Redis integration** for real-time node state pub/sub (`app/redis_client.py`, `app/state_manager.py`). Redis is optional — app degrades gracefully to in-memory caching if unavailable.
- **SSE endpoint** `GET /api/stream/node-states` pushes node status, RTT, and bandwidth changes to connected clients in real time. Uses Redis pub/sub when available, falls back to 1s polling otherwise.
- **Frontend EventSource** replaces `setInterval` polling for topology ping status. Node cards update reactively via SSE events without full page redraw.
- **Docker infrastructure** — `Dockerfile` and `docker-compose.yml` for containerized deployment (app + PostgreSQL + Redis).
- `redis>=5.0` added to `requirements.txt`.
- `REDIS_URL` added to `.env.example`.
- Initial markdown changelog tracking.
- Initial version-controlled user guide in [docs/USER_GUIDE.md](docs/USER_GUIDE.md).
- Operational-map backend foundation for authored SNMPc-style map workflows:
  - map view models and schemas
  - map object and link models and schemas
  - object and link binding support
  - operational-map API routes
- A `Services` cloud object on `/topology` that can be moved and resized in edit mode and derives its status from the services pinned to the main dashboard watchlist.
- Persisted the `/topology` demo-mode selector so edit-mode preview states survive reloads