# SMP User Guide

## Purpose

The Seeker Management Platform (SMP) is an operator-facing web application for managing Seeker nodes, tracking service health, reviewing discovered network state, and building operational views.

This guide is written for users and maintainers who need a practical description of what SMP does today.

## Current Areas

### Main Dashboard

Path: `/`

The Main Dashboard is the high-level landing page for the platform. It is intended to become the wallboard view that shows the most important pinned nodes and services in one place.

Current focus:

- Platform status summary
- Navigation into the main operational areas
- Foundation for pinned node and service views

### Node Dashboard

Path: `/nodes/dashboard`

The Node Dashboard is the primary operational list for Seeker nodes. It combines:

- Anchor nodes managed directly in SMP
- Discovered nodes surfaced from network data

What operators can do here:

- Review anchor and discovered nodes in separate lists
- Search both lists
- Add anchor nodes from the dashboard
- Drill into detailed node views
- Track basic topology metadata such as:
  - `include_in_topology`
  - `topology_level`
  - `topology_unit`

Anchor nodes are the SMP-managed inventory records. Discovered nodes represent nodes learned from the environment and surfaced through the backend discovery pipeline.

Operational note:

- Node Dashboard live status is intentionally pessimistic. If SMP cannot refresh current reachability for an anchor or discovered node, that row will present as down until the backend proves it is reachable again. This avoids stale "up" state when the operator is disconnected from the Seeker network.

### Node Detail

Paths:

- `/nodes/{node_id}`
- `/nodes/discovered/{site_id}`

Node detail pages are intended for drill-down on a single node. The managed anchor-node detail page focuses on operational inspection, including identity and network-related data returned by the backend.

For discovered node detail pages (`/nodes/discovered/{site_id}`), a **Promote to Anchor Node** button appears in the toolbar. Clicking it opens a modal where the operator enters API credentials and configuration. On submit, the DN is converted to a full Anchor Node — the DN record is deleted and the new AN appears in Node Inventory, ready for polling, charts, and topology inclusion.

### Services Dashboard

Path: `/services/dashboard`

The Services Dashboard provides an operational view of backend-polled service checks.

Current service types include:

- URL checks
- DNS checks

What operators can do here:

- Review service status in one place
- Use it as the source for future wallboard pinning

### Managed Nodes

Path: `/nodes`

The Managed Nodes page is the inventory-management view for SMP anchor nodes.

Typical uses:

- Add nodes
- Edit node connection details
- Remove nodes
- Set whether a node should participate in topology views

### Managed Services

Path: `/services`

The Managed Services page is the CRUD view for configured service checks.

Typical uses:

- Add a URL or DNS check
- Remove obsolete checks
- Keep the service inventory aligned with operational needs

### Charts

Path: `/charts`

The Charts page provides time-series traffic visualization for anchor nodes. The backend polls each Seeker node every 60 seconds, collecting the most recent 30 seconds of raw per-second traffic data. When serving to the browser, the API aggregates raw samples into 5-minute time buckets with min/max/avg values for efficient rendering and envelope visualization.

What operators can do here:

- Select an anchor node from the dropdown to view its traffic data
- Choose a time range: 1 Hour, 6 Hours, 24 Hours, or 7 Days
- View charts: User Throughput, Packet Counts, WAN Channel Throughput, and per-site tunnel charts (one card per mate site with throughput + latency on dual axes)
- Toggle between "Envelope" view (shows min/max range band) and "Smooth" view (rolling average only)
- Click stat badges to toggle individual datasets on/off (Avg TX, Avg RX, Latency)
- Export the current view as a PDF report for offline sharing or weekly reporting

Chart features:
- **Envelope bands** show the true min/max range from the Seeker's 30-second decimation windows
- **Rolling average** curve shows the smoothed trend through the data
- **Stat badges** display Avg TX, Avg RX, Peak TX, Peak RX, and Avg Latency — computed from raw per-second samples for accuracy
- **Latency** shown in yellow on the right Y-axis (site tunnel charts only)
- **Colors**: TX/RX use distinct color pairs per tunnel; yellow is reserved exclusively for latency

Below the graphs, a **Summary Report** table shows:
- **User Throughput**: average TX/RX rates (Kbps/Mbps/Gbps), total bytes, and total packets
- **Per-Site Tunnel Summary**: grouped by mate site with rowspan, showing Avg TX, Avg RX, and Avg Latency per tunnel
- **Per-Channel Averages**: average TX/RX rate per WAN channel

All summary statistics are computed from raw per-second samples (not min/max midpoints) for reporting accuracy.

### System Health

Path: `/health`

The System Health page provides a diagnostic view of platform storage usage and background poller status.

What operators can see here:

