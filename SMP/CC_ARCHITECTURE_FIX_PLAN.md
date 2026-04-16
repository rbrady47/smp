# CC Prompt: SMP Performance Architecture Fix

## Context

The SMP frontend freezes intermittently with fewer than 20 ANs configured. Deep analysis reveals three interlocking structural problems. This prompt addresses all three in priority order. Each phase is independently deployable.

**IMPORTANT:** Read the full `static/js/app.js` and referenced backend files before making changes. The topology rendering code has 36+ call sites and complex interdependencies. Understand the data flow before modifying anything.

---

## Phase 1: Incremental DOM Rendering (CRITICAL — Do First)

### Problem

`renderTopologyStage()` (~line 4996) rebuilds the **entire** node layer via `innerHTML` on every state change. At 20 nodes this is ~25ms; at 300 it would be 550ms+ of main thread blocking. It's called from **36 locations** including SSE handlers, timer refreshes, user interactions, and edit mode operations.

Each call:
1. Generates HTML strings for ALL visible entities via `.map().join("")` (~line 5062)
2. Assigns to `layer.innerHTML` — destroying all existing DOM nodes
3. Runs `querySelectorAll("[data-topology-id]").forEach()` to re-attach 5 event listeners per node (~line 5204)
4. Calls `drawTopologyLinks()` which does `svg.innerHTML = ""` (~line 5440) then recreates every SVG line and attaches 4 event listeners per link (~line 5531)

The event listeners use anonymous functions, so old closures retain references to `entityMap`, `layer`, and `topologyState` even after the DOM nodes are destroyed.

### Solution: Split renderTopologyStage into full-render and patch-render paths

#### Step 1: Create a DOM node cache

Add a module-level Map to track existing DOM elements, near the other topology globals (~line 35):

```javascript
/** Maps entity ID → DOM button element for incremental updates */
const _topologyDomCache = new Map();
```

#### Step 2: Extract event listener attachment into a reusable function

Currently, event listeners are attached inline inside `renderTopologyStage()` at lines ~5204-5377. Extract them into a standalone function that takes a single button element:

```javascript
function _attachTopologyEntityListeners(button) {
    button.addEventListener("click", (event) => {
        // === COPY the existing click handler body from ~lines 5206-5280 ===
        // Keep all existing logic: level2 cluster handling, node selection,
        // pinnedTooltip, edit mode, etc.
        // IMPORTANT: Use button.getAttribute("data-topology-id") inside the
        // handler rather than closing over a variable from the render loop.
    });

    button.addEventListener("contextmenu", (event) => {
        // === COPY the existing contextmenu handler from ~lines 5281-5310 ===
    });

    button.addEventListener("dblclick", (event) => {
        // === COPY the existing dblclick handler from ~lines 5311-5360 ===
    });

    button.addEventListener("mouseenter", () => {
        const entityId = button.getAttribute("data-topology-id");
        if (!entityId || topologyState.editMode) return;
        if (topologyState.pinnedLinkNodeId && topologyState.pinnedLinkNodeId !== entityId) return;
        revealDiscoveryLinksForEntity(entityId);
        const root = document.getElementById("topology-root");
        if (root?.getAttribute("data-map-view-id")) {
            applyTopologyHoverFocus(entityId);
        }
    });

    button.addEventListener("mouseleave", () => {
        const entityId = button.getAttribute("data-topology-id");
        if (!entityId || topologyState.editMode) return;
        if (topologyState.pinnedLinkNodeId === entityId) return;
        hideDiscoveryLinksForEntity(entityId);
        clearTopologyHoverFocus();
    });
}
```

#### Step 3: Create `_buildEntityHTML(entity, discoveryCounts, clusterStatusCounts)` 

Extract the `.map()` callback body from ~lines 5062-5160 into a standalone function that returns the HTML string for ONE entity. This is used for both initial render and adding new nodes.

```javascript
function _buildEntityHTML(entity, discoveryCounts, clusterStatusCounts) {
    // === COPY the existing map callback body from renderTopologyStage ===
    // The function should accept one entity and return the button HTML string.
    // Remove any closure dependencies on `visibleEntities` or `entityMap` —
    // pass needed data as parameters instead.
}
```

#### Step 4: Create `_patchTopologyEntityDOM(button, entity, discoveryCounts, clusterStatusCounts)`

This is the key function — it updates an EXISTING DOM element in-place instead of rebuilding it. It should update:

- CSS classes (status changes, selection state, edit mode)
- Text content (node name, RTT chip, status badge, counters)
- Data attributes (data-topology-editable, data-map-view-id)
- Style attribute (position via bubbleStyle)
- Cluster footer counts (if entity is a cluster/submap)

