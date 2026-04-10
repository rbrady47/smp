# Agent Handoff

This file is the shared handoff log for agents working on SMP.

## How To Use
- Add new entries at the top.
- Keep entries short and concrete.
- Record only what another agent needs to continue safely.
- Do not delete older entries unless they are clearly obsolete and superseded.

## 2026-04-10 — Session: Nginx HTTP/2 Reverse Proxy

### Branch / commit
- Branch: `claude/fix-page-load-performance-jOU30`

### What was built

- **Nginx HTTP/2 reverse proxy** in Docker Compose to eliminate HTTP/1.1 head-of-line blocking that caused 30-50s page-load stalls when the SSE connection occupied the browser's keep-alive slot.
- `nginx/nginx.conf` — reverse proxy with SSE-aware location (buffering off, 24h read timeout), upstream keepalive pool of 32 connections
- `nginx/generate-cert.sh` — auto-generates self-signed TLS cert on first boot via Nginx's `/docker-entrypoint.d/` mechanism
- `nginx/Dockerfile` — Nginx 1.27 Alpine with OpenSSL for cert generation
- `docker-compose.yml` — added `nginx` service on port 8443, changed `smp` from `ports` to `expose` (internal only), added `nginx-certs` named volume

### Files created
- `nginx/nginx.conf`, `nginx/generate-cert.sh`, `nginx/Dockerfile`

### Files modified
- `docker-compose.yml`, `CHANGELOG.md`, `docs/AGENT_HANDOFF.md`, `docs/CODE_DOCUMENTATION.md`

### Verification
- `python -m compileall -f app tests alembic` — all pass
- `python -m unittest discover -s tests` — 45/45 pass
- No application code changes — Nginx proxy is transparent

### Notes
- Access via `https://localhost:8443` (accept self-signed cert warning)
- Uvicorn no longer exposed on host port 8000; only reachable inside Docker network
- `StaticCacheMiddleware` in `app/main.py` still useful for non-Docker dev

---

## 2026-04-10 — Session: SSE Idle Stall + Static Asset Caching

### Branch / commit
- Branch: `claude/fix-page-load-performance-jOU30`

### What was built

- **SSE visibility handling:** `visibilitychange` listener disconnects SSE on tab background, reconnects on focus. `beforeunload` handler ensures clean teardown on page navigation.
- **SSE reconnect backoff:** Replaced 10s hard-coded delay with exponential backoff (2s, 4s, 8s, cap 30s). Reset on successful connect.
- **Static cache headers:** `StaticCacheMiddleware` in `app/main.py` adds `Cache-Control: public, max-age=86400` to `/static/` responses.
- **SSE keep-alive interval:** Server-side fallback generators now sleep 15s between keep-alive frames (was 1s). Data-changed path still checks every 1s.

### Files touched
- `static/js/app.js` — visibilitychange, beforeunload, backoff, reconnect counter
- `app/main.py` — StaticCacheMiddleware class + registration
- `app/routes/stream.py` — keep-alive interval 1s → 15s in 3 generators
- `CHANGELOG.md`, `docs/AGENT_HANDOFF.md`, `docs/CODE_DOCUMENTATION.md`

### Verification
- `python -m compileall -f app tests alembic` — all pass
- `python -m unittest discover -s tests` — 45/45 pass

---

## 2026-04-10 — Session: Performance Fixes (Tier 1 + Tier 2)

### Branch / commit
- Branch: `claude/fix-page-load-performance-jOU30`

### What was built

**Tier 1 — Eliminate 20-30s hangs:**
- Sized connection pool: `pool_size=30, max_overflow=20, pool_timeout=10, pool_recycle=3600` (was defaults: 5/10/30/none)
- Staggered 7 poller startups (0s to 5s apart) with sleep jitter to prevent thundering herd
- Dedicated 40-thread `ThreadPoolExecutor` for blocking I/O (ping, TCP, nslookup) — was sharing default 5-thread pool
- Circuit breaker on seeker polling: exponential backoff (30s→300s) for unreachable nodes via `PollerState.node_failure_counts` / `node_backoff_until`
- Reduced per-node poll timeout from 30s to 15s

**Tier 2 — Improve general responsiveness:**
- Removed `await db.commit()` from 6 read-only GET handlers (dashboard, topology, nodes)
- Added composite index `ix_chart_samples_node_ts_type` on `(node_id, timestamp, sample_type)` — migration `20260410_0018`
- Pre-built lookup dicts in submap discovery for O(1) cache access; deferred full-node query in DN detail
- Frontend `apiRequest()` now uses `AbortController` with 15s timeout
- Discovery polling interval changed from 30s to 300s (SSE is primary)

### Files touched
- `app/db.py` — pool configuration
- `app/main.py` — thread pool, staggered poller starts
- `app/poller_state.py` — circuit breaker fields
- `app/pollers/seeker.py` — circuit breaker logic, reduced timeout, jitter
- `app/pollers/charts.py` — jitter
- `app/pollers/dn_seeker.py` — jitter
- `app/pollers/services.py` — jitter
- `app/pollers/dashboard.py` — jitter
- `app/routes/dashboard.py` — removed read-only commits
- `app/routes/topology.py` — removed read-only commits
- `app/routes/nodes.py` — removed read-only commit
- `app/routes/discovery.py` — batched cache lookups, deferred query
- `static/js/app.js` — fetch timeouts, discovery interval
- `alembic/versions/20260410_0018_add_chart_samples_composite_index.py` (new)
- `CHANGELOG.md`, `docs/AGENT_HANDOFF.md`, `docs/CODE_DOCUMENTATION.md`

### Verification
- `python -m compileall -f app tests alembic` — all pass
- `python -m unittest discover -s tests` — same pre-existing failures (7 import errors + 1 topology test), no new failures

### Assumptions / gaps
- DB session release pattern (Fix 2) was already correct in all pollers — sessions close after node queries, before API calls
- Circuit breaker state is in-memory only — resets on process restart (acceptable for this use case)
- Pre-existing test failures: topology sort bug (`int('agg-cloud')`), missing `DATABASE_URL` for tests that import `app.db` directly

### Next steps
- Verify pool stats under load via `db:pool` diag command
- Confirm circuit breaker behavior with known-unreachable nodes
- Apply migration `20260410_0018` on production DB (`alembic upgrade head`)
- Consider adding data retention policy for `chart_samples` table

---

## 2026-04-08 — Session: Diagnostic Console + Link Bug Fixes

### Branch / commit
- Branch: `claude/seeker-charts-polling-UAgpt`

### What was built

**Diagnostic Console**
- Created `app/diag.py` — handler registry with 9 starter codes:
  - `help` — list all codes
  - `poller:status` — task states, intervals
  - `cache:stats` — entry counts for all in-memory caches
  - `cache:detail name=X` — dump specific cache contents
  - `db:pool` — SQLAlchemy connection pool stats
  - `redis:status` — Redis connection, version, memory
  - `system:info` — Python version, uptime, PID
  - `node:detail node_id=X` — cached seeker detail for a node
  - `ping:detail node_id=X` — ping snapshot and samples
- Added `POST /api/diag` endpoint in `app/routes/system.py`
- Added console UI to `/health` page: text input, Run button, JSON output, history chips
- Created `docs/DIAG_CODES.md` catalog

**Link bug fixes**
- Fixed link delete tooltip not clearing
- Fixed submap link creation delay (refreshTopologyData early return)
- Fixed Link Config panel staying open (close before API call)
- Fixed race condition: SSE/timer refreshes overwriting user data (generation counter)
- Optimistic link add/delete/save: instant local update, rollback on API failure

### Files touched
- `app/diag.py` (new) — handler registry
- `app/routes/system.py` — POST /api/diag endpoint
- `templates/health.html` — diag console section
- `static/css/style.css` — console styles
- `static/js/app.js` — console wiring + all link fixes
- `docs/DIAG_CODES.md` (new) — code catalog
- `CHANGELOG.md`, `docs/AGENT_HANDOFF.md`

---

## 2026-04-08 — Session: Link Bug Fixes

### Branch / commit
- Branch: `claude/seeker-charts-polling-UAgpt`

### What was fixed

**Bug 1 — Link delete doesn't clear tooltip** (`app.js:6802`)
- Added `topologyState.pinnedLinkTooltipId = null` and `hideTopologyLinkTooltip()` to the `topology-link-ctx-delete` click handler, matching the pattern used by other entity click handlers.

**Bug 2 — Link creation delayed in submaps** (`app.js:5962`)
- `refreshTopologyData()` had an early return when `data-map-view-id` was set (submap view), so newly created links never refreshed the payload. Now delegates to `refreshTopologyPage()` which handles submap fetching correctly.

### Files touched
- `static/js/app.js` — 2 targeted fixes (4 lines changed)
- `CHANGELOG.md` — added fix entries
- `docs/AGENT_HANDOFF.md` — this entry

---

## 2026-04-08 — Session: Dynamic Link Attachment

### Branch / commit
- Branch: `claude/seeker-charts-polling-UAgpt`

### What was built
Replaced the 8 fixed anchor point dots on topology nodes with dynamic edge attachment. Links now connect to the nearest edge of each node based on relative position (angle-based for circles, ray-rect intersection for rectangles).

