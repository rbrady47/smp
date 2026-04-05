# Agent Handoff

This file is the shared handoff log for agents working on SMP.

## How To Use
- Add new entries at the top.
- Keep entries short and concrete.
- Record only what another agent needs to continue safely.
- Do not delete older entries unless they are clearly obsolete and superseded.

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
- **Cleanup**: Site 42 (HubASC-698042-ISKR) not showing on submap despite being in tunnel list
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
