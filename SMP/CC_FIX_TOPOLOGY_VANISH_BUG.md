# CC Handoff: Fix Topology Nodes Vanish After Refresh

## Bug Summary

Topology entity nodes disappear from the map and never recover. The stage shows an empty dark background with no entities. Reproduced on both Chrome and Edge. The page requires a full reload to recover.

**Observed state during bug:**
- DOM: 0 `[data-topology-id]` elements (all entity buttons gone)
- Cache: `_topologyDomCache` has 24 entries — all detached (`isConnected === false`)
- No subsequent `renderTopologyStage()` call fires to recover
- `drawTopologyLinks()` SVG is also empty (no links rendered)

## Root Cause

**Primary defect — `loadTopologyPage()` error handler (line 7549):**

```javascript
// static/js/app.js — loadTopologyPage(), line 7545
} catch (error) {
    const drawer = document.getElementById("topology-details-drawer");
    const layer = document.getElementById("topology-node-layer");
    if (layer) {
        layer.innerHTML = "";   // ← CLEARS ALL DOM CHILDREN
    }
    // ... drawer error message ...
}
```

`layer.innerHTML = ""` destroys all entity button DOM nodes, but **does not clear `_topologyDomCache` or `_topologyLinkDomCache`**. This leaves the cache holding 24 detached references that no longer point to live DOM elements. No `renderTopologyStage()` call follows, so the topology is stuck empty.

**Trigger path:** `handleVisibilityRecovery()` (line 3015) → `safeStart(loadTopologyPage, "topology")` (line 3033). When the browser tab is backgrounded and returns to focus, Chrome fires `visibilitychange`, which calls `loadTopologyPage()`. If any of its 5 concurrent API requests fail (network timeout, server busy after cold start, etc.), the catch block runs and wipes the stage.

**Secondary defect — `refreshTopologyData()` (line 6051):**

This function clears both caches but never calls `renderTopologyStage()`. Currently dead code (no call sites), but if it's ever wired up, it will cause the same symptom. Should be either fixed or deleted.

## Fix

### Part 1: Error handler cache clear (REQUIRED)

**File:** `static/js/app.js`  
**Function:** `loadTopologyPage()` — error handler around line 7545–7554

**Current code (lines 7545–7554):**
```javascript
    } catch (error) {
        const drawer = document.getElementById("topology-details-drawer");
        const layer = document.getElementById("topology-node-layer");
        if (layer) {
            layer.innerHTML = "";
        }
        if (drawer) {
            drawer.innerHTML = `<p class="status-error">${escapeHtml(error.message || "Unable to load topology")}</p>`;
        }
    }
```

**Replace with:**
```javascript
    } catch (error) {
        const drawer = document.getElementById("topology-details-drawer");
        const layer = document.getElementById("topology-node-layer");
        if (layer) {
            layer.innerHTML = "";
        }
        _topologyDomCache.clear();
        _topologyLinkDomCache.clear();
        if (drawer) {
            drawer.innerHTML = `<p class="status-error">${escapeHtml(error.message || "Unable to load topology")}</p>`;
        }
    }
```

**What this does:** When the error handler wipes the layer DOM, it now also clears the cache maps so they stay in sync. The next successful render (from SSE snapshot, timer refresh, or manual action) will see cache size === 0 and correctly rebuild from scratch.

### Part 2: Defensive isConnected sweep in renderTopologyStage (RECOMMENDED)

**File:** `static/js/app.js`  
**Function:** `renderTopologyStage()` — after the cache-empty guard (line 5378)

**After the existing cache-empty guard block (lines 5374–5378):**
```javascript
    // If the cache is empty (first render or after a structural clear),
    // remove all existing entity buttons from the layer to prevent duplication.
    if (_topologyDomCache.size === 0 && layer.children.length > 0) {
        layer.querySelectorAll("[data-topology-id]").forEach(function(el) { el.remove(); });
    }
```