### Files touched
- `static/js/app.js`:
  - Added `getEdgeAttachmentPoint()` — circle/rect edge intersection geometry
  - Added `isCircularTopologyEntity()` — entity shape classifier
  - Updated `drawTopologyLinks()` — uses dynamic edge attachment instead of fixed anchor lookup
  - Replaced 8 anchor point `<span>` elements with single `topology-link-create-zone`
  - Rewrote link creation UX — drag from edge zone, rubberband tracks angle dynamically
  - Added `getTopologyNodeSnapTarget()` / `highlightTopologySnapTarget()` — node-level snap targeting
  - Simplified `setTopologyActiveLinkHandleTarget()` — no more per-anchor highlighting
  - Removed `connectedAnchorMap` / `discoveryWorstMap` usage (dead code with no anchor dots)
- `static/css/style.css`:
  - Replaced `.topology-anchor-point` styles with `.topology-link-create-zone` and `.is-link-snap-target`
- `CHANGELOG.md` — added entry

### Key decisions
- **No backend changes** — `source_anchor`/`target_anchor` DB fields kept for backward compat
- **pickAnchorPointFromSet()** still used to compute compass direction keys for API calls when creating links
- **Real-time drag** works unchanged — `drawTopologyLinks()` was already called on every `pointermove`; only the endpoint calculation changed

### Next steps
- Consider removing dead anchor-point functions (`getTopologyAnchorPointDefinitions`, `getTopologyConnectedAnchorMap`, `getDiscoveryWorstAnchorMap`)
- May want to add a small inset to edge attachment for visual padding between line end and icon

---

## 2026-04-08 — Session: System Health Page

### Branch / commit
- Branch: `claude/seeker-charts-polling-UAgpt`

### What was built
System Health page (`/health`) with chart data storage usage badge and platform diagnostics.

### Files touched
- `app/routes/system.py` — added `GET /api/health` endpoint (chart storage stats, per-node breakdown, poller intervals)
- `app/routes/pages.py` — added `/health` page route
- `templates/health.html` — new health page template with storage badges, per-node table, poller info
- `static/js/app.js` — added `loadHealthPage()` with storage helpers, color-coded chip logic
- `templates/*.html` (10 files) — updated nav links from `#health` to `/health`
- `docs/USER_GUIDE.md` — added System Health section
- `docs/CODE_DOCUMENTATION.md` — updated route table
- `CHANGELOG.md` — added health page entry

### API
`GET /api/health` returns:
- `chart_storage`: total_rows, estimated_bytes, oldest/newest timestamp, per_node breakdown
- `nodes`: total count, charts_enabled count
- `pollers`: seeker/charts/services interval_s

### Storage badge thresholds
- Green: < 1M rows
- Yellow: 1M–5M rows
- Red: > 5M rows

### Next steps
- Data retention policy (auto-purge old chart_samples)
- Poller cycle health (last run time, errors per cycle)

---

## 2026-04-08 — Session: Async SQLAlchemy Migration

### Branch / commit
- Branch: `async_SQLAlchemy_refactor` (off `claude/seeker-charts-polling-UAgpt`)

### What was built
Complete migration from synchronous SQLAlchemy to async SQLAlchemy 2.0. All DB I/O is now non-blocking.

### Architecture
- **Engine**: `create_async_engine(DATABASE_URL, pool_pre_ping=True)` with `async_sessionmaker(expire_on_commit=False)`
- **Route handlers**: All ~60 handlers use `db: AsyncSession = Depends(get_db)` with `await` on every DB call
- **Service functions**: All ~30 service functions accept `AsyncSession`, return awaitable results
- **Pollers**: All 6 pollers use `async with AsyncSessionLocal() as db:` — no more `asyncio.to_thread()` wrappers
- **Alembic**: Untouched — creates its own sync engine via `engine_from_config()`, imports only `DATABASE_URL` and `Base`
- **Tests**: Converted to `IsolatedAsyncioTestCase` with `aiosqlite` for async SQLite

### Why
The app had 7 background pollers executing 10+ synchronous DB calls per second on a single-threaded uvicorn event loop. Every `db.scalars()`, `db.commit()`, `db.execute()` blocked the event loop — HTTP requests queued behind poller operations, causing 15-20 second intermittent page load delays.

### Key decisions
- **Keep psycopg**: psycopg 3.x supports async natively with same `postgresql+psycopg://` URL — no driver change needed
- **`expire_on_commit=False`**: Prevents detached-instance errors when accessing model attributes after commit
- **No `relationship()`**: No lazy-loading concerns — all data access is explicit via queries
- **Alembic isolation**: Alembic imports only `DATABASE_URL` and `Base`, builds its own sync engine — zero coupling to async migration

### Files touched (25 files)
- `app/db.py` — replaced sync engine/session with async equivalents
- `app/main.py` — async startup `create_all`, async wrapper functions
- `app/services/node_health.py` — `get_node_or_404`, `refresh_nodes` now async
- `app/operational_map_service.py` — all 15 functions now async
- `app/topology_editor_state_service.py` — 2 functions now async
- `app/node_projection_service.py` — DB calls now awaited
- `app/node_discovery_service.py` — discovery functions now async
- `app/node_dashboard_backend.py` — 9 methods converted to async
- `app/routes/{nodes,maps,services,dashboard,topology,discovery,charts,pages}.py` — all handlers converted
- `app/pollers/{seeker,ping,services,dashboard,dn_seeker,charts}.py` — native AsyncSession
- `requirements.txt` — added `aiosqlite`
- `tests/test_{node_dashboard_backend,operational_map_service,topology_editor_state_service}.py` — async tests

### Verification
1. Docker build succeeds with `python -m compileall app tests alembic`
2. Container starts, pollers cycle without errors (10 nodes in 3.0s)
3. Page navigation dramatically faster — no more 15-20s delays
4. Alembic `upgrade head` still works
5. All API endpoints return 200

