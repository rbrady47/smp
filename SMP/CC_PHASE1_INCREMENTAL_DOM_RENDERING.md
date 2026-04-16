# CC Prompt: Phase 1 — Incremental DOM Rendering for Topology

## Context

The SMP frontend freezes intermittently with fewer than 20 ANs configured. The root cause is `renderTopologyStage()` in `static/js/app.js` (~line 4996), which rebuilds the **entire** topology node layer via `innerHTML` on every state change. It is called from **39 locations**. Each call destroys all DOM nodes, regenerates HTML for every visible entity, assigns to `innerHTML`, then re-attaches 5 event listeners per node and 4 per link — all using anonymous functions that prevent cleanup.

**Phase 3 (backend hardening, SSE leak fix, apiRequest timeout, discovery RAF, visibilitychange) is ALREADY DONE** on the working branch. This prompt covers ONLY the incremental DOM rendering work.

**Branch:** Work on the current working branch (should be `cowork/working-state-2026-04-11` or similar). Do `git log --oneline -5` to confirm before starting.

**IMPORTANT:** Read the full `static/js/app.js` before making changes. The topology rendering code has 39 call sites and complex interdependencies across entity types (lvl0 nodes, lvl1 nodes, lvl2 clusters, submaps, discovered nodes, services-cloud). Understand all entity types and their HTML differences before modifying anything.

---

## Commit 0: Poller Overlap Guards (Quick Pre-Commit)

All four backend pollers use bare `while True` / `await asyncio.sleep()` loops with no overlap guard. If a cycle takes longer than the interval, cycles stack.

Add a `_running` flag to each poller:

### File: `app/pollers/dashboard.py`
**Function:** `node_dashboard_polling_loop()` (~line 257)
**Interval:** 1.0s

Add at module level near other globals:
```python
_dashboard_poll_running = False
```

Wrap the loop body:
```python
async def node_dashboard_polling_loop(ps: PollerState) -> None:
    global _dashboard_poll_running
    while True:
        if _dashboard_poll_running:
            logger.warning("Previous dashboard poll cycle still running, skipping")
            await asyncio.sleep(NODE_DASHBOARD_FAST_REFRESH_SECONDS)
            continue
        _dashboard_poll_running = True
        try:
            # ... existing cycle body ...
        except Exception:
            logger.exception("Dashboard polling cycle failed")
        finally:
            _dashboard_poll_running = False
        await asyncio.sleep(NODE_DASHBOARD_FAST_REFRESH_SECONDS)
```

### File: `app/pollers/seeker.py`
**Function 1:** `seeker_polling_loop()` (~line 223), interval 10.0s
**Function 2:** `site_name_resolution_loop()` (~line 325), interval 30.0s

Apply the same pattern with `_seeker_poll_running` and `_site_name_poll_running` respectively.

### File: `app/pollers/dn_seeker.py`
**Function:** `dn_seeker_polling_loop()` (~line 32), interval 5.0s

Apply the same pattern with `_dn_seeker_poll_running`.

**Commit message:** `fix: add overlap guards to all poller loops`

**Validate:**
```bash
python -c "import ast; ast.parse(open('app/pollers/dashboard.py').read())"
python -c "import ast; ast.parse(open('app/pollers/seeker.py').read())"
python -c "import ast; ast.parse(open('app/pollers/dn_seeker.py').read())"
```

---

## Commit 1: Extract Helper Functions from renderTopologyStage()

This commit extracts code from `renderTopologyStage()` into reusable functions WITHOUT changing behavior. After this commit, `renderTopologyStage()` should work identically — it just calls the extracted functions.

### Step 1A: Add the DOM caches

Near the other topology globals (around line 35-50), add:

```javascript
/** Maps entity ID → DOM button element for incremental topology updates */
const _topologyDomCache = new Map();
/** Maps link ID → { hit: SVGLineElement, visual: SVGLineElement, handle1?: HTMLElement, handle2?: HTMLElement } */
const _topologyLinkDomCache = new Map();
```

### Step 1B: Extract `_buildEntityClasses(entity, fadedEntityIds)`

Extract the class computation from the `.map()` callback (~lines 5073-5089) into a standalone function:

```javascript
function _buildEntityClasses(entity, fadedEntityIds) {
    const isSubmap = entity.kind === "submap";
    const isServiceCloud = entity.kind === "services-cloud";
    const isCluster = entity.level === 2;
    const isLvl1 = entity.level === 1;
    const isFocusedUnit = entity.level === 2 && topologyState.focusUnit && topologyState.focusUnit === entity.unit;
    const isDiscovered = entity.kind === "discovered";
    return [
        "topology-entity",
        isCluster ? "topology-cluster" : "topology-node",
        isSubmap ? "topology-submap" : "",
        isDiscovered ? "topology-discovered" : "",
        isServiceCloud ? "topology-service-cloud" : "",
        `topology-status-${getEffectiveTopologyEntityStatus(entity) || "neutral"}`,
        entity.level === 0 ? "topology-node-agg" : "",
        isLvl1 ? "topology-node-lvl1" : "",
        isFocusedUnit ? "is-selected" : "",
        topologyState.selectedEntityIds.has(entity.id) ? "is-multi-selected" : "",
        topologyState.selectedKind === "entity" && topologyState.selectedId === entity.id ? "is-selected" : "",
        topologyState.pinnedTooltipId === entity.id ? "is-tooltip-pinned" : "",
        fadedEntityIds.has(entity.id) ? "is-topology-faded" : "",
    ]
        .filter(Boolean)
        .join(" ");
}
```