- **Chart Data Usage**: Total rows in the `chart_samples` table, estimated storage size, oldest/newest sample timestamps, and time span covered
- **Per-Node Breakdown**: Table showing sample count, date range, and time span for each node collecting chart data
- **Storage Badges**: Color-coded chips — green (under 1M rows), yellow (1M–5M rows), red (over 5M rows) — for quick at-a-glance assessment
- **Poller Intervals**: Configured polling cadences for Seeker, Charts, and Services background tasks
- **Platform Info**: Hostname, total node count, charts-enabled node count

A Refresh button allows manual re-fetch of health data.

#### Diagnostic Console

At the bottom of the Diag page is a **Diagnostic Console** for running diag codes. Type a code and press Enter or click Run to query runtime diagnostics.

Useful codes:
- `help` — list all available codes
- `poller:status` — check if all background pollers are running
- `cache:stats` — see how many entries are in each in-memory cache
- `db:pool` — check database connection pool utilization
- `redis:status` — verify Redis connectivity and memory usage
- `node:detail node_id=42` — inspect cached Seeker data for a specific node
- `ping:detail node_id=42` — inspect ping state for a node

Results display as formatted JSON. Recently-used codes appear as clickable chips for quick re-runs. See `docs/DIAG_CODES.md` for the full catalog.

### Fixed Topology View

Path: `/topology`

The current Topology page is still the earlier fixed-layout operational topology view. It is not yet the long-term authored map system.

Current behavior:

- Shows a structured Division C2 Information Network layout
- Uses real node metadata where available
- Supports filtering by location and unit
- Provides a detail drawer for selected nodes, links, and clusters
- Includes a discovery relationship panel for attribution signals
- Includes a moveable, resizable `Services` cloud object whose status is driven by the services pinned to the main dashboard watchlist:
  - green when all pinned services are healthy
  - yellow when the pinned set is mixed or degraded
  - red with a pulse when all pinned services are down, unknown, or disabled
- Includes a demo-mode selector in edit mode for previewing all-up, all-down, or mixed topology states, and remembers that choice in the saved topology editor state
- Topology recovery: if the page encounters an error during background refresh (e.g., after returning from a backgrounded tab), the stage clears gracefully and recovers automatically on the next successful data fetch. No manual reload required.
- Real-time updates via Server-Sent Events (SSE) on all pages: node status, RTT, bandwidth, service check results, and discovery events update automatically without manual page refresh. The node dashboard, services dashboard, main dashboard, node detail page, and topology all receive live updates. If Redis is running, updates are push-based; otherwise the system falls back to polling. A "reconnecting..." indicator appears in the header if the connection drops. Manual refresh buttons remain available as a force-reload fallback.

#### Submaps

Submaps appear as portal-style cards on the main topology map. Each submap represents a drill-in view (e.g., 1BCT, 2BCT, 3BCT) containing its own set of anchor and discovered nodes.

**Visual design:**
- Submaps have no visible border or background at rest — just a label, a mesh network icon, and a subtle glow. The mesh becomes the primary visual element.
- Each dot in the mesh represents one discovered node: green = up, red = down, white = no data (placeholder).
- The mesh cluster grows and shrinks with the number of discovered nodes (minimum 3 nodes). Lines connect nearby nodes with a closed perimeter.
- A soft radial glow behind the mesh scales with the cluster size.
- Hovering a submap slightly increases the glow.

**Hover tooltip:**
- Hover any submap to see a tooltip listing all discovered node site IDs, color-coded green (up) or red (down).

**Interactions:**
- Click a submap card to drill into that submap view.
- Right-click a submap card in edit mode to rename it (opens a prompt dialog).
- Submap cards can be moved and resized in edit mode like other topology entities.

**Inside a submap:**
- Discovered nodes are auto-placed starting from the center of the screen, spiraling outward. They stay clear of anchor nodes (120px clearance) and never overlap each other.
- Discovery links from anchor nodes (AN→DN) always connect south (AN) to north (DN). Links between discovered nodes (DN→DN) use the closest side anchor (E/SE/S/SW/W) based on geometry.
- Hovering or click-pinning a node fades all unconnected nodes to near-invisible, highlighting only the hovered node and its direct connections. Click again or click empty space to clear.
- Anchor node tooltips are hidden inside submaps — only discovered node tooltips are shown.
- Saved positions (from dragging in edit mode) are preserved across page refreshes.

**Services cloud:**
- A services cloud object appears on the main topology map (not inside submaps). It shows the aggregate status of service checks pinned from the Services Dashboard. Green = all healthy, yellow = mixed, red = all down.

This page should be treated as the legacy or transitional topology surface while authored operational maps are built out.

### Operational Maps

Status: paused and removed as a separate UI path.

The standalone `/operational-maps` page is no longer active. The authored-map ideas are being retained as design input for the current `/topology` feature instead of being developed as a separate experience.

Current design direction:

- Separate discovery truth from operator-authored maps
- Start from a blank canvas
- Let operators place authored objects intentionally
- Support submaps for drill-in navigation

Current object types:

