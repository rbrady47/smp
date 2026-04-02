# SMP Code Documentation

> Current state as of 2026-04-02 — branch `claude/update-smp-topology-tOXUn`

---

## Architecture Overview

SMP is a FastAPI monolith with a vanilla JS frontend. All backend logic lives in `app/`, the single-page frontend is `static/js/app.js` + `static/css/style.css`, and HTML is served via Jinja2 templates.

```
Browser ──HTTP──> FastAPI (app/main.py)
                    ├── REST API (/api/*)
                    ├── HTML pages (Jinja2 templates)
                    ├── Background tasks (ping, Seeker polling, service checks)
                    └── SQLAlchemy ──> PostgreSQL
```

### Data Flow

1. **AN Seeker polling** (5s): `seeker_polling_loop()` → `refresh_seeker_detail_for_node()` → `seeker_detail_cache[node.id]`
2. **DN Seeker polling** (5s): `dn_seeker_polling_loop()` → `probe_discovered_node_detail()` → `discovered_node_cache[site_id]`
3. **Ping monitoring** (5s): `ping_monitor_loop()` → `ping_snapshots[node.id]` / `dn_ping_snapshots[site_id]`
4. **Service checks** (30s): `service_polling_loop()` → DB updates
5. **Dashboard projection** (5s): `node_dashboard_polling_loop()` → combines caches into dashboard payload
6. **Frontend refresh** (user-selected): polls `/api/topology/maps/{id}/discovery` → renders topology + discovery links

---

## Backend Files

### `app/main.py` (~2500 lines)

The FastAPI application. Contains routes, background tasks, and the core orchestration logic.

**Constants (lines ~85-95):**
- `PING_INTERVAL_SECONDS = 5.0` — ping burst cycle
- `SEEKER_POLL_INTERVAL_SECONDS = 5.0` — AN Seeker API poll
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
| `seeker_polling_loop()` | 5s | Polls AN Seeker APIs, backfills `node_id` from config |
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
- `get_bwv_cfg(node)` — async, fetches config from Seeker API
- `get_bwv_stats(node)` — async, fetches stats (tunnels, channels, bandwidth)
- `normalize_bwv_stats(raw)` — extracts WAN/LAN tx/rx rates, CPU, site count
- `build_detail_payload(node, ...)` — assembles full node detail from config + stats + routes
- `build_dashboard_link_status(tunnels)` — computes link health from tunnel data

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
- `getTopologySubmapIconMarkup(entity)` — returns inline SVG of the glowing mesh network icon (6 nodes, 10 connection lines, radialGradient glow halos)
- Submap entity HTML: label on top → mesh icon → DN count bubbles at bottom
- DN counts read from `_submapDnCountCache` (localStorage) at render time, falling back to backend payload values
- Per-refresh cycle: `refreshTopologyPage()` fetches `/api/topology/maps/{id}/discovery` for each submap in parallel, counts peers by ping status, updates the localStorage cache, then re-renders
- `renameTopologySubmap(entity)` — right-click rename handler (edit mode only), calls `PUT /api/topology/maps/{id}` with new name

**DN count tooltips:**
- `.topology-submap-dn-up` and `.topology-submap-dn-down` bubbles store site IDs in `data-dn-names` attribute
- `mouseenter` creates a `.topology-submap-dn-tooltip` element positioned above the bubble
- `mouseleave` removes the tooltip; stale tooltips also cleaned up at start of each render cycle
- Tooltip classes: `.dn-tooltip-up` (green text, green border), `.dn-tooltip-down` (red text, red border)

**Entity interactions:**
- **Hover**: reveals discovery links for that entity
- **Click**: pins/unpins tooltip + discovery links
- **Double-click (AN)**: opens HTTPS web session to node
- **Double-click (DN)**: opens HTTPS web session to DN host
- **Right-click (AN/DN)**: opens floating detail panel
- **Right-click (Submap, edit mode)**: rename prompt via `renameTopologySubmap()`
- **Stage click**: clears all pins

**`refreshSubmapDiscovery(submapViewId)` (~line 5274):**
Called on every frontend refresh cycle. Fetches discovery data, builds DN entities, builds discovery link objects (AN→DN and DN→DN with deduplication), detects state changes, and triggers flash animations.

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
- `.topology-submap-dn-counts` — flex container for up/down count bubbles
- `.topology-submap-dn-up` / `.topology-submap-dn-down` — circled green/red numbers (1.6rem diameter)
- `.topology-submap-dn-tooltip` — vader-themed hover tooltip (near-black bg, red/green border glow per variant)
- 6 instances of `.topology-node:not(.topology-cluster)` have `:not(.topology-submap)` added to prevent circle styling from overriding submap card shape

---

## Templates

| Template | Route | Purpose |
|----------|-------|---------|
| `topology.html` | `/topology`, `/topology/maps/{id}` | Topology canvas with edit controls, breadcrumbs, refresh selector |
| `dashboard.html` | `/nodes/dashboard` | AN + DN node dashboard |
| `main_dashboard.html` | `/` | Mission dashboard with pinned nodes/services |
| `node_detail.html` | `/nodes/{id}`, `/nodes/discovered/{site_id}` | Drill-down node detail |
| `nodes.html` | `/nodes` | Node inventory management |
| `services.html` | `/services` | Service check inventory |
| `services_dashboard.html` | `/services/dashboard` | Service health dashboard |
| `index.html` | (landing) | Platform status landing page |

---

## Database Migrations

14 Alembic migrations in `alembic/versions/`, from `0001` (initial nodes table) through `0014` (discovered node map columns). Key additions:
- `0005/0006`: Discovered nodes + observations tables
- `0009`: Operational map tables (views, objects, links)
- `0010/0011`: Topology editor state + demo mode
- `0012`: Node ping fields
- `0013`: Topology links table
- `0014`: DN map positioning columns

---

## Tests

7 test modules using Python `unittest`, SQLite in-memory:

| File | Tests | Coverage |
|------|-------|----------|
| `test_node_dashboard_backend.py` | 823 lines | Dashboard state machine, caching, projection |
| `test_topology.py` | 191 lines | Topology helpers, status mapping, mock payload |
| `test_operational_map_service.py` | 203 lines | Map CRUD operations |
| `test_operational_map_schemas.py` | 75 lines | Schema validation |
| `test_node_dashboard_summary.py` | 71 lines | Summarization logic |
| `test_topology_editor_state_service.py` | 67 lines | Editor state persistence |
| `test_node_watchlist_projection_service.py` | 54 lines | Watchlist filtering |

---

*Last updated: 2026-04-01*