### Step 1C: Extract `_buildEntityHTML(entity, discoveryCounts, clusterStatusCounts, fadedEntityIds)`

Extract the full `.map()` callback body (~lines 5063-5158) into a standalone function that returns the HTML string for ONE entity button. The function body is the existing callback body verbatim, except:
- Replace `entity` parameter references with the function parameter
- Use the new `_buildEntityClasses()` for class computation
- Accept `fadedEntityIds` as a parameter (computed in the caller)

```javascript
function _buildEntityHTML(entity, discoveryCounts, clusterStatusCounts, fadedEntityIds) {
    const layout = getTopologyEntityLayout(entity);
    // ... COPY the entire .map() callback body from lines 5064-5158 ...
    // Replace the inline class computation with: _buildEntityClasses(entity, fadedEntityIds)
    // Return the HTML string for one <button>
}
```

**CRITICAL:** Copy the EXACT existing logic. Do not simplify, refactor, or change any behavior. Every entity type (submap, discovered, cluster, lvl0, lvl1, services-cloud) has unique HTML structure — preserve it all.

### Step 1D: Extract `_attachTopologyEntityListeners(button, entityMap)`

Extract the event listener attachment block from inside `renderTopologyStage()` (~lines 5204-5377) into a standalone function. This block is currently inside a `layer.querySelectorAll("[data-topology-id]").forEach((button) => { ... })` loop.

The new function takes a single `button` element and the `entityMap`:

```javascript
function _attachTopologyEntityListeners(button, entityMap) {
    // COPY the existing click handler body from ~lines 5206-5278
    button.addEventListener("click", (event) => {
        // Use button.getAttribute("data-topology-id") to get the entity ID
        // Use entityMap.get(nextId) to look up the entity
        // ... exact existing logic ...
    });

    // COPY the existing contextmenu handler from ~lines 5280-5325
    button.addEventListener("contextmenu", (event) => {
        // ... exact existing logic ...
    });

    // COPY the existing dblclick handler from ~lines 5327-5358
    button.addEventListener("dblclick", (event) => {
        // ... exact existing logic ...
    });

    // COPY the existing mouseenter handler from ~lines 5360-5368
    button.addEventListener("mouseenter", () => {
        // ... exact existing logic ...
    });

    // COPY the existing mouseleave handler from ~lines 5370-5376
    button.addEventListener("mouseleave", () => {
        // ... exact existing logic ...
    });
}
```

**CRITICAL CHANGE:** Inside the click, contextmenu, and dblclick handlers, there are currently 4 locations where `renderTopologyStage()` is called after state changes:
- Line 5241 (click → lvl2 cluster → set focus unit)
- Line 5262 (click → anchor/discovered → toggle tooltip pin)
- Line 5278 (click → toggle entity selection)
- Line 5343/5355 (dblclick → clear selection after opening web view)

**For now, leave these `renderTopologyStage()` calls in place.** They will be replaced with targeted DOM updates in Commit 3. Marking them with a `// TODO: Phase1-patch` comment would help.

### Step 1E: Extract `_attachTopologyLinkListeners(line, link, linkId, svg, handleLayer, stageRect)`

Extract the link event listener attachment block from `drawTopologyLinks()` (~lines 5530-5669) into a standalone function.

```javascript
function _attachTopologyLinkListeners(line, link, linkId, svg, handleLayer, stageRect) {
    // COPY the click handler from ~lines 5530-5561
    line.addEventListener("click", (event) => {
        // ... exact existing logic ...
        // Note: line 5560 calls renderTopologyStage() — keep it for now, mark TODO
    });

    // COPY the contextmenu handler from ~lines 5562-5574
    line.addEventListener("contextmenu", (event) => {
        // ... exact existing logic ...
    });

    // COPY the mouseenter handler from ~lines 5575-5588
    line.addEventListener("mouseenter", () => {
        // ... exact existing logic ...
    });

    // COPY the mouseleave handler from ~lines 5589-5593
    line.addEventListener("mouseleave", () => {
        // ... exact existing logic ...
    });
}
```

### Step 1F: Extract `_attachSubmapDnTooltip(submapBtn, icon)`

Extract the submap DN hover tooltip logic from ~lines 5175-5202 into a standalone function:

```javascript
function _attachSubmapDnTooltip(submapBtn, icon) {
    submapBtn.addEventListener("mouseenter", () => {
        // ... COPY existing mouseenter logic from ~lines 5178-5195 ...
    });
    submapBtn.addEventListener("mouseleave", () => {
        // ... COPY existing mouseleave logic from ~lines 5196-5201 ...
    });
}
```

### Step 1G: Update `renderTopologyStage()` to call extracted functions

Replace the inline code with calls to the new functions:
1. Replace the `.map()` callback with calls to `_buildEntityHTML()`
2. Replace the `layer.querySelectorAll("[data-topology-id]").forEach()` listener block with calls to `_attachTopologyEntityListeners()` per button
3. Replace the submap DN tooltip block with calls to `_attachSubmapDnTooltip()`

Similarly, update `drawTopologyLinks()` to call `_attachTopologyLinkListeners()` instead of inline listener blocks.

**After this step, behavior should be IDENTICAL.** Test by loading the topology page, clicking nodes, toggling edit mode, and verifying everything works as before.

**Commit message:** `refactor: extract topology render helpers for incremental DOM update`

**Validate:**
```bash
node -c static/js/app.js
```

---

## Commit 2: Implement Differential DOM Updates in renderTopologyStage()

