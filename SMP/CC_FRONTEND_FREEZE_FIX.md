# CC Prompt: Fix Intermittent Page Freezing

## Problem

Pages become unresponsive after sitting idle for extended periods. The prior SSE listener leak fix (named `sseHandlers`, exponential backoff, `removeEventListener` cleanup) partially addressed this but three root causes remain.

## Root Causes

### 1. `apiRequest()` has no timeout (CRITICAL)
**File:** `static/js/app.js` — `async function apiRequest()` (~line 7731)

Every API call in the entire frontend goes through `apiRequest()`, which calls `fetch()` with no `AbortController` or timeout. If the backend is slow or a connection hangs, the promise blocks forever. Since multiple `setInterval` timers fire periodically (topology refresh, discovery auto-refresh), pending requests stack up until the browser's 6-connections-per-host limit fills. At that point the tab freezes — no more network calls can proceed, and the main thread blocks waiting for sockets.

### 2. Discovery RAF loop never stops (CRITICAL)
**File:** `static/js/app.js` — `function discoveryTick()` (~line 9019)

`discoveryTick()` calls `requestAnimationFrame(discoveryTick)` unconditionally at the end (line ~9148). There's a guard `if (!discoveryState.running) return;` at the top (line ~9020), but `discoveryState.running` is **never set to false** — not on page navigation, not on tab hide, never. `cancelAnimationFrame()` is never called anywhere.

Once a user visits the Discovery page and loads root nodes, the O(n²) physics simulation runs at ~60fps forever, even after navigating to Topology or Dashboard. This burns CPU continuously and can make the tab sluggish.

### 3. No `visibilitychange` listener (HIGH)
**File:** `static/js/app.js` — bottom of DOMContentLoaded handler (~line 11190)

There's a `beforeunload` handler that disconnects SSE streams, but no `visibilitychange` handler. When the user switches tabs or minimizes the browser, all timers (topology refresh interval, discovery RAF, discovery auto-refresh interval, SSE connection) keep running. Chrome deprioritizes backgrounded tabs, so pending work stacks up and can cause a burst of processing when the tab regains focus.

## What to Change

### Fix 1: Add AbortController timeout to `apiRequest()` (CRITICAL)

**File:** `static/js/app.js`
**Function:** `async function apiRequest()` (~line 7731)

Add a default 15-second timeout using AbortController. Allow callers to override if needed.

**Replace the current function with:**

```javascript
async function apiRequest(url, options = {}) {
    const timeoutMs = options.timeout ?? 15000;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
        const response = await fetch(url, {
            headers: {
                "Content-Type": "application/json",
                ...(options.headers ?? {}),
            },
            ...options,
            signal: options.signal ?? controller.signal,
        });

        if (!response.ok) {
            let detail = "Request failed";
            try {
                const errorData = await response.json();
                detail = errorData.detail ?? detail;
            } catch (error) {
                // Ignore JSON parsing errors and keep the fallback message.
            }
            throw new Error(detail);
        }

        if (response.status === 204) {
            return null;
        }

        return response.json();
    } finally {
        clearTimeout(timeoutId);
    }
}
```

**Key points:**
- Default 15s timeout — long enough for normal ops, short enough to prevent indefinite hangs
- Callers can pass `{ timeout: 30000 }` for longer operations if needed
- Callers can pass their own `signal` to override (e.g., for manual abort)
- The `finally` block always clears the timeout to prevent leaks
- Aborted requests throw `AbortError`, which existing `try/catch` blocks will handle

### Fix 2: Stop Discovery RAF loop on page exit (CRITICAL)

**File:** `static/js/app.js`

#### 2a. Create a cleanup function for discovery state

Add this function near the other discovery functions (after `discoveryFetchAndInit`, ~line 9567):

```javascript
function discoveryCleanup() {
    discoveryState.running = false;
    if (discoveryState.animFrameId) {
        cancelAnimationFrame(discoveryState.animFrameId);
        discoveryState.animFrameId = null;
    }
    if (discoveryState.refreshTimer) {
        clearInterval(discoveryState.refreshTimer);
        discoveryState.refreshTimer = null;
    }
}
```

#### 2b. Call cleanup from `beforeunload`

Update the `beforeunload` handler (~line 11190) to also clean up discovery:

```javascript
window.addEventListener("beforeunload", () => {
    disconnectNodeDashboardStream();
    disconnectNodeStateStream();
    discoveryCleanup();
});
```

#### 2c. Guard the RAF loop continuation

In `discoveryTick()`, add a DOM presence check before scheduling the next frame. Currently line ~9148 reads:

```javascript
discoveryState.animFrameId = requestAnimationFrame(discoveryTick);
```

**Replace with:**

```javascript
// Only continue the animation loop if still on the discovery page
if (discoveryState.running && document.getElementById("discovery-stage")) {
    discoveryState.animFrameId = requestAnimationFrame(discoveryTick);
} else {
    discoveryState.running = false;
    discoveryState.animFrameId = null;
}
```