```javascript
function _patchTopologyEntityDOM(button, entity, discoveryCounts, clusterStatusCounts) {
    // Update classes
    const newClasses = _computeEntityClasses(entity); // extract class computation from _buildEntityHTML
    if (button.className !== newClasses) button.className = newClasses;

    // Update position (only if entity has positioning data)
    const newStyle = _computeEntityStyle(entity);
    if (button.getAttribute("style") !== newStyle) button.setAttribute("style", newStyle);

    // Update status badge
    const badge = button.querySelector(".topology-status-badge");
    if (badge && entity.status) {
        const badgeClass = `topology-status-badge ${entity.status}`;
        if (badge.className !== badgeClass) badge.className = badgeClass;
        if (badge.textContent !== entity.status) badge.textContent = entity.status;
    }

    // Update RTT chip (reuse logic from _updateTopologyEntityDOM ~line 3393)
    const chip = button.querySelector(".topology-rtt-chip");
    if (chip) {
        const rttState = entity.ping_state || entity.rtt_state || "unknown";
        const chipClass = `topology-rtt-chip rtt-${rttState}`;
        if (chip.className !== chipClass) chip.className = chipClass;
        const rttText = entity.latency_ms != null ? `${entity.latency_ms} ms` : "--";
        if (chip.textContent !== rttText) chip.textContent = rttText;
    }

    // Update data-topology-editable
    const editable = topologyState.editMode ? "true" : "false";
    if (button.dataset.topologyEditable !== editable) button.dataset.topologyEditable = editable;

    // Update cluster footer counts if applicable
    // ... (adapt from existing _buildEntityHTML cluster footer logic)

    // Update aria-label
    const label = entity.name || entity.id;
    if (button.getAttribute("aria-label") !== label) button.setAttribute("aria-label", label);
}
```

**Key principle:** Only touch the DOM when the value actually changed. The `if (old !== new)` guards prevent unnecessary reflows.

#### Step 5: Rewrite `renderTopologyStage()` to use differential updates

Replace the `layer.innerHTML = visibleEntities.map(...)` block (~lines 5062-5160) with:

```javascript
    // Build set of entity IDs that should be visible
    const visibleIds = new Set(visibleEntities.map(e => e.id));

    // 1. Remove nodes that are no longer visible
    for (const [id, button] of _topologyDomCache) {
        if (!visibleIds.has(id)) {
            button.remove();
            _topologyDomCache.delete(id);
        }
    }

    // 2. Update existing nodes, create new ones
    for (const entity of visibleEntities) {
        let button = _topologyDomCache.get(entity.id);
        if (button) {
            // Existing node — patch in-place
            _patchTopologyEntityDOM(button, entity, discoveryCounts, clusterStatusCounts);
        } else {
            // New node — create, attach listeners ONCE, cache it
            const temp = document.createElement("div");
            temp.innerHTML = _buildEntityHTML(entity, discoveryCounts, clusterStatusCounts);
            button = temp.firstElementChild;
            layer.appendChild(button);
            _attachTopologyEntityListeners(button);
            _topologyDomCache.set(entity.id, button);
        }
    }
```

#### Step 6: Apply the same pattern to `drawTopologyLinks()`

Replace the `svg.innerHTML = ""` + full rebuild pattern (~lines 5440-5529) with a similar cache:

```javascript
const _topologyLinkDomCache = new Map(); // link ID → {hit: SVGLineElement, visual: SVGLineElement}
```

In `drawTopologyLinks()`:
1. Build a Set of current link IDs
2. Remove cached links not in the set
3. For existing links, update x1/y1/x2/y2 attributes and class in-place
4. For new links, create SVG elements, attach listeners ONCE, cache them

#### Step 7: Clear the caches on full topology reload

When `topologyPayload` is replaced (e.g., in `refreshTopologyStructure()` at ~line 7967), clear the DOM caches so the next render does a full rebuild:

```javascript
if (topologyResult.status === "fulfilled") {
    topologyPayload = topologyResult.value;
    _topologyDomCache.clear();    // Force full rebuild
    _topologyLinkDomCache.clear();
}
```

This ensures that structural changes (new nodes, removed nodes, reordered layout) still get a clean render, while state-only changes (status, RTT, counters) use the fast patch path.

#### Step 8: Reduce renderTopologyStage() call sites

Many of the 36 call sites can be replaced with targeted updates:

- **SSE `node_update`** (~line 3063, `applyNodeUpdate`): Already calls `_updateTopologyEntityDOM()` which does targeted updates. If status changed, it calls `detectNodeStateChanges()` which calls `renderTopologyStage()`. Change this to only update the specific node's classes/badge, not full render.

- **SSE `snapshot`** (~line 3034, `applyFullSnapshot`): Currently calls `renderTopologyStage()` unconditionally. Change to call `_patchTopologyEntityDOM()` for each node in the snapshot, only doing a full render if the entity *set* changed (new/removed nodes).