This is the key commit. Replace the `innerHTML` wipe-and-rebuild with a differential update that:
1. Removes DOM nodes for entities no longer visible
2. Patches existing DOM nodes in-place for state changes
3. Creates new DOM nodes only for newly-appeared entities
4. Attaches event listeners ONCE at creation time, never re-attaches

### Step 2A: Create `_patchTopologyEntityDOM(button, entity, discoveryCounts, clusterStatusCounts, fadedEntityIds)`

This function updates an existing DOM button element in-place:

```javascript
function _patchTopologyEntityDOM(button, entity, discoveryCounts, clusterStatusCounts, fadedEntityIds) {
    // 1. Update className (only if changed)
    const newClasses = _buildEntityClasses(entity, fadedEntityIds);
    if (button.className !== newClasses) {
        button.className = newClasses;
    }

    // 2. Update position via style attribute (only if changed)
    const layout = getTopologyEntityLayout(entity);
    const newStyle = `left:${layout.x}px; top:${layout.y}px; --topology-bubble-size:${layout.size}px;`;
    if (button.getAttribute("style") !== newStyle) {
        button.setAttribute("style", newStyle);
    }

    // 3. Update data-topology-editable
    const editable = topologyState.editMode ? "true" : "false";
    if (button.dataset.topologyEditable !== editable) {
        button.dataset.topologyEditable = editable;
    }

    // 4. Update status badge
    const badge = button.querySelector(".topology-status-badge");
    if (badge) {
        const effectiveStatus = getEffectiveTopologyEntityStatus(entity) || "neutral";
        const badgeClass = `topology-status-badge ${effectiveStatus}`;
        if (badge.className !== badgeClass) badge.className = badgeClass;
        if (badge.textContent !== effectiveStatus) badge.textContent = effectiveStatus;
    }

    // 5. Update RTT chip (reuse pattern from existing _updateTopologyEntityDOM at ~line 3393)
    const chip = button.querySelector(".topology-rtt-chip");
    if (chip) {
        const rttState = entity.ping_state || entity.rtt_state || "unknown";
        const chipClass = `topology-rtt-chip rtt-${rttState}`;
        if (chip.className !== chipClass) chip.className = chipClass;
        const rttText = entity.latency_ms != null ? `${entity.latency_ms} ms` : "--";
        if (chip.textContent !== rttText) chip.textContent = rttText;
    }

    // 6. Update tooltip RTT
    const tooltipRtt = button.querySelector("[data-tooltip-rtt]");
    if (tooltipRtt) {
        const rttText = entity.latency_ms != null ? `${entity.latency_ms} ms` : "--";
        if (tooltipRtt.textContent !== rttText) tooltipRtt.textContent = rttText;
    }

    // 7. Update WAN TX/RX tooltip
    const wanTxRx = button.querySelector("[data-tooltip-wan-txrx]");
    if (wanTxRx) {
        const text = `${formatRate(entity.wan_tx_bps || 0)} / ${formatRate(entity.wan_rx_bps || 0)}`;
        if (wanTxRx.textContent !== text) wanTxRx.textContent = text;
    }

    // 8. Update LAN TX/RX tooltip
    const lanTxRx = button.querySelector("[data-tooltip-lan-txrx]");
    if (lanTxRx) {
        const text = `${formatRate(entity.lan_tx_bps || 0)} / ${formatRate(entity.lan_rx_bps || 0)}`;
        if (lanTxRx.textContent !== text) lanTxRx.textContent = text;
    }

    // 9. Update WAN Total tooltip
    const wanTotal = button.querySelector("[data-tooltip-wan-total]");
    if (wanTotal) {
        const text = `↑${entity.wan_tx_total || "--"} / ↓${entity.wan_rx_total || "--"}`;
        if (wanTotal.textContent !== text) wanTotal.textContent = text;
    }

    // 10. Update LAN Total tooltip
    const lanTotal = button.querySelector("[data-tooltip-lan-total]");
    if (lanTotal) {
        const text = `↑${entity.lan_tx_total || "--"} / ↓${entity.lan_rx_total || "--"}`;
        if (lanTotal.textContent !== text) lanTotal.textContent = text;
    }

    // 11. Update CPU tooltip
    const cpu = button.querySelector("[data-tooltip-cpu]");
    if (cpu) {
        const text = typeof entity.cpu_avg === "number" && Number.isFinite(entity.cpu_avg)
            ? `${Math.round(entity.cpu_avg)}%` : "--";
        if (cpu.textContent !== text) cpu.textContent = text;
    }

    // 12. Update Version tooltip
    const version = button.querySelector("[data-tooltip-version]");
    if (version) {
        const text = String(entity.version || "--").trim() || "--";
        if (version.textContent !== text) version.textContent = text;
    }

    // 13. Update node name display
    const nameEl = button.querySelector(".topology-node-name");
    if (nameEl) {
        const isDiscovered = entity.kind === "discovered";
        const newName = isDiscovered
            ? escapeHtml(entity.site_id || entity.node_id || entity.name)
            : escapeHtml(getTopologyEntityLabel(entity));
        if (nameEl.textContent !== newName) nameEl.textContent = newName;
    }

    // 14. Update aria-label
    const isSubmap = entity.kind === "submap";
    const isServiceCloud = entity.kind === "services-cloud";
    const isCluster = entity.level === 2;
    const titleText = isSubmap
        ? `Submap: ${entity.name}`
        : isServiceCloud
        ? "Services cloud"
        : isCluster ? `${entity.unit} Edge Nodes` : entity.level === 1 ? `${entity.unit} / ${entity.location}` : entity.name;
    if (button.getAttribute("aria-label") !== escapeHtml(titleText)) {
        button.setAttribute("aria-label", escapeHtml(titleText));
    }

    // 15. Update cluster footer counts if applicable
    if (isCluster) {
        const clusterUpCount = getEffectiveTopologyClusterUpCount(
            entity.unit, clusterStatusCounts.upByUnit.get(entity.unit) || 0
        );
        const discoveredCount = getTopologyDiscoveryCount(entity, discoveryCounts);
        const footerEl = button.querySelector(".topology-cluster-footer");
        if (footerEl) {
            const newFooter = getTopologyClusterFooterMarkup(discoveredCount, clusterUpCount);
            // Compare innerHTML to detect changes (footer is small, this is cheap)
            if (footerEl.outerHTML !== newFooter) {
                const temp = document.createElement("div");
                temp.innerHTML = newFooter;
                if (temp.firstElementChild) {
                    footerEl.replaceWith(temp.firstElementChild);
                }
            }
        }
    }

    // 16. Update resize handle visibility
    const existingHandle = button.querySelector(".topology-resize-handle");
    if (topologyState.editMode && !existingHandle) {
        const handle = document.createElement("span");
        handle.className = "topology-resize-handle";
        handle.setAttribute("data-topology-resize-handle", "true");
        handle.setAttribute("aria-hidden", "true");
        button.appendChild(handle);
    } else if (!topologyState.editMode && existingHandle) {
        existingHandle.remove();
    }
}
```

