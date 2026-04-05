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