- `node`
- `submap`
- `label`

Planned authored-map workflow:

1. Create a map canvas.
2. Add node, submap, and label objects.
3. Assign a node ID to node objects.
4. Bind live SMP data to objects and links.
5. Drill into submaps from parent maps.

Important rules:

- Node objects must be assigned a node ID before status binding is meaningful.
- Submap objects should point to another authored map.
- Discovery topology and operational maps are separate concepts.

## Data Concepts

### Anchor Nodes

Anchor nodes are SMP-managed records created by operators. These are the primary inventory objects that carry connection settings and topology participation settings.

### Discovered Nodes

Discovered nodes are learned from the environment and surfaced through backend discovery logic. They help extend operator visibility beyond manually entered anchor nodes.

Discovered nodes can be **promoted to Anchor Nodes** from the DN detail page. This requires the operator to supply API credentials (username/password). After promotion, the DN is deleted and replaced by a full AN record with polling, charts, and topology support.

### Discovery Topology vs Operational Maps

These are different views with different purposes.

- Discovery topology shows what the system learns from inventory and discovery data.
- Operational maps show what operators intentionally draw and care about.

The current roadmap is to preserve both:

- discovery/truth topology for observed state
- authored operational maps for mission/operator workflow

## API Areas

The current backend exposes several relevant route groups:

- `/api/nodes`
- `/api/services`
- `/api/dashboard/nodes`
- `/api/dashboard/services`
- `/api/topology`
- `/api/topology/discovery`
- `/api/discovered-nodes/{site_id}/promote` — promotes a Discovered Node to an Anchor Node (POST with API credentials)
- `/api/nodes/{id}/chart-stats` — per-second traffic counters (user bytes/packets, channel data, tunnel data) collected every 60s from each Seeker node via `bwvChartStats`. Query with `?start=<epoch>&end=<epoch>&limit=N` for weekly reporting.
- `/api/health` — platform health diagnostics: chart storage metrics (row counts, size estimate, date range, per-node breakdown), node counts, and poller intervals
- Operational-map concepts are being folded back into `/topology` planning rather than exposed as a separate API/UI path for operators right now.

Operational-map API coverage currently includes:

- map view CRUD
- map object CRUD
- object binding create/delete
- map link CRUD
- link binding create/delete
- map detail payload retrieval

## Local Setup

1. Install a full CPython 3.11+ distribution.
2. Run `.\scripts\bootstrap.ps1`.
3. Set `DATABASE_URL` as needed.
4. Run migrations with `.\.venv\Scripts\alembic.exe upgrade head`.
5. Start the app with `.\scripts\dev.ps1 -Reload`.

## Testing

Run:

- `.\scripts\test.ps1`

Notes:

- The repo expects a local virtual environment in `.venv`.
- If the local Python installation path changes after the venv is created, the venv may need to be recreated.

## Documentation Policy

This repository should keep user-facing documentation in markdown so changes are easy to review in version control.

When features change, update:

- `docs/USER_GUIDE.md` for operator-visible behavior
- `CHANGELOG.md` for a concise history of notable changes

## Database Architecture

SMP uses async SQLAlchemy 2.0 with PostgreSQL (psycopg 3.x async mode). All database I/O is non-blocking — route handlers and background pollers use `AsyncSession` so the event loop remains responsive during DB queries. Alembic migrations use a separate sync engine.

## Polling Architecture

SMP polls each anchor node's Seeker API on two cadences:

- **Fast path (10s):** Fetches config, stats, and learnt routes in a single login session (one login + three requests per node). Updates the dashboard cache immediately. Site names that are already known from previous polls or other nodes are applied instantly. Up to 20 nodes are polled concurrently.
- **Slow path (30s):** Resolves unknown tunnel-peer site names by probing remote Seekers for their config. This is the expensive step (each probe requires HTTP login + request). Resolved names are patched into the cached data and persist across cycles.

This split keeps the dashboard responsive (fresh data every 10s) while site names fill in progressively over the first few minutes after startup.

### WAN Throughput Metrics

SMP shows two WAN throughput values:

- **Interface total** (`wan_tx_bps` / `wan_rx_bps`): From `txTotRateIf` / `rxTotRateIf`. Includes all traffic on the WAN interface (tunnel payload + overhead + non-tunnel).
- **Channel sum** (`wan_tx_bps_channels` / `wan_rx_bps_channels`): Sum of per-channel rates (`txChanRate` / `rxChanRate`). Matches what the Seeker UI shows per-channel. This is the tunnel payload rate only.

The channel-sum rate is typically slightly lower than the interface total because it excludes overhead.

## Known Current State

- The platform is still prototype-stage.
- The fixed Topology page remains available.
- SNMPc-style authored-map ideas are still relevant, but they are now reference material for future topology evolution rather than a separate active feature path.
- Some backend support for authored operational maps already exists before the full editor workflow is complete.