**Key principle:** Every DOM mutation is guarded with an `if (old !== new)` check. This prevents unnecessary browser reflows. The majority of calls will touch zero DOM properties.

### Step 2B: Rewrite the `renderTopologyStage()` innerHTML block

Replace the innerHTML assignment block (~lines 5062-5160) AND the listener attachment block (~lines 5171-5377) with the differential update logic:

```javascript
    // --- BEGIN DIFFERENTIAL DOM UPDATE ---

    // Build set of entity IDs that should be visible
    const visibleIds = new Set(visibleEntities.map((e) => e.id));

    // 1. Remove cached nodes that are no longer visible
    for (const [id, cachedButton] of _topologyDomCache) {
        if (!visibleIds.has(id)) {
            cachedButton.remove();
            _topologyDomCache.delete(id);
        }
    }

    // 2. Update existing nodes, create new ones
    for (const entity of visibleEntities) {
        let button = _topologyDomCache.get(entity.id);
        if (button) {
            // Existing node — patch in-place (no innerHTML, no listener re-attachment)
            _patchTopologyEntityDOM(button, entity, discoveryCounts, clusterStatusCounts, fadedEntityIds);
        } else {
            // New node — create from HTML, attach listeners ONCE, cache it
            const temp = document.createElement("div");
            temp.innerHTML = _buildEntityHTML(entity, discoveryCounts, clusterStatusCounts, fadedEntityIds);
            button = temp.firstElementChild;
            if (button) {
                layer.appendChild(button);
                _attachTopologyEntityListeners(button, entityMap);
                // Attach submap DN tooltip if applicable
                const submapIcon = button.querySelector(".topology-node-icon-submap[data-submap-dn-all]");
                if (submapIcon) {
                    _attachSubmapDnTooltip(button, submapIcon);
                }
                _topologyDomCache.set(entity.id, button);
            }
        }
    }

    // Clean up stale selection state
    if (topologyState.selectedKind === "entity" && topologyState.selectedId && !visibleIds.has(topologyState.selectedId)) {
        topologyState.selectedKind = null;
        topologyState.selectedId = null;
    }
    topologyState.selectedEntityIds = new Set(
        Array.from(topologyState.selectedEntityIds).filter((entityId) => visibleIds.has(entityId)),
    );

    // Clean up stale DN tooltips
    document.querySelectorAll(".topology-submap-dn-tooltip").forEach((t) => t.remove());

    // --- END DIFFERENTIAL DOM UPDATE ---
```

**REMOVE** the old `layer.innerHTML = visibleEntities.map(...).join("")` block (lines 5062-5160).
**REMOVE** the old `layer.querySelectorAll("[data-topology-id]").forEach(...)` listener block (lines 5204-5377).
**REMOVE** the old submap DN tooltip attachment block (lines 5171-5202).

**KEEP** the post-render logic (lines 5379-5428): `wireTopologyLayoutEditor`, `syncTopologyEntitySelectionStyles`, `stage.onclick`, `drawTopologyLinks`, `revealDiscoveryLinks`, `refreshPinnedLinkTooltip`, `renderTopologyDrawer`, `renderTopologyStateLog`.

### Step 2C: Handle the empty-state case

The empty state check at ~lines 5032-5043 uses `layer.innerHTML = ...`. This is fine — it's only hit when there are zero entities. BUT you must also clear the DOM cache when entering empty state:

```javascript
    if (!visibleEntities.length) {
        // Clear all cached nodes
        for (const [id, cachedButton] of _topologyDomCache) {
            cachedButton.remove();
        }
        _topologyDomCache.clear();
        _topologyLinkDomCache.clear();

        layer.innerHTML = `
            <div class="topology-empty-state">
                <strong>Blank map ready</strong>
                <span>Click Edit Map, then Add Node to place your first Seeker icon.</span>
            </div>
        `;
        drawTopologyLinks(entityMap);
        if (typeof renderTopologyDetailsDrawer === "function") {
            renderTopologyDetailsDrawer(null);
        }
        return;
    }
```

**Commit message:** `feat: implement differential DOM rendering for topology stage`

**Validate:**
```bash
node -c static/js/app.js
```

---