This ensures the loop self-terminates if the user navigates away from Discovery (the `#discovery-stage` element won't exist on other pages). The existing check at line ~9024 handles the `!stage` case by continuing the RAF loop — change that too:

Currently lines ~9023-9026:
```javascript
if (!stage || !nodes.length) {
    discoveryState.animFrameId = requestAnimationFrame(discoveryTick);
    return;
}
```

**Replace with:**
```javascript
if (!stage || !nodes.length) {
    if (stage) {
        // Stage exists but no nodes — keep polling for nodes to appear
        discoveryState.animFrameId = requestAnimationFrame(discoveryTick);
    } else {
        // Stage gone — user navigated away, stop the loop
        discoveryState.running = false;
        discoveryState.animFrameId = null;
    }
    return;
}
```

### Fix 3: Add `visibilitychange` listener (HIGH)

**File:** `static/js/app.js`
**Location:** Just before the `beforeunload` handler (~line 11190)

Add a visibility change handler that pauses all recurring work when the tab is hidden and resumes on return:

```javascript
document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
        // Pause all recurring work when tab is backgrounded
        disconnectNodeStateStream();
        disconnectNodeDashboardStream();
        // Pause discovery RAF if running
        if (discoveryState.running) {
            discoveryState.running = false;
            if (discoveryState.animFrameId) {
                cancelAnimationFrame(discoveryState.animFrameId);
                discoveryState.animFrameId = null;
            }
        }
        // Pause topology refresh timer
        if (typeof dashboardRefreshTimer !== "undefined" && dashboardRefreshTimer) {
            clearInterval(dashboardRefreshTimer);
            dashboardRefreshTimer = null;
        }
    } else {
        // Tab returned — reconnect with a small debounce to avoid thrash
        setTimeout(() => {
            connectNodeStateStream();
            // Restart topology timers if on topology page
            if (document.getElementById("topology-root")) {
                startTopologyTimers();
            }
            // Restart discovery if on discovery page with active roots
            if (document.getElementById("discovery-stage") && discoveryState.rootNodeIds.length) {
                if (!discoveryState.running) {
                    discoveryState.running = true;
                    discoveryState.animFrameId = requestAnimationFrame(discoveryTick);
                }
            }
            // Re-trigger dashboard refresh if on a dashboard page
            if (document.getElementById("nodeGrid") || document.getElementById("mainNodeGrid")) {
                applyDashboardRefreshInterval();
            }
        }, 500);
    }
});
```

### Fix 4: Verify prior SSE fixes are present (CHECK FIRST)

Before making changes, verify that the prior commit's SSE fixes are actually in the working tree. Check for:

1. **`sseHandlers` object** — a module-level const with 9 named handler functions (snapshot, node_update, dn_update, node_offline, service_snapshot, service_update, dn_discovered, dn_removed, structure_changed)
2. **`sseReconnectDelay` variable** — exponential backoff starting at 2000ms, doubling to max 60000ms
3. **`disconnectNodeStateStream()` calling `removeEventListener`** for each handler in `sseHandlers`

**If any of these are missing**, apply them per the instructions in `CC_SSE_LISTENER_LEAK_FIX.md` (already in the SMP workspace folder) before proceeding with the fixes above. The SSE handler refactor is a prerequisite — without named handler references, the `visibilitychange` disconnect/reconnect cycle would re-add anonymous listeners on every tab return.

## Execution Order

1. **Check** if prior SSE fixes (sseHandlers, backoff, removeEventListener) are present. If not, apply them first from `CC_SSE_LISTENER_LEAK_FIX.md`.
2. **Fix 1** — `apiRequest()` timeout (prevents connection pool exhaustion)
3. **Fix 2** — Discovery RAF cleanup (prevents CPU burn after leaving discovery)
4. **Fix 3** — `visibilitychange` listener (prevents background tab resource waste)

## Validation

After applying all changes:

1. **Timeout test:** Open DevTools Network tab, throttle to "Slow 3G", navigate to topology — verify API calls abort after 15s instead of hanging forever. Check console for AbortError messages (these are expected, not bugs).

2. **Discovery RAF test:** Navigate to Discovery → add a root node so physics starts → navigate to Topology. Open DevTools Console and run:
   ```javascript
   console.log("running:", discoveryState.running, "animFrameId:", discoveryState.animFrameId);
   ```
   Both should be `false`/`null`. The RAF loop should have self-terminated when Discovery's DOM was removed.

3. **Visibility test:** On the topology page, switch to another browser tab for 10+ seconds, switch back. Verify:
   - "Updated Xs ago" counter resets and resumes advancing
   - Only ONE SSE connection in Network tab (no duplicates)
   - No burst of stacked API calls in Network tab

4. **Soak test:** Leave the topology page open for 10+ minutes. Verify "Updated Xs ago" stays advancing and the page remains responsive. Check DevTools → Memory tab for steady memory (no continuous growth).

5. **Unit tests:** Run `pytest tests/` — all should pass (these changes are frontend-only).

6. **JS syntax:** Run `python -m py_compile` won't help here. Instead verify:
   ```bash
   node -c static/js/app.js
   ```
   Should exit cleanly with no syntax errors.

## Files Modified

- `static/js/app.js` — all four fixes (apiRequest timeout, discovery cleanup, visibilitychange listener, SSE fix verification)

## Risk Assessment

- **Low risk:** `apiRequest()` timeout — all existing callers have `try/catch`, AbortError will be caught like any other error
- **Low risk:** Discovery RAF cleanup — only adds termination conditions to an existing loop
- **Low risk:** Visibility handler — new additive code, doesn't modify existing timer logic
- **No backend changes** — this is entirely frontend