**Insert this block immediately after (before line 5380 "Remove cached nodes no longer visible"):**
```javascript
    // Defensive: if all cached nodes are detached (e.g. layer was rebuilt
    // externally), treat as a cold start — clear the stale cache so the
    // create-new branch below rebuilds every entity.
    if (_topologyDomCache.size > 0) {
        let anyConnected = false;
        for (const [, btn] of _topologyDomCache) {
            if (btn.isConnected) { anyConnected = true; break; }
        }
        if (!anyConnected) {
            _topologyDomCache.clear();
            _topologyLinkDomCache.clear();
            layer.querySelectorAll("[data-topology-id]").forEach(function(el) { el.remove(); });
        }
    }
```

**What this does:** If the cache has entries but none of them are actually in the DOM (all detached), it resets to a clean state. This is a safety net that catches any code path — present or future — that destroys the layer's children without clearing the caches. The `for...of` with early break is O(1) in the common healthy case (first entry is connected → done).

### Part 3: Clean up dead code (OPTIONAL)

**File:** `static/js/app.js`  
**Function:** `refreshTopologyData()` — lines 6038–6057

This function clears both caches (line 6051–6052) but never calls `renderTopologyStage()`. It has zero call sites — it's dead code. Either:

**Option A — Delete it entirely** (preferred, reduces confusion):
Remove lines 6038–6057.

**Option B — Add the missing render call** (if you plan to use it later):
After line 6052, add:
```javascript
            renderTopologyStage();
```

## Validation Steps

1. **Fresh load test:**
   ```javascript
   // In browser console after page loads:
   document.querySelectorAll('[data-topology-id]').length  // → 24
   _topologyDomCache.size                                   // → 24
   ```

2. **Simulated error handler test:**
   ```javascript
   // Force the error path manually:
   const layer = document.getElementById("topology-node-layer");
   layer.innerHTML = "";
   _topologyDomCache.clear();
   _topologyLinkDomCache.clear();
   // Verify cache is clean:
   _topologyDomCache.size  // → 0
   // Now trigger recovery:
   renderTopologyStage();
   document.querySelectorAll('[data-topology-id]').length  // → 24
   _topologyDomCache.size                                   // → 24
   ```

3. **Detached-cache recovery test (Part 2):**
   ```javascript
   // Simulate detached state without clearing cache:
   document.getElementById("topology-node-layer").innerHTML = "";
   // Cache still has entries but all detached:
   _topologyDomCache.size  // → 24 (stale)
   // Now render — defensive sweep should recover:
   renderTopologyStage();
   document.querySelectorAll('[data-topology-id]').length  // → 24
   _topologyDomCache.size                                   // → 24
   ```

4. **Multi-refresh soak:**
   ```javascript
   // Run 5 rapid refreshTopologyStructure() calls:
   for (let i = 0; i < 5; i++) refreshTopologyStructure();
   // Wait 5 seconds, then check:
   setTimeout(() => {
       console.log('DOM:', document.querySelectorAll('[data-topology-id]').length);
       console.log('Cache:', _topologyDomCache.size);
   }, 5000);
   // Both should be 24
   ```

5. **Visibility recovery simulation:**
   ```javascript
   // Manually trigger the recovery path:
   handleVisibilityRecovery();
   // Wait for async completion, then check:
   setTimeout(() => {
       console.log('DOM:', document.querySelectorAll('[data-topology-id]').length);
       console.log('Cache:', _topologyDomCache.size);
   }, 3000);
   ```

## Risk Assessment

- **Part 1 (error handler):** Zero risk. Two `Map.clear()` calls in a catch block that already wipes the DOM. No behavior change on the success path.
- **Part 2 (defensive sweep):** Very low risk. Only fires when every single cached button is detached — a state that should never occur during normal operation. Early-break loop means zero overhead in the happy path.
- **Part 3 (dead code):** Zero risk if deleting. The function has no callers.

## Commit Message

```
fix: clear topology DOM cache in loadTopologyPage error handler

The error handler in loadTopologyPage() cleared layer.innerHTML but
left _topologyDomCache holding detached references, causing all
topology entities to vanish with no recovery path. Also adds a
defensive sweep in renderTopologyStage() that detects fully-detached
cache state and resets to clean.
```