### Gaps / next steps
- **Remaining sync DB in routes**: Route handlers still run sync `db.add()` (which is fine — `add` doesn't do I/O) but the `get_db()` session scope means transactions are handled correctly
- **Connection pool tuning**: Default pool size may need adjustment under higher load — monitor with `pool_size` and `max_overflow` params
- **Data retention**: `chart_samples` table grows indefinitely — needs cleanup policy
- **CDN vendoring**: Chart.js/jsPDF still loaded from CDN

---

## 2026-04-08 — Session: DN Promotion to Anchor Node

### Branch / commit
- Branch: `claude/seeker-charts-polling-UAgpt` (off `seeker-charts`)

### What was built
Discovered Node (DN) promotion to Anchor Node (AN) — full backend + frontend feature.

### Architecture
- **Endpoint**: `POST /api/discovered-nodes/{site_id}/promote` — accepts `DnPromoteRequest` payload (API creds, ports, topology settings). Creates Node, deletes DN + observations + relationships, publishes SSE events.
- **Schema**: `DnPromoteRequest` in `app/schemas.py` — requires `api_username` and `api_password`, optional overrides for name/host/location/ports/topology/charts settings.
- **UI**: "Promote to Anchor Node" button on DN detail page toolbar. Opens modal form pre-filled from DN data. On success, redirects to new AN detail page.
- **Cleanup**: DN record, observations, relationships, and caches all cleared via existing `delete_discovered_node()`.

### Files touched
- `app/schemas.py` — added `DnPromoteRequest`
- `app/routes/discovery.py` — added `promote_discovered_node()` endpoint
- `templates/node_detail.html` — added promote button + modal (conditional on `detail_kind == "discovered"`)
- `static/js/app.js` — added `initDnPromotion()` with modal open/close, form submit, API call, redirect
- `tests/test_dn_promotion.py` — schema validation tests
- `CHANGELOG.md`, `docs/USER_GUIDE.md`, `docs/AGENT_HANDOFF.md`, `docs/CODE_DOCUMENTATION.md` — updated

### Verification
1. Compile check passes (`python -m compileall app tests alembic`)
2. Schema tests validate required fields, extra field rejection, port ranges, invalid topology units
3. No new migration needed — no DB schema changes (promotion uses existing Node model)

### Gaps / next steps
- **Data retention**: `chart_samples` grows indefinitely — needs cleanup policy
- **CDN vendoring**: For air-gapped deployments, vendor Chart.js/jsPDF locally
- **Auto-refresh on charts page**: Currently requires manual re-selection
- **Bulk promotion**: Currently one DN at a time; batch promotion could be added
- **Promoted AN immediate polling**: After promotion, the new AN will be picked up on the next poller cycle (10s seeker poll, 60s charts poll). No immediate forced poll on promotion.

---

## 2026-04-07 — Session: Charts Feature (Complete)

### Branch / commit
- Branch: `seeker-charts`

### What was built
Complete charts data polling, visualization, and PDF reporting feature for Seeker traffic data.

### Architecture
- **Polling**: Single `bwvChartStats(startTime=0, entries=30)` per 60s cycle per node — most recent 30 seconds of raw per-second data. No cursor tracking, no df; duplicates handled by ON CONFLICT DO NOTHING.
- **Server-side bucketing**: `/chart-stats` endpoint aggregates raw samples into 5-minute buckets, emitting min/max/avg rows per bucket. Pre-parses tunnel/channel JSON server-side. 7-day view: ~6K rows to browser instead of 302K.
- **Storage**: `chart_samples` table with `sample_type` column. Unique constraint on `(node_id, timestamp, sample_type)`. ~37 GB for 21 nodes/7 days (30 raw rows/min/node).
- **Charts UI**: Chart.js with dual-axis graphs (throughput left, latency right in yellow). Per-site tunnel charts with envelope bands (from bucket min/max) + rolling average. Clickable stat badges. Smooth/Envelope toggle.
- **Summary report**: Aggregated from raw samples only (accurate). Per-site tunnel table with rowspan grouping. All rates in Kbps/Mbps/Gbps.
- **PDF export**: jsPDF with chart images + formatted summary text

### Files touched
- `app/pollers/charts.py` — single-fetch polling loop (startTime=0, entries=30), logEntries parser
- `app/seeker_api.py` — `get_bwv_chart_stats(df=, entries=)`
- `app/models.py` — `ChartSample` with `sample_type` column
- `app/poller_state.py` — `charts_last_le`, `charts_raw_last_le`
- `app/routes/charts.py` — `/chart-stats` (5-min bucket aggregation), `/chart-summary` (raw-only)
- `app/routes/pages.py` — `/charts` page route
- `app/main.py` — charts poller lifecycle + router mounting
- `templates/charts.html` — charts page with CDN scripts
- `templates/*.html` (all 9) — Charts nav link
- `static/js/app.js` — all chart rendering, badges, envelope/smooth toggle, PDF export
- `static/css/style.css` — charts-specific styles
- `alembic/versions/20260407_0015_*` — create chart_samples table
- `alembic/versions/20260407_0016_*` — add sample_type column
- `tests/test_charts_parser.py` — parser unit tests

### Color scheme
- TX/RX: blue/orange (T0), cyan/pink (T1), green/purple (T2), teal/rose (T3)
- Latency: yellow family only — bright yellow, gold, amber, light yellow
- Yellow reserved exclusively for latency

### Known state
- Migrations 0015 + 0016 must be applied (`alembic stamp 20260407_0015` then `alembic upgrade head` if DB already exists)

### Gaps / next steps
- No auto-refresh (user re-selects to see new data)
- No data retention cleanup (chart_samples grows indefinitely)
- CDN scripts need internet; vendor locally for air-gapped deployments
- Consider data tiering: per-second for 24h, rolled up to 1-min for 7 days

---

## 2026-04-07 — Session: Charts UI Visualization + PDF Export (superseded above)

### Branch / commit
- Branch: `seeker-charts`

### What was done
Added a `/charts` page with interactive Chart.js time-series graphs and client-side PDF export.

### Files touched
- `app/routes/pages.py` — added `/charts` route
- `templates/charts.html` — **new file** — charts page with CDN scripts (Chart.js, html2canvas, jsPDF)
- `templates/*.html` (all 9) — added "Charts" nav link
- `static/js/app.js` — added charts page JS (~230 lines): node selector, time range buttons, throughput/packets/channel charts, PDF export
- `static/css/style.css` — added charts-specific CSS (~60 lines)
- `CHANGELOG.md`, `docs/USER_GUIDE.md`, `docs/CODE_DOCUMENTATION.md`, `docs/AGENT_HANDOFF.md`

### Verification
- Compile check passes
- Charts page loads, node dropdown populates, charts render with real data
- PDF export captures themed charts

### What was done (continued)
- Added `/api/nodes/{node_id}/chart-summary` endpoint with server-side aggregation of tunnel/channel/user data
- Summary table below graphs shows per-tunnel avg TX/RX (Kbps/Mbps/Gbps), avg latency (ms), user totals
- Mate site IDs resolved from seeker_detail_cache mates list
- Summary table included in PDF export as formatted text
- Added `_formatBps()` utility for bits-per-second formatting

### Gaps / next steps
- No auto-refresh on the charts page (user must re-select to see new data)
- CDN scripts require internet access; vendor locally for air-gapped deployments

---

## 2026-04-07 — Session: Charts Data Polling Feature

### Branch / commit
- Branch: `seeker-charts` (off `main`)

### What was done
Implemented a 60-second polling loop that collects per-second traffic counters from each Seeker node via the `bwvChartStats` API endpoint and stores them in PostgreSQL for weekly reporting.

### Files touched
- `app/seeker_api.py` — added `get_bwv_chart_stats()` function
- `app/models.py` — added `ChartSample` model with `(node_id, timestamp)` unique constraint
- `app/poller_state.py` — added `charts_last_le` dict and `charts_poll_task` handle
- `app/pollers/charts.py` — **new file** — polling loop + `logEntries` parser + bulk insert with dedup
- `app/routes/charts.py` — **new file** — `GET /api/nodes/{node_id}/chart-stats` endpoint
- `app/main.py` — launch/cancel charts poller in lifespan, mount charts router
- `alembic/versions/20260407_0015_create_chart_samples_table.py` — **new migration**
- `tests/test_charts_parser.py` — **new test** — unit tests for `parse_log_entries()`
- `CHANGELOG.md` — added feature entries
- `docs/USER_GUIDE.md` — added chart-stats API to API Areas
- `docs/CODE_DOCUMENTATION.md` — added charts polling to Data Flow, route listing
- `docs/AGENT_HANDOFF.md` — this entry

### Verification
- `python -m compileall app tests alembic` — passes (no syntax errors)
- Unit tests: same 7 pre-existing import failures (missing deps in env), no new failures
- Parser logic verified via inline assertions

### Key design decisions
- Uses `pg_insert().on_conflict_do_nothing()` for dedup — silently skips duplicates
- Per-node `last_le` cursor tracked in `PollerState.charts_last_le` (in-memory only, resets on restart to re-fetch with `startTime=0`)
- Channel and tunnel data stored as JSON text in `channel_data` / `tunnel_data` columns (variable structure per node)
- 65-entry over-request covers gaps from BV restarts

### Gaps / next steps
- No Redis state persistence for `charts_last_le` — cursor resets on app restart (acceptable: dedup prevents duplicate inserts)
- No data retention policy / cleanup for old chart_samples rows
- No aggregation endpoint for weekly rollups (mentioned as optional in spec)
- No frontend UI for chart visualization

---

## 2026-04-06 — Session: Final Documentation Pass (Both PRs Merged to Main)

### Branch / commit
- Branch: `main` (both PRs merged)
- PR #1: `claude/optimize-seeker-polling-jbU5K` — Seeker Polling Optimization
- PR #2: `bugfix/session-fixes` — SSE Connection Flood & Feedback Loop Fixes

### What was done
Documentation-only session. Updated `docs/CODE_DOCUMENTATION.md` with a **Performance Anti-Patterns** warnings section codifying the six patterns that caused severe production problems across both PRs. These warnings are intended to prevent regressions by future developers or agents. Also updated SSE and dashboard poller documentation to reflect batched publishing and 10s publish interval.

### Files touched
- `docs/AGENT_HANDOFF.md` — this consolidated entry
- `docs/CODE_DOCUMENTATION.md` — added Performance Anti-Patterns section; updated SSE/dashboard docs
- `CHANGELOG.md` — verified complete (both PRs already documented under Unreleased)
- `docs/USER_GUIDE.md` — verified complete (no operator-visible changes needed)

### Summary of both PRs (for future reference)

**PR #1 (Seeker Polling Optimization)** touched: `app/pollers/seeker.py`, `app/seeker_api.py`, `app/state_manager.py`, `app/main.py`, `app/poller_state.py`, `app/pollers/dashboard.py`, `static/js/app.js`, `app/routes/pages.py`, `docker-compose.yml`

**PR #2 (SSE Connection Flood & Feedback Loop Fixes)** touched: `static/js/app.js`, `app/routes/discovery.py`

### Known gaps / next steps
- SSE reconnect uses fixed 10s delay — could add exponential backoff for prolonged outages
- Submap discovery is loaded once on page load; if a submap is created mid-session, its discovery data won't appear until next page load
- `wan_tx_bps_channels` / `wan_rx_bps_channels` not yet surfaced in Pydantic schema or frontend tooltip
- `REMOTE_SITE_CFG_CACHE` in `seeker_api.py` is in-memory only — could be moved to Redis for cross-restart persistence

---

## 2026-04-06 — Session: SSE Connection Flood & Feedback Loop Fixes

### Branch / commit
- Branch: `bugfix/session-fixes` (merged to `main`)

### What was built this session

**SSE connection flood fix (static/js/app.js)**
- `connectNodeStateStream()` now checks `readyState !== CLOSED` before opening a new EventSource — prevents duplicate connections
- SSE `onerror` handler closes the connection and manually reconnects after 10s instead of relying on EventSource auto-reconnect (which retries immediately and floods the server)
- Removed `connectNodeStateStream()` call from `startTopologyTimers()` — SSE is connected once at DOMContentLoaded for all pages, not re-opened per timer start

**Per-submap fetch storm fix (static/js/app.js)**
- Removed per-submap `/api/topology/maps/{id}/discovery` fetch from `refreshTopologyStructure()` — was firing on every timer tick
- Removed per-submap `/api/topology/maps/{id}/discovery` fetch from `refreshTopologyPage()` — same issue on both main map and submap paths
- Submap discovery data is now loaded once on page load only, not re-fetched on timer/SSE cycles

**Discovery feedback loop fix (app/routes/discovery.py)**
- `dn_discovered` events now only published for genuinely new peers (tracked via `newly_created_site_ids` set)
- Previously, the GET endpoint published events for ALL peers on every request, creating an infinite fetch-event-fetch loop

**Link tooltip throttling (static/js/app.js)**
- `_throttledLinkTooltipRefresh()` uses setTimeout(5000) — only refreshes when a tooltip is actually pinned
- Prevents rapid-fire tooltip HTTP requests during SSE update bursts

### Files touched
- `static/js/app.js` — SSE guard, manual reconnect, removed per-submap fetches from timers, throttled link tooltip
- `app/routes/discovery.py` — feedback loop fix (only publish for new peers)

### Verification
- `python -m compileall app tests alembic` — clean
- `python -m unittest discover -s tests` — same pre-existing state

### Performance warnings documented
These patterns caused severe production problems and must not be reintroduced:
1. **NEVER publish per-node SSE events in a loop** — use batched snapshots
2. **NEVER fetch per-submap endpoints on a timer** — fetch once on page load, cache results
3. **NEVER publish SSE events from a GET endpoint** — creates feedback loops
4. **SSE connections must be guarded** — EventSource auto-reconnects aggressively; use manual reconnect with backoff
5. **Seeker rate fields are Bytes/s** — multiply by 8 for bits/s display
6. **Seeker API: use single-session login** — concurrent logins trigger rate limiting

### Known gaps / next steps
- SSE reconnect uses fixed 10s delay — could add exponential backoff for prolonged outages
- Submap discovery is loaded once on page load; if a submap is created mid-session, its discovery data won't appear until next page load

---

## 2026-04-05 — Session: Optimize Seeker Polling Performance

### Branch / commit
- Branch: `claude/optimize-seeker-polling-jbU5K`

### What was built this session

**Seeker poller fast/slow split**
- Moved `resolve_site_name_map()` out of the fast poll loop into a new `site_name_resolution_loop` (30s cadence, 10s startup delay)
- Fast path: `load_node_detail()` now only applies already-known site names from `_collect_known_site_names()` — zero HTTP overhead
- Slow path: `site_name_resolution_loop()` walks cached tunnel lists, finds unknown peer site names, probes them, and patches the cache in-place
- Added timing logs: each poll cycle and resolution cycle logs elapsed time
- Added `site_name_resolution_task` to `PollerState` and wired into lifespan start/stop

**Single-session Seeker login**
- New `seeker_fetch_all()` in `seeker_api.py` performs 1 login + 3 data requests per node per cycle (was 3 separate login/request pairs)
- Removed scheme fallback — only the operator-configured scheme (http/https) is used; no silent retry on the other scheme

**Seeker poll concurrency and interval**
- Added `SEEKER_POLL_CONCURRENCY = 20` asyncio semaphore in `pollers/seeker.py` — allows scaling to 300+ nodes without flooding
- `SEEKER_POLL_INTERVAL_SECONDS` raised to 10s (was 5s) for a stable, sustainable cadence after fast/slow split

**Rate unit fix**
- Seeker rate fields (`txChanRate`, `rxChanRate`, `txTotRateIf`, `rxTotRateIf`) are Bytes/s, not bits/s
- Applied ×8 conversion in `_format_rate()` and in `normalize_bwv_stats()` for both interface-total and channel-sum fields
- `wan_tx_bps_channels` / `wan_rx_bps_channels` (sum of per-channel rates) added alongside existing `wan_tx_bps` / `wan_rx_bps`

**Sticky yellow fix**
- `_publish_dashboard_to_redis` in `pollers/dashboard.py` now publishes windowed rows so `rtt_state` correctly recovers from yellow; previously the state could get stuck yellow indefinitely

**SSE live updates — node detail and link tooltip**
- Node detail page: tunnels and channels tables now re-render on SSE node_update events (was static after initial page load)
- Link tooltip: stat cache cleared on SSE events; if the tooltip is pinned it auto-refreshes; link stats cache TTL reduced from 10s to 4s

**App logging**
- Added `logging.basicConfig(level=logging.INFO)` to `app/main.py` so INFO-level logs from application startup and pollers are visible

**Redis logging visibility**
- Promoted all Redis failure/error log messages in `state_manager.py` from `logger.debug()` to `logger.warning()`

### Files touched
- `app/pollers/seeker.py` — fast/slow split, single-session login, concurrency semaphore, 10s interval, `_collect_known_site_names`, `_apply_known_site_names`, `site_name_resolution_loop`
- `app/pollers/dashboard.py` — sticky yellow fix (`_publish_dashboard_to_redis` windowed rows)
- `app/poller_state.py` — added `site_name_resolution_task`
- `app/main.py` — wired `site_name_resolution_loop` into lifespan; added `logging.basicConfig`
- `app/seeker_api.py` — `seeker_fetch_all()` single-session login; ×8 rate conversion; `wan_tx_bps_channels` / `wan_rx_bps_channels`
- `app/state_manager.py` — promoted Redis failure logs to warning level
- `static/js/app.js` — node detail SSE re-render (tunnels/channels); link tooltip cache clear + auto-refresh on SSE; link stats cache TTL 4s
- `docs/USER_GUIDE.md`, `docs/CODE_DOCUMENTATION.md`, `CHANGELOG.md`, `docs/AGENT_HANDOFF.md`

### Verification
- `python -m compileall app tests alembic` — clean
- `python -m unittest discover -s tests` — same pre-existing failures (missing deps in env)

### Known gaps / next steps
- **Schema pass-through:** `wan_tx_bps_channels` / `wan_rx_bps_channels` are available in the normalized stats dict but not yet surfaced in `NodeDashboardAnchorRow` Pydantic schema or the frontend tooltip — add when UI comparison is needed
- **Seeker UI field mapping:** The Seeker UI "Throughput" column appears to use `txChanRate[i]` per channel; the `txTotRateIf` value is the aggregate interface rate. Operators seeing a mismatch should compare against the channel-sum fields.
- **Resolution cache persistence:** `REMOTE_SITE_CFG_CACHE` in `seeker_api.py` is in-memory only. Could be moved to Redis for cross-restart persistence.

---

## 2026-04-05 — Session: Modular Architecture Rebuild (Phases 1–5)

### Branch / commit
- Branch: `claude/ecstatic-hamilton-bTOp5`

### Phase 4: Frontend SSE Migration
- Changed `connectNodeStateStream()` to connect to `/api/stream/events` (all channels) instead of `/api/stream/node-states`
- Added SSE event handlers: `service_snapshot`, `service_update`, `dn_discovered`, `dn_removed`, `structure_changed`
- Connected SSE from `DOMContentLoaded` on ALL pages (not just topology)
- Removed `setInterval` polling for node dashboard, services dashboard, main dashboard, and node detail pages
- Only topology structure refresh timer remains (safety net for submap/link/DN count re-fetch)
- Added `_updateNodeDashboardFromSSE()` — updates `currentNodeDashboardPayload` rows in-place and re-renders
- Added `_updateNodeDetailFromSSE()` — updates detail page summary gauges live from SSE node_update events
- Added `_rerenderServicesIfVisible()` — re-renders services dashboard/main dashboard services from SSE service_update events
- `loadServicesDashboard()` now saves payload to `topologyDashboardServicesPayload` for SSE to update

### Phase 3: Multi-Channel Redis Pub/Sub
- Extended `app/state_manager.py` with 4 channels: `smp:node-updates`, `smp:services`, `smp:discovery`, `smp:topology-structure`
- Added `publish_service_state()`, `publish_discovery_event()`, `publish_topology_change()`
- Added `subscribe_channels()` for multi-channel pub/sub subscription
- Added `get_all_service_states()` for service snapshot on SSE connect
- Wired service poller (`app/pollers/services.py`) to publish after each check
- Wired discovery routes to publish `dn_discovered`/`dn_removed`
- Wired topology link CRUD + map CRUD + node create/delete to publish `structure_changed`
- Created unified SSE endpoint `GET /api/stream/events?channels=node-states,services,discovery,topology-structure`
- Kept legacy `/api/stream/node-states` as backward-compatible alias
- Service snapshot emitted as `service_snapshot` event on SSE connect

### Phase 5: Redis Cache Warm-Up
- Added `update_seeker_cache()` and `get_all_seeker_cache()` to `state_manager.py` — stores seeker detail in Redis keys `smp:seeker-cache:{node_id}` with 30s TTL
- Wired `pollers/seeker.py` to write to Redis after each seeker detail cache update (both success and error fallback paths)
- Added `_warm_caches_from_redis()` to `main.py` lifespan — runs after Redis init, before pollers start
- Warms: seeker detail cache, service status cache, and node/DN states
- Eliminates the ~15s cold-start delay — dashboard has data immediately after restart

### All phases complete
- Phase 1: Route extraction (56 routes → 9 modules)
- Phase 2: Poller extraction (5 loops → `app/pollers/`, PollerState)
- Phase 3: Multi-channel Redis pub/sub (4 channels, unified SSE)
- Phase 4: Frontend SSE migration (no more setInterval polling)
- Phase 5: Redis cache warm-up (instant restart recovery)

---

## 2026-04-05 — Session: Modular Architecture Rebuild (Phase 1 + Phase 2)

### Branch / commit
- Branch: `claude/ecstatic-hamilton-bTOp5`

### What was built this session

**Phase 2: Poller + Service Extraction**
- Created `app/poller_state.py` — `PollerState` dataclass holding all 11 mutable cache dicts + task handles
- Created `app/pollers/ping.py` — `ping_host`, `check_tcp_port`, `build_ping_snapshot`, `build_dn_ping_snapshot`, `ping_monitor_loop`
- Created `app/pollers/seeker.py` — `compute_node_status`, `load_node_detail`, `refresh_seeker_detail_for_node`, `seeker_polling_loop`
- Created `app/pollers/dn_seeker.py` — `dn_seeker_polling_loop`
- Created `app/pollers/services.py` — `check_service`, `service_polling_loop`, `merge_service_payload`, `summarize_service_statuses`
- Created `app/pollers/dashboard.py` — `summarize_dashboard_node`, `probe_discovered_node_detail`, `node_dashboard_polling_loop`, `normalize_node_dashboard_window`, `apply_windowed_detail_summary`
- Created `app/services/node_health.py` — `serialize_node`, `refresh_nodes`, `get_node_or_404`, `request_node_telemetry`
- Converted startup/shutdown from `@app.on_event` to FastAPI lifespan context manager
- `main.py` reduced from ~1,250 → 221 lines (total 2,612 → 221, 92% reduction)
- All poller functions now receive `PollerState` as first parameter
- Backward-compatible wrapper functions in `main.py` inject `_ps` so route modules work unchanged

### Verification
- `python -m compileall app tests alembic` — clean
- `python -m unittest discover -s tests` — same 3 pre-existing failures
- All 20 backward-compatible names verified in `main.py`

---

## 2026-04-05 — Session: Modular Architecture Rebuild (Phase 1)

### Branch / commit
- Branch: `claude/ecstatic-hamilton-bTOp5`
- All changes committed and pushed

### What was built this session

**Route extraction (Phase 1 of 5)**
- Extracted all 56 route handlers from `app/main.py` into 9 route modules under `app/routes/`
- `app/routes/pages.py` — 9 HTML page routes
- `app/routes/system.py` — `/api/status`
- `app/routes/nodes.py` — `/api/nodes` CRUD, detail, refresh, telemetry, bwvstats, flush-all
- `app/routes/services.py` — `/api/services` CRUD, `/api/dashboard/services`
- `app/routes/dashboard.py` — `/api/dashboard/nodes`, `/api/node-dashboard`
- `app/routes/topology.py` — `/api/topology`, links CRUD, editor-state
- `app/routes/maps.py` — `/api/topology/maps` CRUD, objects, links, bindings
- `app/routes/discovery.py` — `/api/discovered-nodes`, submap discovery
- `app/routes/stream.py` — SSE endpoints
- `main.py` reduced from 2,612 → ~1,250 lines (non-route code: constants, globals, helpers, polling loops, startup/shutdown)
- Route handlers use deferred imports (`from app.main import ...`) to access shared state — this is an intentional temporary pattern that Phase 2 will replace with PollerState injection

### Verification
- `python -m compileall app tests alembic` — all files compile clean
- `python -m unittest discover -s tests` — same 3 pre-existing failures (not caused by this change)
- All 56 route handlers verified to reference only names that exist in `main.py`

### Architecture plan (remaining phases)
- Phase 2: Extract pollers + services from main.py → `app/pollers/`, `app/services/`, `app/poller_state.py`
- Phase 3: Multi-channel Redis pub/sub (services, discovery, topology-structure events)
- Phase 4: Frontend SSE migration (eliminate all setInterval polling)
- Phase 5: Move seeker cache to Redis (optional, deferred)

### Known gaps
- Route modules use deferred imports from `app.main` — Phase 2 will replace with proper dependency injection
- Pre-existing test failures (3 failures, 4 errors) unrelated to this change

---

## 2026-04-05 — Session: Backend rework — Redis cache + SSE real-time updates

### Branch / commit
- Branch: `back-end-refactor` (also developed on `claude/ecstatic-hamilton-bTOp5`)
- All changes committed and pushed

### What was built this session

**Redis integration (Phase 1)**
- `app/redis_client.py` — async Redis connection with lazy init, graceful fallback if Redis unavailable
- `app/state_manager.py` — dual-write layer publishing node state to Redis pub/sub channel `smp:node-updates`
- `app/main.py` — wired state_manager into `node_dashboard_polling_loop`, added Redis init/shutdown to startup/shutdown hooks
- `requirements.txt` — added `redis>=5.0`

**SSE endpoint (Phase 2)**
- `GET /api/stream/node-states` — new SSE endpoint with two modes:
  - Redis mode: snapshot on connect + pub/sub push for deltas
  - Fallback mode: 1s poll loop comparing serialized dashboard cache
- Existing `/api/node-dashboard/stream` kept for backward compatibility

**Frontend EventSource (Phase 3)**
- `static/js/app.js` — new `connectNodeStateStream()` replaces `setInterval(refreshTopologyPingStatus, 2000)` with SSE-driven updates
- Targeted DOM updates via `_updateTopologyEntityDOM()` — updates RTT chip, tooltip, and status badge without full redraw
- `applyFullSnapshot()`, `applyNodeUpdate()`, `applyDnUpdate()`, `applyNodeOffline()` handle each SSE event type
- Reconnection indicator in topology header ("reconnecting...")

**Docker infrastructure (Phase 4)**
- `Dockerfile` — Python 3.11-slim, compile check at build time
- `docker-compose.yml` — 3 services: app + PostgreSQL 16 + Redis 7 (ephemeral, no disk persistence)
- `.env.example` — added `REDIS_URL`

### Files touched
- `app/redis_client.py` (new)
- `app/state_manager.py` (new)
- `app/main.py` (modified — imports, polling loop, SSE endpoint, startup/shutdown)
- `static/js/app.js` (modified — SSE handler, polling replacement, targeted updates)
- `requirements.txt` (modified — added redis)
- `.env.example` (modified — added REDIS_URL)
- `Dockerfile` (new)
- `docker-compose.yml` (new)
- `docs/USER_GUIDE.md`, `docs/CODE_DOCUMENTATION.md`, `CHANGELOG.md`, `docs/AGENT_HANDOFF.md`

### Verification
- `python -m compileall app tests alembic` — passes
- `python -m unittest discover -s tests` — 21 pass, 7 pre-existing failures (unrelated to this work)
- Redis is optional — app starts and polls correctly without Redis running

### Known gaps / next steps
- **Node Dashboard SSE**: The node dashboard page (`/nodes/dashboard`) still uses `setInterval` + fetch for its list refresh. Could be wired to the same SSE stream.
- **Service checks**: Not yet published to Redis — services status updates still polled.
- **Load testing**: SSE with 300+ nodes and multiple concurrent clients not yet validated.
- **Docker prod config**: `docker-compose.yml` uses default PG creds — should be parameterized for production.

---

## 2026-04-03 — Session: Topology submap improvements, services cloud, hover focus

### Branch / commit
- Branch: `claude/topology-feature-updates-95FqO`
- Latest commit: `7f577bd` — "fix: kill all visual activity on faded nodes with filter override"
- All commits pushed to origin

### What was built this session

**Submap mesh icon bug fix**
- Padding dots (added to reach 3-dot minimum) were incorrectly colored red. Now white/neutral.

**Services cloud re-enabled**
- `buildTopologyServiceCloudEntity()` wired back into `getTopologyEntities()`
- Bypasses layoutOverrides filter so it always renders
- Only visible on main map — hidden inside submap views via `isTopologyEntityVisible()`

**DN auto-placement — center-out radial spiral**
- First DN placed dead center of the submap stage
- Subsequent DNs spiral outward using golden-angle (137.5°) offset
- 120px exclusion zone around each AN position, 84px minimum separation between DNs
- Saved positions (DB) and layout overrides still take priority

**Discovery link anchor point assignment**
- AN→DN: fixed south (AN) → north (DN)
- DN→DN: geometry-based selection from E/SE/S/SW/W via `pickAnchorPointFromSet()`
- Replaced generic `pickAnchorPointByAngle()` with constrained AP picker

**Hover focus effect (submaps only)**
- Hovering or click-pinning a node fades all unconnected nodes to 12% opacity with desaturation
- Pinned state is render-baked (computed in `fadedEntityIds` set before render loop) — no flash on DOM rebuild
- Transient hover uses DOM-based `applyTopologyHoverFocus()` / `clearTopologyHoverFocus()`
- CSS: `animation: none !important` and `filter: saturate(0) brightness(0.4) !important` on faded nodes and children
- Only active inside submap views, not on main map

**AN tooltips hidden in submaps**
- `isInsideSubmap` check suppresses AN hover tooltip inside submap views

**CLAUDE.md update**
- Added instruction to read `docs/CLAUDE_GIT_INSTRUCTIONS.md` at session start

### Files touched
- `CLAUDE.md` — git instructions reference
- `static/js/app.js` — services cloud wiring, DN placement, AP assignment, hover focus, AN tooltip suppression
- `static/css/style.css` — hover focus fade styling
- `docs/USER_GUIDE.md` — submap interactions, services cloud, hover focus
- `docs/CODE_DOCUMENTATION.md` — hover focus system, DN placement, AP assignment docs
- `CHANGELOG.md` — session entries
- `docs/AGENT_HANDOFF.md` — this entry

### Verification
- Services cloud renders on main map only
- Mesh icon padding dots are white/neutral
- DN placement spirals from center, avoids ANs
- AN→DN links use S→N, DN→DN use geometry-based E/SE/S/SW/W
- Pinned hover focus is solid (no flashing); transient hover has minor flicker (known, acceptable)
- AN tooltips suppressed inside submaps

### Known gaps / next steps
- Transient hover focus (non-pinned) has minor flicker during refresh cycles since it's DOM-based rather than render-baked. Could be improved by storing hovered entity ID in state.
- Documentation updates should be reviewed for consistency with prior entries.

---

## 2026-04-02 — Session: Submap icon/card redesign, DN count bubbles, hover tooltips

### Branch / commit
- Branch: `claude/update-smp-topology-tOXUn`
- Latest commit: `57e6479` — "fix: increase mesh glow intensity, widen radius slightly"
- All commits pushed to origin

### What was built this session

**Submap icon redesign**
- Replaced the generic folder-with-nodes icon with a single glowing mesh network SVG icon for all submaps
- 6 mesh nodes connected by cyan lines with radialGradient glow halos
- Icon rendered inline via `getTopologySubmapIconMarkup()` in `app.js`

**Submap portal card (placemat)**
- Changed submap card shape from circle to rounded-rectangle "portal" card
- Dark background with cyan glow border, hover effects with brightened border
- Added `:not(.topology-submap)` to 6 CSS selectors to prevent `.topology-node` circle styling from overriding submap card
- Reduced submap default size from 160×96 to 120×72 (25% smaller)

**Card layout changes**
- Label moved to top of card, mesh icon centered below
- "Submap" subtitle removed
- Right-click rename in edit mode via `renameTopologySubmap()` function

**Data-driven mesh icon**
- Each dot in the mesh = one DN, colored green (up), red (down), or white/neutral (no data)
- Minimum 3 nodes so the mesh never looks empty; white nodes indicate no data, not "3 up"
- Mesh lines: nearest-neighbor connections (2-3 per node) plus convex hull closing the perimeter
- Cluster scales with node count: small counts cluster tight in center, larger counts expand
- Radial SVG glow behind mesh scales proportionally with cluster size
- Invisible placemat at rest (no border/background); hover only increases mesh glow
- Backend `/api/topology` returns `dn_up`, `dn_down`, `dn_up_names`, `dn_down_names` per submap
- Counts derived from actual discovery endpoint results via frontend fetching `/api/topology/maps/{id}/discovery` per submap in parallel
- Counts cached in `localStorage` (`smp-submap-dn-counts` key) so they persist across page refreshes

**DN hover tooltips**
- Hovering the submap card shows vader-themed tooltip listing all DNs with green/red coloring per status
- Tooltip cleanup on re-render to prevent sticking

### Files touched
- `app/main.py` — per-submap DN count queries, `dn_up_names`/`dn_down_names` in submap payload, `source_anchor_node_id` filter
- `static/js/app.js` — `getTopologySubmapIconMarkup()`, `renameTopologySubmap()`, submap entity rendering, DN counter HTML, tooltip hover handlers, localStorage-backed `_submapDnCountCache`, discovery-derived count fetching
- `static/css/style.css` — `.topology-submap` portal card, mesh icon styling, DN counter circles, vader-themed tooltip, `:not(.topology-submap)` specificity fixes
- `templates/topology.html` — cache-bust version bumps

### Verification
- DN counts match actual displayed nodes in each submap
- Counts persist across page refreshes (hard refresh included) via localStorage
- Tooltips show correct site IDs with green/red coloring
- Right-click rename works in edit mode
- Submap portal cards visually distinct from circular anchor node cards

### Known gaps / next steps
- Backend `/api/topology` still returns approximate `dn_up`/`dn_down` counts from a DB query — these serve as fallback until the frontend discovery fetch completes. Long-term, the backend count logic could be refactored to reuse the discovery endpoint's filtering.
- Documentation requirements added to `CLAUDE.md` this session — all future changes must update USER_GUIDE, CHANGELOG, AGENT_HANDOFF, and CODE_DOCUMENTATION.

---

## 2026-04-01 — Session: DN polling, DN-DN links, discovery link visibility, AP coloring

### Branch / commit
- Branch: `claude/add-claude-documentation-Wax1r`
- Commit: `063d1b2` — "Fix pinned discovery links flashing on every render cycle"
- All commits pushed to origin

### What was built this session

**Pydantic schema fixes**
- Added `rtt_state` and `latency_ms` to `TopologyDiscoveryDiscoveredNode` in `app/schemas.py` — fixed 500 errors on main map `/api/topology/discovery`
- Added default `row_type`, `pin_key`, `detail_url` in `_normalize_discovered_row` in `app/node_dashboard_backend.py` — fixed all DN probes silently failing with `NodeDashboardDiscoveredRow` validation errors

**DN Seeker API polling**
- AN + DN poll intervals set to 5s (`SEEKER_POLL_INTERVAL_SECONDS`, `DN_SEEKER_POLL_INTERVAL_SECONDS`)
- DN probe TTL lowered from 300s to 5s (`discovered_probe_ttl_seconds`)
- `dn_seeker_polling_loop` now also iterates `discovered_node_cache` (in-memory), not just DB rows, so DNs get probed before the submap discovery endpoint persists them

**AN inventory exclusion improvements**
- `seeker_polling_loop` backfills `Node.node_id` from `config_summary.site_id` for ANs with NULL `node_id` — eliminates transient 4-5s window where ANs appear as DNs on cold starts
- Discovery filter does host-based reverse matching: scans AN tunnel `mate_ip` against registered AN hosts to catch ANs without credentials or cache entries

**Discovery link lifecycle — down tunnels**
- New `_tunnel_row_exists()` in `app/node_discovery_service.py` — checks tunnel exists in S&T regardless of ping status
- AN→DN and DN→DN loops use `_tunnel_row_exists` for link creation, `_tunnel_row_is_eligible` only for NEW peer discovery
- Pre-seeds `seen_site_ids` from `discovered_nodes` DB so known-but-down peers still get links
- Links only disappear when Seeker removes the tunnel from S&T entirely

**Discovery link visibility system**
- `visibility: hidden` (not just opacity) for hidden discovery links — prevents CSS animation override
- `revealAllDiscoveryLinks()` shows all links in edit mode
- `revealDownDiscoveryLinks()` removed (reverted approach)
- Pinned/edit links pre-revealed at SVG creation time (`is-link-revealed` in class attr) to prevent flicker on render cycles
- Mouseleave guarded in edit mode so links don't accidentally hide

**AP (anchor point) coloring for down links**
- `getDiscoveryWorstAnchorMap()` — worst-wins scoring for discovery links (down > degraded > healthy)
- AP dots turn red if any connected discovery link is down, even if other links are healthy
- Separate from `getTopologyConnectedAnchorMap` which uses best-wins for authored links

**Edit mode discovery links**
- All discovery links revealed when entering Edit Map mode
- Hidden again (respecting pins) when leaving edit mode

### Files touched
- `app/main.py` — DN polling, AN backfill, discovery endpoint link logic
- `app/node_dashboard_backend.py` — probe TTL, _normalize_discovered_row defaults
- `app/node_discovery_service.py` — `_tunnel_row_exists()` function
- `app/schemas.py` — TopologyDiscoveryDiscoveredNode fields
- `app/topology.py` — (unchanged, referenced for context)
- `static/js/app.js` — discovery link visibility, AP coloring, pre-reveal, edit mode
- `static/css/style.css` — visibility:hidden approach, down-pulse animation scoping

### Known gaps / next steps
- **P5.2.6**: 24hr Timeout & Reappear Logic — stale DNs disappear, re-discovered come back
- **Cleanup**: Site 42 (example site) not showing on submap despite being in tunnel list
- **Cleanup**: DN debug logging still at debug level (appropriate)
- **NSL**: Removed from topology/submaps (HTML commented out) — planned for main dashboard later
- **Main map discovery**: Fixed Pydantic error but main map `/api/topology/discovery` still uses `TopologyDiscoveryPayload` model (submap returns plain dict)

---

## 2026-03-30 — Session handoff (Claudius v0.1.1)

### Branch / commit
- Branch: `Claudius` (pushed to `origin/Claudius`)
- Commit: `82a22e9` — "v0.1.1 — user-authored topology map, WAN/LAN metrics, auto-refresh"
- Base: `a053fd6` (origin/main, v0.9.3)
- Worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`

### What was built this session

**Topology — user-authored map (core architectural shift)**
- Topology is now manually built node-by-node. Auto-discovery is disabled (`NodeDashboardBackend.discovery_enabled = False`).
- `POST /api/nodes/flush-all` clears all node DB tables and in-memory caches.
- Add Node button lives inside the Node Inventory panel (not the header). Click opens the modal, which first closes the inventory panel.
- Node save is now instant — `create_node`, `list_nodes`, and `update_node` no longer make live TCP/API calls; all reads from the backend cache.

**Auto-refresh dropdown**
- Topology header has a dropdown (10 sec / 30 sec / 1 min / 5 min / 30 min / 1 hr) replacing the static refresh button.
- Bug fixed: topology.html had `data-refresh-seconds` on options but the JS handler reads `data-seconds` — corrected to `data-seconds`.
- Handler now uses `target.closest("[data-seconds]")` for robustness.
- Styled to match the Demo dropdown (dark navy, backdrop-filter, blue-border options).

**Ping RTT — separate 15s burst cycle**
- `ping_node_burst()` fires 3 concurrent probes per node every 15s.
- `GET /api/nodes/ping-status` — lightweight endpoint, reads in-memory ping cache only.
- `refreshTopologyPingStatus()` + `updateTopologyPingChips()` — in-place DOM RTT chip updates with no full topology re-render.
- `startTopologyTimers()` orchestrates: Seeker data on user interval, ping on fixed 15s, "Updated X ago" counter on 1s.

**"Updated X ago" indicator**
- Small muted timestamp in the topology header. Updates every second. Hidden until first load.

**WAN / LAN TX/RX split + cumulative totals**
- `seeker_api.py` — `normalize_bwv_stats()` now extracts:
  - `wan_tx_bps` / `wan_rx_bps` from `txTotRateIf` / `rxTotRateIf`
  - `lan_tx_bps` / `lan_rx_bps` from `userRate[0]` / `userRate[1]`
  - `lan_tx_total` / `lan_rx_total` from `totUserBytes` (pre-formatted strings e.g. "1204.5G")
  - `wan_tx_total` / `wan_rx_total` summed from `totChanBytesTx` / `totChanBytesRx`
- `app/schemas.py` — `NodeDashboardAnchorRow` has 8 new fields (all with `extra="forbid"` safe defaults).
- `app/node_projection_service.py` — anchor record builder passes all 8 fields.
- `app/main.py` — `summarize_dashboard_node()` passes all 8 fields from normalized stats.
- `getTopologyEntities()` → `mergeDashboardAnchorState()` — propagates all 8 fields into topology entity objects.

**Topology node card tooltip**
- Hover (or click-to-pin) tooltip now shows: Node ID, RTT, WAN TX/RX rate, LAN TX/RX rate, WAN Total (cumulative), LAN Total (cumulative), CPU, Version.
- Tooltip widened: `min-width: 22rem; max-width: 28rem`.
- Tooltip hidden in edit mode.

**Click-to-pin tooltip**
- Single click on a map node pins the tooltip; click again or click anywhere else dismisses it.
- `topologyState.pinnedTooltipId` tracks the pinned node.
- `document.addEventListener("click", ...)` clears pin on any click that isn't intercepted by `stopPropagation` on the node button.
- Entering edit mode automatically clears any pinned tooltip.

**Node Inventory panel row**
- Anchor rows now show: WAN ↑/↓ rates, LAN ↑/↓ rates, WAN Total cumulative, LAN Total cumulative (replacing the old single Tx/Rx row).

### Files touched this session
- `app/main.py`
- `app/seeker_api.py`
- `app/schemas.py`
- `app/node_projection_service.py`
- `app/node_dashboard_backend.py`
- `static/js/app.js`
- `static/css/style.css`
- `templates/topology.html`

### State of the DB
- PostgreSQL. Node inventory is empty (cleared this session). Discovery is disabled. Add nodes fresh from the Topology page → Add Node.

### Known gaps / next steps
- Windowed metric averaging (10s/30s/1m/5m/30m/1hr) for WAN/LAN rates not yet wired — `node_dashboard_backend.py` stores but does not yet compute per-window averages for the new fields.
- No topology links authored yet — the map has nodes but no link lines between them.
- Cache-bust version in topology.html is `author15`; bump to `author16` on next change.

---

## 2026-03-30 10:15 ET - RTT/status presentation pass
- Scope: Unified the main dashboard RTT chip with the Node Dashboard RTT helper so the card uses the same `rtt_state`-aware status coloring, and tightened the fallback latency threshold for rows missing an explicit RTT state.
- Branch/worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit: working tree only, not yet committed
- Files touched:
  [static/js/app.js](C:\Users\rick4\.codex\worktrees\601a\smp\static\js\app.js)
  [docs/AGENT_HANDOFF.md](C:\Users\rick4\.codex\worktrees\601a\smp\docs/AGENT_HANDOFF.md)
- Verification run:
  - Inspection only, no tests run yet
- Assumptions:
  - The main dashboard RTT chip should follow the same state model as the Node Dashboard row chips.
  - Rows with explicit `rtt_state` should always win over raw ping status.
- Open risks / blockers:
  - The working tree still contains unrelated topology and Node Dashboard edits from other slices.
- Next recommended step:
  - If the visual treatment still feels inconsistent, consolidate the RTT state helper into a single shared renderer utility.

## 2026-03-30 09:55 ET - Topology refresh-sync
- Scope: Aligned `/api/topology` health sourcing with the Node Dashboard summary model and added a passive topology refresh timer that re-pulls topology, discovery, node-dashboard, and cloud-service payloads on the shared refresh cadence.
- Branch/worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit: working tree only, not yet committed
- Files touched:
  [app/main.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\main.py)
  [static/js/app.js](C:\Users\rick4\.codex\worktrees\601a\smp\static\js\app.js)
  [docs/AGENT_HANDOFF.md](C:\Users\rick4\.codex\worktrees\601a\smp\docs\AGENT_HANDOFF.md)
- Verification run:
  - `.\\.venv\\Scripts\\python.exe -m compileall app tests alembic`
- Assumptions:
  - Topology should reuse the same dashboard window/health cadence rather than independently computing freshness.
  - Passive refresh is preferable to a full topology reload so authored layout/editor state stays intact.
- Open risks / blockers:
  - The refresh loop rerenders the topology stage, so an actively edited drawer or drag interaction may still be interrupted by a refresh tick.
  - The worktree still has unrelated topology edits from earlier slices; do not revert them while continuing this task.
- Next recommended step:
  - If needed, make the topology refresh pause while a drawer text input or drag interaction is active.

## 2026-03-30 09:20 ET - Node Dashboard inspection
- Scope: Inspected the current Node Dashboard backend, projection, and frontend render path to map the ownership boundary for the dashboard slice.
- Branch/worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit: working tree only, not yet committed
- Files touched:
  [app/main.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\main.py)
  [app/node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_dashboard_backend.py)
  [app/node_discovery_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_discovery_service.py)
  [app/node_projection_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_projection_service.py)
  [app/node_watchlist_projection_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_watchlist_projection_service.py)
  [static/js/app.js](C:\Users\rick4\.codex\worktrees\601a\smp\static\js\app.js)
  [templates/dashboard.html](C:\Users\rick4\.codex\worktrees\601a\smp\templates\dashboard.html)
  [app/schemas.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\schemas.py)
- Verification run:
  - Inspection only, no tests run yet
- Assumptions:
  - Node Dashboard remains the root operational list view for anchors and discovered nodes.
  - The cache/stream pair in `/api/node-dashboard` is the primary delivery path for the page.
- Open risks / blockers:
  - The working tree currently has unrelated topology edits in `static/js/app.js`, `static/css/style.css`, and `templates/topology.html`; do not revert them while working the dashboard slice.
  - RTT presentation on the dashboard is split between JS classification and theme-specific CSS overrides in `static/css/style.css`.
- Next recommended step:
  - Triage any dashboard-specific bugs in the cache/projection path before touching shared styling or topology code.

## 2026-03-29 20:13 ET - Topology demo-mode persistence
- Scope: Persisted the `/topology` demo-mode selector through the topology editor-state contract so preview mode survives reloads and can be replayed from saved state.
- Branch/worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit: working tree only, not yet committed
- Files touched:
  [app/models.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\models.py)
  [app/schemas.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\schemas.py)
  [app/topology_editor_state_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\topology_editor_state_service.py)
  [static/js/app.js](C:\Users\rick4\.codex\worktrees\601a\smp\static\js\app.js)
  [tests/test_topology_editor_state_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_topology_editor_state_service.py)
  [alembic/versions/20260329_0011_add_topology_editor_state_demo_mode.py](C:\Users\rick4\.codex\worktrees\601a\smp\alembic\versions\20260329_0011_add_topology_editor_state_demo_mode.py)
  [docs/USER_GUIDE.md](C:\Users\rick4\.codex\worktrees\601a\smp\docs\USER_GUIDE.md)
  [CHANGELOG.md](C:\Users\rick4\.codex\worktrees\601a\smp\CHANGELOG.md)
- Verification run:
  - `./scripts/test.ps1`
  - `.\\.venv\\Scripts\\python.exe -m compileall app tests alembic`
- Assumptions:
  - Demo mode is an editor-facing preview state, not a separate public topology page.
  - Persisting the selected mode alongside layout and link-anchor settings is the right long-term behavior.
- Open risks / blockers:
  - The worktree still contains unrelated in-flight edits from other agents.
- Next recommended step:
  - Decide whether demo mode should remain edit-only or also be exposed as a read-only viewer hint.

## Entry Template
```md
## YYYY-MM-DD HH:MM ET - Agent/Task
- Scope:
- Branch/worktree:
- Latest validated commit:
- Files touched:
- Verification run:
- Assumptions:
- Open risks / blockers:
- Next recommended step:
```

## 2026-03-29 15:40 ET - Topology / Services cloud watchlist object
- Scope: Added a moveable and resizable `Services` cloud object to `/topology` and bound its status to the service checks pinned on the main dashboard watchlist.
- Branch/worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit: `d792da9` plus local uncommitted sandbox changes
- Files touched:
  [static/js/app.js](C:\Users\rick4\.codex\worktrees\601a\smp\static\js\app.js)
  [static/css/style.css](C:\Users\rick4\.codex\worktrees\601a\smp\static\css\style.css)
  [templates/topology.html](C:\Users\rick4\.codex\worktrees\601a\smp\templates\topology.html)
  [docs/USER_GUIDE.md](C:\Users\rick4\.codex\worktrees\601a\smp\docs\USER_GUIDE.md)
  [CHANGELOG.md](C:\Users\rick4\.codex\worktrees\601a\smp\CHANGELOG.md)
- Verification run:
  - Attempted `.\scripts\test.ps1`
  - Attempted `.\.venv\Scripts\python.exe -m compileall app tests alembic`
- Assumptions:
  - The "main dashboard pinned services" source of truth remains browser-local pinned service IDs plus `/api/dashboard/services`, so topology computes the bound status client-side.
  - Red status should mean every pinned service is in a non-healthy state (`failed`, `unknown`, or `disabled`); mixed/partial service health should surface as yellow.
- Open risks / blockers:
  - The sandbox venv is broken and points to a missing `C:\Users\rick4\AppData\Local\Programs\Python\Python312\python.exe`, so the standard test and compile commands fail before running.
  - The services cloud currently has no topology links; it is a status object and detail drawer binding only.
- Next recommended step:
  - Verify `/topology` in the running app with a pinned-service mix, then decide whether the services cloud should gain authored links into the topology graph or remain a standalone status object.

## 2026-03-29 10:11 ET - Node Dashboard / AN+DN fail-closed health
- Scope: Extended the disconnected-network fix so both anchor and discovered rows fail closed instead of reusing stale live state when current reachability is unavailable.
- Branch/worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit: `d792da9` plus local uncommitted sandbox changes
- Files touched:
  [app/main.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\main.py)
  [app/node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_dashboard_backend.py)
  [app/node_discovery_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_discovery_service.py)
  [tests/test_node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_node_dashboard_backend.py)
  [tests/test_node_dashboard_summary.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_node_dashboard_summary.py)
  [docs/USER_GUIDE.md](C:\Users\rick4\.codex\worktrees\601a\smp\docs\USER_GUIDE.md)
  [CHANGELOG.md](C:\Users\rick4\.codex\worktrees\601a\smp\CHANGELOG.md)
- Verification run:
  - `.\scripts\test.ps1`
  - `.\.venv\Scripts\python.exe -m compileall app tests alembic`
- Assumptions:
  - The Node Dashboard should be pessimistic when disconnected from the Seeker network: stale live indicators are worse than temporarily showing down.
  - Last-known identity metadata may remain visible, but current reachability indicators must be sourced from fresh checks.
- Open risks / blockers:
  - No local HTTP server was running in the sandbox during this slice, so runtime verification still depends on restarting the app instance the user is actually viewing.
  - Existing unrelated local edits remain in the worktree.
- Next recommended step:
  - Restart the running SMP app from the intended checkout and verify `/nodes/dashboard` against the disconnected-network condition before pushing or syncing to the real repo.

## 2026-03-29 09:52 ET - Node Dashboard / DN stale-live-state fix
- Scope: Fixed discovered-node refresh behavior so off-network or unreachable DNs stop inheriting stale `Up`, RTT, Web, SSH, and traffic state from cache; documented the behavior.
- Branch/worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit: `d792da9` plus local uncommitted sandbox changes
- Files touched:
  [app/node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_dashboard_backend.py)
  [app/node_discovery_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_discovery_service.py)
  [tests/test_node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_node_dashboard_backend.py)
  [docs/USER_GUIDE.md](C:\Users\rick4\.codex\worktrees\601a\smp\docs\USER_GUIDE.md)
  [CHANGELOG.md](C:\Users\rick4\.codex\worktrees\601a\smp\CHANGELOG.md)
- Verification run:
  - `.\scripts\test.ps1`
  - `.\.venv\Scripts\python.exe -m compileall app tests alembic`
- Assumptions:
  - For the Node Dashboard, stale DN health should fail closed once the short ping TTL expires.
  - Last-known DN identity/version can remain visible, but live-state indicators must not remain optimistic when disconnected.
- Open risks / blockers:
  - Anchor nodes use a separate ping/status pipeline and were not changed in this slice.
  - Existing unrelated local edits remain in the sandbox worktree; do not revert them blindly.
- Next recommended step:
  - Fix discovered-node provenance rendering in `/nodes/dashboard` so surfaced-by context is always visible and consistent with the backend relationship data.

## 2026-03-29 10:30 ET - Operational Maps / Sandbox-to-Real Sync
- Scope: Reconciled the sandbox worktree to the latest real-repo baseline, then layered the first authored operational-map slice on top.
- Branch/worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit: `d792da9` pushed to `origin/main`
- Files touched:
  [app/main.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\main.py)
  [app/models.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\models.py)
  [app/schemas.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\schemas.py)
  [app/topology.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\topology.py)
  [app/node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_dashboard_backend.py)
  [app/node_discovery_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_discovery_service.py)
  [app/operational_map_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\operational_map_service.py)
  [static/js/app.js](C:\Users\rick4\.codex\worktrees\601a\smp\static\js\app.js)
  [static/js/operational_maps.js](C:\Users\rick4\.codex\worktrees\601a\smp\static\js\operational_maps.js)
  [static/css/style.css](C:\Users\rick4\.codex\worktrees\601a\smp\static\css\style.css)
  [templates/topology.html](C:\Users\rick4\.codex\worktrees\601a\smp\templates\topology.html)
  [templates/operational_maps.html](C:\Users\rick4\.codex\worktrees\601a\smp\templates\operational_maps.html)
  [alembic/versions/20260327_0007_add_node_topology_columns.py](C:\Users\rick4\.codex\worktrees\601a\smp\alembic\versions\20260327_0007_add_node_topology_columns.py)
  [alembic/versions/20260327_0008_add_node_relationships_table.py](C:\Users\rick4\.codex\worktrees\601a\smp\alembic\versions\20260327_0008_add_node_relationships_table.py)
  [alembic/versions/20260329_0009_add_operational_map_tables.py](C:\Users\rick4\.codex\worktrees\601a\smp\alembic\versions\20260329_0009_add_operational_map_tables.py)
  [tests/test_node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_node_dashboard_backend.py)
  [tests/test_topology.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_topology.py)
  [tests/test_operational_map_schemas.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_operational_map_schemas.py)
  [tests/test_operational_map_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_operational_map_service.py)
- Verification run:
  - `.\scripts\test.ps1`
  - `.\.venv\Scripts\python.exe -m compileall app tests alembic`
  - Real repo runtime smoke on port `8012` against local Postgres
- Assumptions:
  - Sandbox worktree is the build source.
  - Real repo is updated only with validated sandbox commits.
  - Node objects in operational maps must resolve to an anchor `node_id` or discovered `site_id`.
- Open risks / blockers:
  - Real Postgres schema had drifted from Alembic history. DB was stamped to `20260329_0009` because the tables already existed.
  - The authored-map UI is only the first slice: create/select maps, place objects, drag/save, basic inspector.
- Next recommended step:
- Add link authoring and binding UI on `/operational-maps`.

## 2026-03-29 13:05 ET - Operational Maps Reframed Back Into Topology

- Decision:
  - removed the standalone `/operational-maps` page from the sandbox app
  - retained the SNMPc-style ideas as reference material instead of continuing a separate user-facing feature
- Current direction:
  - build on `/topology`
  - keep discovery truth first-class
  - fold authored-layout and binding ideas into topology incrementally
- New references:
  - [CURRENTP_TO_SNMPc_MAP_REFERENCE.md](C:\Users\rick4\.codex\worktrees\601a\smp\CURRENTP_TO_SNMPc_MAP_REFERENCE.md)
  - [TOPOLOGY_EVOLUTION_PLAN.md](C:\Users\rick4\.codex\worktrees\601a\smp\TOPOLOGY_EVOLUTION_PLAN.md)

## 2026-03-30 11:05 ET - Topology / Node Dashboard refresh-source alignment
- Scope:
  - aligned `/api/topology` and `/api/topology/discovery` to the same cached Node Dashboard payload and selected `window_seconds` used by the Node Dashboard UI
  - collapsed topology onto the shared dashboard refresh cadence so map and Services cloud update passively without a hard refresh
  - broadened topology status normalization so dashboard-style values (`healthy`, `degraded`, `offline`, `failed`, `unknown`) map cleanly into topology health
- Branch/worktree:
  - `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit:
  - `a053fd6` plus local uncommitted sandbox changes
- Files touched:
  - [app/main.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\main.py)
  - [app/topology.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\topology.py)
  - [static/js/app.js](C:\Users\rick4\.codex\worktrees\601a\smp\static\js\app.js)
  - [templates/topology.html](C:\Users\rick4\.codex\worktrees\601a\smp\templates\topology.html)
- Verification run:
  - `.\.venv\Scripts\python.exe -m compileall app tests alembic`
- Assumptions:
  - topology health should follow the same Node Dashboard cache rows the operator sees, not independently recompute anchor status
  - the selected dashboard refresh window should be shared by topology API requests so status interpretation stays aligned
- Open risks / blockers:
  - existing unrelated local edits remain in the worktree
  - no live browser/runtime verification was run in this slice, so confirmation still depends on the app instance the user is viewing
- Next recommended step:
  - verify on the running `/topology` and `/nodes/dashboard` views that an anchor state transition appears on both surfaces within the same passive refresh window, then tune around edit-mode refresh interruptions if needed