- **User click handlers** (~lines 5241-5415): These toggle selection state. Instead of full re-render, just toggle CSS classes on the clicked element and previously-selected element.

- **Edit mode toggles**: These legitimately need a full render (classes change on every node). Keep these as-is.

**Target: reduce full renders from ~36 per interaction cycle to ~5 (page load, edit mode toggle, structural changes, submap navigation).**

---

## Phase 2: Eliminate Dual-Fetch Pattern (CRITICAL — Do Second)

### Problem

Two competing mechanisms fetch the same node state data:

1. **SSE** (`/api/stream/events`): Sends a full `snapshot` on connect, then incremental `node_update`/`dn_update` events as state changes
2. **Timer** (`refreshTopologyStructure()` at ~line 3480): Fetches `/api/topology` + `/api/topology/discovery` + `/api/dashboard/services` every N seconds

Both paths end up calling `renderTopologyStage()`, so every 60 seconds you get a redundant full-state fetch + full DOM rebuild on top of whatever SSE already provided.

### Solution

**Keep SSE as the source of truth for node state.** Keep the timer refresh ONLY for structural data that SSE doesn't cover (topology layout positions, link definitions, submap configuration).

#### Step 1: Split `refreshTopologyStructure()` into two functions

```javascript
// Fetches structural data only — layout, links, submaps
// Called on timer and on SSE structure_changed events
async function refreshTopologyStructure() {
    const root = document.getElementById("topology-root");
    if (!root) return;
    if (topologyState.dragging) return;

    const submapId = root.getAttribute("data-map-view-id");
    if (submapId) {
        if (topologyPayload) renderTopologyStage();
        return;
    }

    const gen = _topologyFetchGeneration;
    try {
        // Only fetch topology structure (layout/links) and services
        // Node STATE comes from SSE, not from this endpoint
        const [topologyResult, dashboardServicesResult] = await Promise.allSettled([
            apiRequest(buildNodeDashboardRequestUrl("/api/topology")),
            apiRequest("/api/dashboard/services"),
        ]);

        if (gen !== _topologyFetchGeneration) return;

        let structureChanged = false;
        if (topologyResult.status === "fulfilled") {
            // Check if structure actually changed before triggering render
            const newPayload = topologyResult.value;
            if (_topologyStructureHash(newPayload) !== _topologyStructureHash(topologyPayload)) {
                topologyPayload = newPayload;
                _topologyDomCache.clear();
                _topologyLinkDomCache.clear();
                structureChanged = true;
            } else {
                // Merge just the node positions/config, keep existing state from SSE
                _mergeTopologyStructure(topologyPayload, newPayload);
            }
        }
        if (dashboardServicesResult.status === "fulfilled") {
            topologyDashboardServicesPayload = dashboardServicesResult.value;
        }

        if (structureChanged && topologyPayload) {
            renderTopologyControls();
            renderTopologyStage();
        }
    } catch (error) {
        console.error("Unable to refresh topology structure", error);
    }
}
```

#### Step 2: Remove `/api/topology/discovery` from the timer fetch

The discovery overlay data is already pushed via SSE `dn_discovered`/`dn_removed` events. Don't re-fetch it on the timer. This saves one full round-trip per refresh cycle.

#### Step 3: Increase the default refresh interval

With SSE providing real-time state updates, the timer only needs to catch structural changes (which are rare — someone adding/removing a node or link in edit mode). Increase the default from 1 minute to 5 minutes, or make it event-driven only (refresh on `structure_changed` SSE event).

---

## Phase 3: Backend Hardening (HIGH — Do Third)

### Problem 1: Poller overlap

All four pollers (`dashboard.py`, `seeker.py`, `dn_seeker.py`, site name resolution) use bare `while True` / `await asyncio.sleep()` loops with **no overlap guard**. If a cycle takes longer than the sleep interval, the next cycle starts immediately, stacking up:
- DB sessions from concurrent cycles
- HTTP connections to Seeker nodes
- asyncio tasks in the event loop

### Solution: Add overlap guards to all pollers

Apply this pattern to each poller's main loop:

```python
_running = False

async def poller_loop(ps: PollerState) -> None:
    global _running
    while True:
        if _running:
            logger.warning("Previous %s cycle still running, skipping", POLLER_NAME)
            await asyncio.sleep(POLL_INTERVAL)
            continue
        _running = True
        try:
            await do_poll_cycle(ps)
        except Exception:
            logger.exception("%s polling cycle failed", POLLER_NAME)
        finally:
            _running = False
        await asyncio.sleep(POLL_INTERVAL)
```

Apply to these files:

| File | Function | Line | Interval |
|------|----------|------|----------|
| `app/pollers/dashboard.py` | `node_dashboard_polling_loop()` | ~255 | 1s |
| `app/pollers/seeker.py` | `seeker_polling_loop()` | ~217 | 10s |
| `app/pollers/seeker.py` | `site_name_resolution_loop()` | ~311 | 30s |
| `app/pollers/dn_seeker.py` | `dn_seeker_polling_loop()` | ~22 | 5s |

### Problem 2: Database pool sizing

`app/db.py` line 16 uses SQLAlchemy defaults (pool_size=5, max_overflow=10 = 15 total). With 4 pollers + SSE connections + API requests, this is too small.

### Solution: Explicit pool sizing

```python
async_engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=3600,
)
```

### Problem 3: `apiRequest()` has no timeout

Already documented in `CC_FRONTEND_FREEZE_FIX.md`. Apply the AbortController timeout from that prompt as part of this work.

### Problem 4: Discovery RAF loop never stops

Already documented in `CC_FRONTEND_FREEZE_FIX.md`. Apply the `discoveryCleanup()` function and RAF guards from that prompt.

### Problem 5: No `visibilitychange` listener

Already documented in `CC_FRONTEND_FREEZE_FIX.md`. Apply the visibility handler from that prompt.

---

## Execution Order

1. **Phase 3 quick wins first** — they're small and independent:
   - `apiRequest()` timeout (from `CC_FRONTEND_FREEZE_FIX.md`)
   - Discovery RAF cleanup (from `CC_FRONTEND_FREEZE_FIX.md`)
   - `visibilitychange` listener (from `CC_FRONTEND_FREEZE_FIX.md`)
   - Poller overlap guards (this document)
   - DB pool sizing (this document)

2. **Phase 1** — incremental DOM rendering:
   - Extract `_buildEntityHTML()`, `_patchTopologyEntityDOM()`, `_attachTopologyEntityListeners()`
   - Add `_topologyDomCache` and `_topologyLinkDomCache`
   - Rewrite `renderTopologyStage()` differential path
   - Rewrite `drawTopologyLinks()` differential path
   - Reduce call sites from 36 to ~5 full renders

3. **Phase 2** — eliminate dual-fetch:
   - Split `refreshTopologyStructure()` 
   - Remove discovery from timer fetch
   - Increase refresh interval

## Validation

### Phase 3 (quick wins):
```bash
# Backend tests
pytest tests/ -v

# Frontend syntax
node -c static/js/app.js

# Manual: verify pages load, SSE connects, topology renders
```

### Phase 1 (incremental rendering):
1. Open topology with all configured nodes visible
2. Open DevTools → Performance → Record for 30 seconds
3. Verify no layout thrashing (long yellow bars in flame chart)
4. Check that SSE `node_update` events do NOT trigger `innerHTML` assignment
5. Check `_topologyDomCache.size` matches visible node count
6. Toggle edit mode on/off — verify it still works (full render path)
7. Click a node — verify selection highlights without full render
8. Add/remove a node in edit mode — verify it appears/disappears correctly

### Phase 2 (dual-fetch elimination):
1. Open DevTools → Network → filter XHR
2. On topology page, verify only ONE `/api/topology` fetch on load
3. Wait 5 minutes — verify no redundant topology fetches (SSE handles state)
4. Trigger a `structure_changed` event — verify topology refreshes

### Soak test:
1. Leave topology open for 30+ minutes
2. Monitor DevTools → Memory tab — should be flat, not growing
3. Monitor "Updated Xs ago" — should keep advancing
4. Interact with the page periodically — should remain responsive

## Files Modified

### Frontend
- `static/js/app.js` — all Phase 1, Phase 2, and frontend Phase 3 changes

### Backend
- `app/pollers/dashboard.py` — overlap guard
- `app/pollers/seeker.py` — overlap guard (2 loops)
- `app/pollers/dn_seeker.py` — overlap guard
- `app/db.py` — pool sizing

## Risk Assessment

- **Phase 3 (quick wins):** Very low risk — additive guards, no behavior changes
- **Phase 1 (incremental rendering):** Medium risk — changes the core render path. The DOM cache approach preserves the existing HTML structure; it just avoids recreating it. The key risk is event handler behavior differences when DOM nodes persist vs. are recreated. Test edit mode thoroughly.
- **Phase 2 (dual-fetch):** Low risk — reduces network calls, doesn't change data format

## Architecture Note

This plan works within the existing vanilla JS architecture. It does NOT require introducing React, Vue, or any framework. The differential DOM update pattern is the same technique used by virtual DOM libraries — we're just applying it manually to the topology render path. The discovery page's custom physics engine would benefit from a quadtree for O(n log n) repulsion at 300+ nodes, but that's a separate optimization and not needed for the current scale.
