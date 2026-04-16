# CC Prompt: Fix DOM Cache Clear Duplication Bug

## Problem

After implementing the incremental DOM rendering in `renderTopologyStage()`, there is a **DOM node duplication bug** that occurs whenever `_topologyDomCache` is cleared. This happens at all 5 `topologyPayload =` assignment sites (structural refreshes) and on SSE full snapshots.

### What happens

1. `_topologyDomCache.clear()` is called (e.g., when `refreshTopologyStructure()` gets a new payload)
2. `renderTopologyStage()` runs
3. The "remove nodes not in visible set" loop iterates `_topologyDomCache` — but the cache is empty, so nothing is removed
4. The "update existing / create new" loop checks `_topologyDomCache.get(entity.id)` for each entity — all return `undefined` since the cache was just cleared
5. **All 24 entities take the "new node" branch** → `layer.appendChild(button)` creates 24 new buttons
6. **The 24 old buttons from the prior render are still children of `layer`** — they were never removed
7. Result: 48 DOM buttons, 24 cached, 24 stale (no event listeners, intercepting clicks)

### Impact

- **Double DOM nodes**: Every topology refresh doubles the button count (48 instead of 24)
- **Broken click targeting**: Stale nodes sit on top of cached nodes at the same positions, intercepting mouse clicks. The stale nodes have no event listeners, so clicks do nothing.
- **The duplication resets each cycle**: On the next `refreshTopologyStructure()` call, the cache clears again, old+new nodes persist, and 24 more are appended. However, since the stale nodes from 2 cycles ago are at the same positions as the ones from 1 cycle ago, it visually looks the same — but DOM count keeps growing: 24 → 48 → 72 → 96... until the layer is eventually cleared by a page navigation.

### Reproduction

1. Load `/topology` — nodes render correctly (24 DOM, 24 cached)
2. Wait 60 seconds for the timer-based `refreshTopologyStructure()` to fire
3. Check: `document.querySelectorAll('[data-topology-id]').length` → **48** (should be 24)
4. Check: `_topologyDomCache.size` → **24**
5. Click any node — **nothing happens** (stale node intercepts the click)

## Root Cause

The differential update loop in `renderTopologyStage()` only removes cached nodes that are no longer in the visible set. When the cache is empty (after a clear), the removal loop has nothing to iterate, so existing DOM children survive.

## Fix

**One-line fix: when the cache is empty at the start of the differential update, wipe the layer's existing children first.**

In `renderTopologyStage()`, find the differential update section (the block that starts with building `visibleIds` and iterating `_topologyDomCache` to remove stale nodes). Add this guard BEFORE the removal loop:

```javascript
    // If the cache is empty (first render or after a structural clear),
    // remove all existing entity buttons from the layer to prevent duplication.
    if (_topologyDomCache.size === 0 && layer.children.length > 0) {
        // Remove only topology entity buttons, preserve any non-entity children
        layer.querySelectorAll("[data-topology-id]").forEach(function(el) { el.remove(); });
    }
```

Place this BEFORE the existing loop that iterates `_topologyDomCache` to remove nodes not in `visibleIds`.

### Apply the same fix for `_topologyLinkDomCache` in `drawTopologyLinks()`

If `drawTopologyLinks()` has a similar differential update pattern (check if it does), add the equivalent guard:

```javascript
    if (_topologyLinkDomCache.size === 0) {
        svg.innerHTML = "";
        handleLayer.innerHTML = "";
    }
```

This is safe because when the link cache is empty, there's nothing to preserve — all links need fresh creation with event listeners.

## Alternative Fix (More Robust)

Instead of checking cache size, add an `isConnected` guard in the differential update's "existing node" branch:

```javascript
    for (const entity of visibleEntities) {
        let button = _topologyDomCache.get(entity.id);
        if (button && button.isConnected) {
            // Existing node — patch in-place
            _patchTopologyEntityDOM(button, entity, ...);
        } else {
            // New node OR stale cached reference — create fresh
            if (button) _topologyDomCache.delete(entity.id); // Clean up stale ref
            const temp = document.createElement("div");
            temp.innerHTML = _buildEntityHTML(entity, ...);
            button = temp.firstElementChild;
            if (button) {
                layer.appendChild(button);
                _attachTopologyEntityListeners(button, entityMap);
                _topologyDomCache.set(entity.id, button);
            }
        }
    }
```

This catches both the cache-clear case AND any case where a cached DOM reference becomes detached for other reasons. However, it doesn't solve the orphaned DOM node problem — the old buttons would still be in the layer. So **use both fixes together**: the `layer.querySelectorAll` cleanup when cache is empty, AND the `isConnected` guard for robustness.

## Recommended Implementation

Apply BOTH fixes:

1. **Cache-empty cleanup** (at the top of the differential loop):
```javascript
if (_topologyDomCache.size === 0 && layer.children.length > 0) {
    layer.querySelectorAll("[data-topology-id]").forEach(function(el) { el.remove(); });
}
```

2. **isConnected guard** (in the per-entity loop):
```javascript
let button = _topologyDomCache.get(entity.id);
if (button && button.isConnected) {
    // patch
} else {
    // create new (clean up stale ref if needed)
}
```

## Validation

### Syntax check:
```bash
node -c static/js/app.js
```

### Functional test:
1. Load `/topology` — verify 24 DOM nodes, 24 cached: 
   ```javascript
   document.querySelectorAll('[data-topology-id]').length === _topologyDomCache.size
   ```
2. Wait 60+ seconds for `refreshTopologyStructure()` timer
3. Re-check: should STILL be 24 DOM nodes, 24 cached — **not 48**
4. Click any node — tooltip should appear (not intercepted by stale node)
5. Toggle edit mode on/off — all nodes should remain visible
6. Force a refresh: `refreshTopologyStructure()` in console, wait 2s, re-check counts

### Soak test:
1. Leave topology open for 10+ minutes
2. Periodically check `document.querySelectorAll('[data-topology-id]').length` — should always equal `_topologyDomCache.size` (24)
3. Memory should be flat (no growing DOM count)

## Files Modified

- `static/js/app.js` — `renderTopologyStage()` differential update section (and `drawTopologyLinks()` if it has the same pattern)

## Risk

Very low. The fix adds a guard that only fires when the cache is empty, which is exactly when the old behavior (innerHTML wipe) would have run anyway. The `isConnected` check is a standard DOM API with no side effects.

**Commit message:** `fix: clear stale DOM nodes when topology cache is empty before differential render`