## Commit 3: Implement Differential DOM Updates in drawTopologyLinks()

Apply the same pattern to `drawTopologyLinks()` (~line 5430).

### Step 3A: Rewrite `drawTopologyLinks()`

Replace the `svg.innerHTML = ""` / `handleLayer.innerHTML = ""` wipe (~lines 5440-5441) with differential updates:

```javascript
function drawTopologyLinks(entityMap) {
    const svg = document.getElementById("topology-links");
    const handleLayer = document.getElementById("topology-link-handle-layer");
    const stage = document.getElementById("topology-stage");
    if (!svg || !handleLayer || !stage || !topologyPayload) {
        return;
    }

    const stageRect = stage.getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${Math.max(stage.clientWidth, 1)} ${Math.max(stage.clientHeight, 1)}`);

    // Build set of link IDs that should be visible
    const visibleLinkIds = new Set();

    (topologyPayload.links ?? []).forEach((link, index) => {
        const fromEntity = entityMap.get(link.from);
        const toEntity = entityMap.get(link.to);
        if (!fromEntity || !toEntity) return;
        if (!isTopologyEntityVisible(fromEntity) || !isTopologyEntityVisible(toEntity)) return;
        if (topologyState.view === "backbone" && link.kind === "cluster") return;

        const fromNode = stage.querySelector(`[data-topology-id="${CSS.escape(link.from)}"]`);
        const toNode = stage.querySelector(`[data-topology-id="${CSS.escape(link.to)}"]`);
        if (!(fromNode instanceof HTMLElement) || !(toNode instanceof HTMLElement)) return;

        const linkId = getTopologyLinkId(link, index);
        visibleLinkIds.add(linkId);

        const fromRect = fromNode.getBoundingClientRect();
        const toRect = toNode.getBoundingClientRect();
        const fromCx = fromRect.left + fromRect.width / 2 - stageRect.left;
        const fromCy = fromRect.top + fromRect.height / 2 - stageRect.top;
        const toCx = toRect.left + toRect.width / 2 - stageRect.left;
        const toCy = toRect.top + toRect.height / 2 - stageRect.top;
        const sourcePoint = getEdgeAttachmentPoint(fromRect, stageRect, toCx, toCy, isCircularTopologyEntity(fromEntity));
        const targetPoint = getEdgeAttachmentPoint(toRect, stageRect, fromCx, fromCy, isCircularTopologyEntity(toEntity));

        const shouldPreReveal = link.kind === "discovery" && (
            topologyState.editMode
            || topologyState.pinnedLinkNodeId === link.from
            || topologyState.pinnedLinkNodeId === link.to
        );

        const cached = _topologyLinkDomCache.get(linkId);
        if (cached) {
            // --- Patch existing link elements in-place ---
            // Update hit area coordinates
            cached.hit.setAttribute("x1", String(sourcePoint.x));
            cached.hit.setAttribute("y1", String(sourcePoint.y));
            cached.hit.setAttribute("x2", String(targetPoint.x));
            cached.hit.setAttribute("y2", String(targetPoint.y));

            // Update visual line coordinates
            cached.visual.setAttribute("x1", String(sourcePoint.x));
            cached.visual.setAttribute("y1", String(sourcePoint.y));
            cached.visual.setAttribute("x2", String(targetPoint.x));
            cached.visual.setAttribute("y2", String(targetPoint.y));

            // Update visual line classes (status may have changed)
            const newVisualClass = `topology-link topology-link-${link.kind} topology-link-${getEffectiveTopologyLinkStatus(link, index) || "neutral"}${shouldPreReveal ? " is-link-revealed" : ""} ${topologyState.selectedKind === "link" && topologyState.selectedId === linkId ? "is-selected" : ""}`;
            if (cached.visual.getAttribute("class") !== newVisualClass) {
                cached.visual.setAttribute("class", newVisualClass);
            }

            // Update hit area reveal class
            const newHitClass = `topology-link-hitarea${shouldPreReveal ? " is-link-revealed" : ""}`;
            if (cached.hit.getAttribute("class") !== newHitClass) {
                cached.hit.setAttribute("class", newHitClass);
            }

            // Update dash array
            if (link.link_type === "dotted") {
                if (!cached.visual.getAttribute("stroke-dasharray")) {
                    cached.visual.setAttribute("stroke-dasharray", "8 6");
                }
            } else {
                if (cached.visual.getAttribute("stroke-dasharray")) {
                    cached.visual.removeAttribute("stroke-dasharray");
                }
            }

            // Handle edit mode link handles
            if (topologyState.editMode && topologyState.selectedKind === "link" && topologyState.selectedId === linkId) {
                if (!cached.handle1) {
                    // Create handles
                    _createLinkHandles(cached, linkId, sourcePoint, targetPoint, handleLayer);
                } else {
                    // Update handle positions
                    cached.handle1.style.left = `${sourcePoint.x}px`;
                    cached.handle1.style.top = `${sourcePoint.y}px`;
                    cached.handle2.style.left = `${targetPoint.x}px`;
                    cached.handle2.style.top = `${targetPoint.y}px`;
                }
            } else if (cached.handle1) {
                cached.handle1.remove();
                cached.handle2.remove();
                cached.handle1 = null;
                cached.handle2 = null;
            }
        } else {
            // --- Create new link elements ---
            const hitShape = document.createElementNS("http://www.w3.org/2000/svg", "line");
            hitShape.setAttribute("x1", String(sourcePoint.x));
            hitShape.setAttribute("y1", String(sourcePoint.y));
            hitShape.setAttribute("x2", String(targetPoint.x));
            hitShape.setAttribute("y2", String(targetPoint.y));
            hitShape.setAttribute("class", `topology-link-hitarea${shouldPreReveal ? " is-link-revealed" : ""}`);
            hitShape.setAttribute("data-topology-link-id", linkId);
            if (link.kind) hitShape.setAttribute("data-link-kind", link.kind);
            hitShape.setAttribute("data-link-from", link.from);
            hitShape.setAttribute("data-link-to", link.to);
            svg.appendChild(hitShape);

            const shape = document.createElementNS("http://www.w3.org/2000/svg", "line");
            shape.setAttribute("x1", String(sourcePoint.x));
            shape.setAttribute("y1", String(sourcePoint.y));
            shape.setAttribute("x2", String(targetPoint.x));
            shape.setAttribute("y2", String(targetPoint.y));
            shape.setAttribute("class", `topology-link topology-link-${link.kind} topology-link-${getEffectiveTopologyLinkStatus(link, index) || "neutral"}${shouldPreReveal ? " is-link-revealed" : ""} ${topologyState.selectedKind === "link" && topologyState.selectedId === linkId ? "is-selected" : ""}`);
            shape.setAttribute("data-topology-link-id", linkId);
            shape.setAttribute("data-link-from", link.from);
            shape.setAttribute("data-link-to", link.to);
            if (link.link_type === "dotted") shape.setAttribute("stroke-dasharray", "8 6");
            svg.appendChild(shape);

            // Attach link listeners ONCE
            _attachTopologyLinkListeners(shape, link, linkId, svg, handleLayer, stageRect);
            // Also attach to hit area for click detection
            _attachTopologyLinkListeners(hitShape, link, linkId, svg, handleLayer, stageRect);

            const entry = { hit: hitShape, visual: shape, handle1: null, handle2: null };

            // Create handles if this link is selected in edit mode
            if (topologyState.editMode && topologyState.selectedKind === "link" && topologyState.selectedId === linkId) {
                _createLinkHandles(entry, linkId, sourcePoint, targetPoint, handleLayer);
            }

            _topologyLinkDomCache.set(linkId, entry);
        }
    });

    // Remove links no longer in the visible set
    for (const [id, entry] of _topologyLinkDomCache) {
        if (!visibleLinkIds.has(id)) {
            entry.hit.remove();
            entry.visual.remove();
            if (entry.handle1) entry.handle1.remove();
            if (entry.handle2) entry.handle2.remove();
            _topologyLinkDomCache.delete(id);
        }
    }

    // Handle layer pointerdown for link handle drag — attach ONCE
    // (moved to _createLinkHandles per-handle)
}
```

### Step 3B: Create `_createLinkHandles(entry, linkId, sourcePoint, targetPoint, handleLayer)`

```javascript
function _createLinkHandles(entry, linkId, sourcePoint, targetPoint, handleLayer) {
    [
        { side: "source", x: sourcePoint.x, y: sourcePoint.y, prop: "handle1" },
        { side: "target", x: targetPoint.x, y: targetPoint.y, prop: "handle2" },
    ].forEach((hp) => {
        const handle = document.createElement("button");
        handle.type = "button";
        handle.className = `topology-link-handle-button topology-link-handle-${hp.side}`;
        handle.setAttribute("data-topology-link-handle", linkId);
        handle.setAttribute("data-topology-link-side", hp.side);
        handle.style.left = `${hp.x}px`;
        handle.style.top = `${hp.y}px`;
        handleLayer.appendChild(handle);
        entry[hp.prop] = handle;
    });
}
```

**NOTE:** The link handle pointerdown drag logic (~lines 5596-5668) is currently attached inside `drawTopologyLinks()` via `handleLayer.querySelectorAll("[data-topology-link-handle]").forEach(...)`. This needs to be moved into `_createLinkHandles()` — attach the pointerdown listener to each handle at creation time instead of querying for all handles after the fact. Copy the existing drag logic verbatim.

**Commit message:** `feat: implement differential DOM rendering for topology links`

**Validate:**
```bash
node -c static/js/app.js
```

---

## Commit 4: Clear DOM Caches on Structural Topology Changes

Add cache clears at locations where `topologyPayload` is replaced (structural changes that require a full rebuild):

### Location 1: `refreshTopologyStructure()` (~line 7950)
After `topologyPayload = topologyResult.value;`:
```javascript
if (topologyResult.status === "fulfilled") {
    topologyPayload = topologyResult.value;
    _topologyDomCache.clear();       // Force full rebuild
    _topologyLinkDomCache.clear();
}
```

Apply the same pattern at **both** places in `refreshTopologyStructure()` where `topologyPayload` is assigned — there are two paths in this function: the submap path (~line 7898) and the normal path (~line 7935).

### Location 2: `loadTopologyPage()` (~line 7480)
After the initial topology load assigns `topologyPayload`, clear caches:
```javascript
_topologyDomCache.clear();
_topologyLinkDomCache.clear();
```

### Location 3: `applyFullSnapshot()` (~line 3048)
When SSE sends a full snapshot that may contain structural changes. Clear caches before rendering:
```javascript
if (document.getElementById("topology-root") && topologyPayload) {
    _topologyDomCache.clear();
    _topologyLinkDomCache.clear();
    detectNodeStateChanges();
    detectLinkStateChanges();
    renderTopologyStage();
}
```

**Note on snapshots:** Full snapshots from SSE may include new/removed nodes. Clearing the cache here is conservative — it causes one full rebuild on snapshot, which is infrequent (only on SSE reconnect). Incremental `node_update` events will use the differential path.

**Commit message:** `fix: clear topology DOM caches on structural payload changes`

---

## Commit 5: Reduce Full Render Call Sites

Now replace `renderTopologyStage()` calls at locations where only state (not structure) changed, with targeted DOM updates:

### Category A: State-only changes — replace with `_patchTopologyEntityDOM()`

These call sites change selection, tooltip pinning, or other visual state on ONE entity. They do NOT add/remove entities or change structure:

| Line | Context | Replacement |
|------|---------|-------------|
| 1916 | Click clears pinned tooltip | `syncTopologyEntitySelectionStyles(layer)` — just toggle CSS classes on the relevant buttons |
| 5241 | Click lvl2 cluster → set focus unit | Keep `renderTopologyStage()` — changes filter visibility (structural) |
| 5262 | Click anchor/discovered → toggle tooltip pin | Toggle `is-tooltip-pinned` class on the button, call `revealDiscoveryLinksForEntity()` / `hideDiscoveryLinksForEntity()`. No full render. |
| 5278 | Click → toggle entity selection | Toggle `is-selected` class on clicked button and previously-selected button. No full render. |
| 5343 | Dblclick discovered → clear selection, open web | Toggle `is-selected` class off. No full render. |
| 5355 | Dblclick anchor → clear selection, open web | Same as above. |
| 5415 | Stage click → clear all selection/tooltips | Iterate `_topologyDomCache` to remove `is-selected`, `is-tooltip-pinned`, `is-multi-selected` classes. No full render. |
| 5560 | Link click → toggle link selection | Toggle `is-selected` class on link SVG elements. Update link handle visibility. No full render needed for nodes. Call `drawTopologyLinks(entityMap)` or just toggle classes directly. |
| 8030 | `refreshTopologyPingStatus()` → RTT changed | Use `_patchTopologyEntityDOM()` per changed node. Replace the final `renderTopologyStage()` with a loop over changed nodes. |

### Category B: Structural changes — keep `renderTopologyStage()`

These call sites add/remove entities, change visibility filters, or replace the payload. `renderTopologyStage()` is correct here, and the differential implementation makes it efficient:

| Line | Context | Action |
|------|---------|--------|
| 1774 | `nudgeTopologySelection()` — edit mode drag | Keep — position changes on all selected entities |
| 2032-2034 | Link creation from edge drag (edit mode) | Keep — link structure changed |
| 3051 | `applyFullSnapshot()` | Keep (with cache clear from Commit 4) |
| 5241 | Click lvl2 cluster → changes visibility filter | Keep |
| 6094 | `placeNodeOnSubmap()` — adds entity | Keep |
| 6363 | `deleteSubmapObject()` — removes entity | Keep |
| 6392 | `renameTopologySubmap()` — name change | Keep |
| 6837/6845 | Link context menu → update/rollback link type | Keep |
| 6874/6879 | Link context menu → delete/rollback link | Keep |
| 6993/7004 | Label save/reset in node editor | Keep |
| 7124 | Filter button toggle (location/unit) | Keep |
| 7132 | View mode toggle (backbone/l2) | Keep |
| 7157 | Edit mode toggle | Keep |
| 7198 | Layout reset | Keep |
| 7237 | Demo mode toggle | Keep |
| 7306 | Create submap | Keep |
| 7349 | Fullscreen change | Keep |
| 7480 | `loadTopologyPage()` initial load | Keep |
| 7495/7509 | Resize / popstate handlers | Keep |
| 7898/7935/7983 | `refreshTopologyStructure()` payload update | Keep |
| 8813 | Node save → place on topology | Keep |

### Implementation for Category A replacements:

For **lines 5262, 5278, 5343, 5355, 5415** (all inside `_attachTopologyEntityListeners`):

Create a helper function for toggling selection/tooltip classes without full render:

```javascript
function _syncTopologyEntityClassesById(entityId) {
    const button = _topologyDomCache.get(entityId);
    if (!button) return;
    button.classList.toggle("is-selected",
        topologyState.selectedKind === "entity" && topologyState.selectedId === entityId
    );
    button.classList.toggle("is-multi-selected",
        topologyState.selectedEntityIds.has(entityId)
    );
    button.classList.toggle("is-tooltip-pinned",
        topologyState.pinnedTooltipId === entityId
    );
}
```

Then in each click handler, instead of `renderTopologyStage()`:
1. Track the previously-selected entity ID before changing state
2. Update `topologyState` as before
3. Call `_syncTopologyEntityClassesById(prevId)` to un-highlight the old selection
4. Call `_syncTopologyEntityClassesById(newId)` to highlight the new selection
5. Call `renderTopologyDrawer()` and `renderTopologyStateLog()` if the drawer/state log needs updating

For **line 8030** (`refreshTopologyPingStatus`):

Replace the final `renderTopologyStage()` with:
```javascript
if (changed && topologyPayload) {
    const entities = getTopologyEntities();
    const discoveryCounts = getTopologyDiscoveryCounts();
    const clusterStatusCounts = getTopologyClusterStatusCounts();
    const fadedEntityIds = new Set(); // Ping updates don't change focus state
    for (const nodeId of Object.keys(byId)) {
        // Find the entity and its cached button
        for (const entity of entities) {
            const eNodeId = entity.inventory_node_id ?? entity.id;
            if (String(eNodeId) === String(nodeId)) {
                const button = _topologyDomCache.get(entity.id);
                if (button) {
                    _patchTopologyEntityDOM(button, entity, discoveryCounts, clusterStatusCounts, fadedEntityIds);
                }
                break;
            }
        }
    }
}
```

For **line 1916** (global click clears pinned tooltip):

Replace `renderTopologyStage()` with:
```javascript
if (topologyState.pinnedTooltipId) {
    const prev = topologyState.pinnedTooltipId;
    topologyState.pinnedTooltipId = null;
    _syncTopologyEntityClassesById(prev);
}
```

**Commit message:** `perf: replace state-only renderTopologyStage() calls with targeted DOM updates`

**Validate:**
```bash
node -c static/js/app.js
```

---

## Commit 6: Update `_updateTopologyEntityDOM()` to use cache

The existing `_updateTopologyEntityDOM()` at ~line 3393 uses `stage.querySelector()` to find DOM elements. Now that we have `_topologyDomCache`, update it to use the cache for O(1) lookup:

```javascript
function _updateTopologyEntityDOM(entityId, state) {
    const button = _topologyDomCache.get(entityId);
    if (!button) return;

    // ... rest of the function stays the same, just replace `el` with `button` ...
}
```

Also remove the `const stage = document.getElementById("topology-stage")` and `const el = stage.querySelector(...)` lines.

**Commit message:** `perf: use DOM cache for targeted topology entity updates`

---

## Testing & Validation

### Syntax check after every commit:
```bash
node -c static/js/app.js
python -c "import ast; ast.parse(open('app/pollers/dashboard.py').read())"
python -c "import ast; ast.parse(open('app/pollers/seeker.py').read())"
python -c "import ast; ast.parse(open('app/pollers/dn_seeker.py').read())"
```

### Functional test checklist (after all commits):

1. **Page load:** Topology page renders all configured nodes correctly
2. **SSE updates:** Real-time state changes (status, RTT) update without layout shift
3. **Node click:** Selection highlights correctly, tooltip pins/unpins
4. **Edit mode toggle:** All nodes get editable state, resize handles appear
5. **Edit mode drag:** Nodes move smoothly, position saves
6. **Edit mode link creation:** Drag from node edge creates link
7. **Link selection:** Click link → highlights, shows handles in edit mode
8. **Link context menu:** Right-click link → menu appears, edit/delete work
9. **Submap navigation:** Double-click submap → navigates, back button works
10. **Filter toggles:** Location/unit/view filters show/hide correct nodes
11. **Layout reset:** Reverts positions, nodes re-render in default positions
12. **Window resize:** Nodes reposition, links follow
13. **Fullscreen:** Toggle works, layout adjusts
14. **Demo mode:** Toggle on/off, snapshot renders correctly
15. **Node add/remove:** Add node in edit mode → appears on topology. Remove → disappears.
16. **Submap add node:** Place node on submap → appears
17. **Label editing:** Save/reset custom label → name updates on bubble
18. **Discovery links:** Hover node → discovery links reveal. Pin → stay visible.
19. **Service cloud:** Hover → service tooltip shows. Click → selects.
20. **Discovered nodes:** Render correctly with site_id, right-click opens detail

### Performance validation:

1. Open topology with all configured nodes
2. Open DevTools → Performance → Record for 30 seconds
3. Verify NO `innerHTML` assignments in the flame chart during SSE updates
4. Verify `_topologyDomCache.size` equals visible node count (check via console: `_topologyDomCache.size`)
5. Verify `_topologyLinkDomCache.size` equals visible link count
6. Open DevTools → Memory → Take heap snapshot before and after 5 minutes → verify flat (no leak)

### Soak test:
1. Leave topology open for 30+ minutes
2. Verify "Updated Xs ago" keeps advancing
3. Click nodes periodically — should remain responsive
4. Check DevTools Memory tab — should be flat, not growing

## Files Modified

### Frontend
- `static/js/app.js` — all Commits 1-6

### Backend
- `app/pollers/dashboard.py` — Commit 0 (overlap guard)
- `app/pollers/seeker.py` — Commit 0 (overlap guard, 2 loops)
- `app/pollers/dn_seeker.py` — Commit 0 (overlap guard)

## Risk Assessment

- **Commit 0 (poller guards):** Very low risk. Additive guards, no behavior change. If a cycle runs long, it skips instead of stacking.
- **Commit 1 (extract helpers):** Zero risk. Pure refactor, no behavior change. If extraction is correct, output is identical.
- **Commit 2 (differential node rendering):** Medium risk. Changes the core render path. The `_patchTopologyEntityDOM` function must handle every entity type. Test every entity type (lvl0, lvl1, lvl2 cluster, submap, discovered, services-cloud) individually.
- **Commit 3 (differential link rendering):** Low-medium risk. Links are simpler than nodes (just SVG line elements). The main risk is link handle drag behavior when handles persist across renders.
- **Commit 4 (cache clears):** Low risk. Conservative — clears on any structural change. Missing a clear would cause stale DOM nodes, but the clear is cheap.
- **Commit 5 (reduce call sites):** Medium risk. Highest chance of regression if a call site needs full render but gets a targeted update instead. The classification table above was audited from the actual code. If anything behaves unexpectedly, the safest fix is to revert that specific call site back to `renderTopologyStage()`.
- **Commit 6 (cache lookup):** Very low risk. Just changes `querySelector` to `Map.get()`.

## Rollback

If any commit causes issues, the safest approach is:
1. Revert only the problematic commit (they're independent)
2. If Commit 2 or 3 causes widespread issues, temporarily revert to innerHTML by clearing the cache at the top of each render function: `_topologyDomCache.clear()` / `_topologyLinkDomCache.clear()` — this forces the "create new" path for every entity, which is equivalent to the old behavior but with extracted functions.
