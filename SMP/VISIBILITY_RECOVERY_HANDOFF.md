# CC Handoff: Add Tab Visibility Recovery to app.js

## Problem

After deploying the nginx HTTP/2 reverse proxy, the HTTP/1.1 head-of-line blocking issue is resolved — initial page loads and active-tab performance are excellent (12ms TTFB, 404ms full load, all resources negotiating `h2`). However, users experience a **new class of sluggishness**: the SMP tab becomes unresponsive after sitting idle or after the user switches to another application/tab and comes back.

### Root Cause

Chrome aggressively throttles background tabs:
- `setInterval` timers are deferred to fire at most once per minute (or less)
- `setTimeout` callbacks are similarly delayed
- Network requests may be deprioritized or deferred
- The HTTP/2 connection itself may be torn down by nginx after idle timeout

When the user switches away from the SMP tab (e.g., to use another app, read email, interact with Cowork), Chrome marks the tab as `document.hidden = true` / `visibilityState = "hidden"`. At that point:

1. **The SSE `EventSource` to `/api/stream/events` goes idle.** If nginx or the upstream closes it (keepalive timeout, h2 GOAWAY), the `onerror` handler fires and schedules a reconnect via `setTimeout(..., 10000)` — but that 10-second timer is *also* throttled because the tab is hidden. The reconnect may not fire for minutes.

2. **The topology structure refresh `setInterval`** (line ~3480) stops firing reliably. The "Updated X ago" 1-second counter (line ~8087) also freezes.

3. **When the user returns**, nothing in the code detects that the tab is now visible. The SSE stream may be dead, data is stale, and the first interaction has to wait for timers to catch up and connections to re-establish.

### Evidence

Confirmed via browser automation testing on `https://localhost:8443`:
- After 223 seconds with the page open, **zero fetch activity** was recorded despite a 60-second refresh timer
- `document.hidden === true` and `visibilityState === "hidden"` while the tab was backgrounded
- The "Last Update" timestamp remained frozen at the initial load time (`7:49:52 AM`) for over 6 minutes
- Performance API showed all initial resources loaded via `h2` with connection reuse, so the issue is purely post-load lifecycle

## Scope

Add a `visibilitychange` event listener to `static/js/app.js` that detects when the tab becomes visible again and immediately recovers: reconnects SSE if dead, forces a fresh data fetch for the current page context, and resets the "last updated" display. Also make the SSE reconnect delay visibility-aware.

## Architecture

No new files. No backend changes. This is a contained change to `static/js/app.js` in two areas:

1. **New `visibilitychange` handler** — registered once in the `DOMContentLoaded` block
2. **Smarter SSE reconnect delay** — shorter when tab is visible, longer when hidden

## Implementation

### 1. Add visibility recovery handler

Insert this block near the SSE connection management section (after the `connectNodeStateStream` function, around line 3032):

```javascript
// --- Tab visibility recovery ---
// Chrome throttles background tabs aggressively: setInterval timers
// fire at most once/minute, setTimeout is delayed, and the h2
// connection or SSE stream may be torn down by nginx.  When the
// user returns we need to reconnect SSE and refresh stale data.

function handleVisibilityRecovery() {
    if (document.visibilityState !== "visible") return;

    // 1. Reconnect SSE if dead
    if (!nodeStateEventSource || nodeStateEventSource.readyState === EventSource.CLOSED) {
        console.log("[visibility] Tab visible — reconnecting SSE");
        connectNodeStateStream();
    }

    // 2. Reset the "updated ago" baseline so it doesn't show a stale jump
    markTopologyLastUpdated();

    // 3. Force a data refresh for the current page context
    //    These are the same functions called during initial page load.
    //    Each one is guarded by checking for its root DOM element,
    //    so only the active page actually fetches.
    safeStart(loadMainDashboard, "main-dashboard");
    safeStart(loadNodeDashboard, "node-dashboard");
    safeStart(loadServicesDashboard, "services-dashboard");
    safeStart(loadTopologyPage, "topology");
    safeStart(loadChartsPage, "charts");
    safeStart(loadHealthPage, "health");
    safeStart(loadNodeDetailPage, "node-detail");
}
```

### 2. Register the listener in DOMContentLoaded

In the `DOMContentLoaded` handler (line ~10932), add after the `connectNodeStateStream()` call:

```javascript
    // Connect SSE for real-time updates on all pages
    connectNodeStateStream();

    // Recover from Chrome background-tab throttling
    document.addEventListener("visibilitychange", handleVisibilityRecovery);
```

### 3. Make SSE reconnect delay visibility-aware

In the `es.onerror` handler inside `connectNodeStateStream()` (line ~3017), change the fixed 10-second delay to be shorter when the tab is visible:

**Current code (line 3017-3025):**
```javascript
    es.onerror = () => {
        const ageEl = document.querySelector(".topology-updated-ago");
        if (ageEl) ageEl.textContent = "reconnecting\u2026";
        // Close and reconnect manually with a delay to prevent rapid reconnect loops.
        // The default EventSource auto-reconnect can flood the connection pool.
        es.close();
        nodeStateEventSource = null;
        setTimeout(() => connectNodeStateStream(), 10000);
    };
```

**Replace with:**
```javascript
    es.onerror = () => {
        const ageEl = document.querySelector(".topology-updated-ago");
        if (ageEl) ageEl.textContent = "reconnecting\u2026";
        // Close and reconnect manually with a delay to prevent rapid reconnect loops.
        // Use a shorter delay when the tab is visible (user is waiting)
        // and a longer delay when hidden (Chrome will throttle anyway).
        es.close();
        nodeStateEventSource = null;
        const delay = document.visibilityState === "visible" ? 3000 : 30000;
        setTimeout(() => connectNodeStateStream(), delay);
    };
```

**Rationale:** When the user is actively looking at the page, 3 seconds is a reasonable reconnect delay (fast enough to feel responsive, slow enough to avoid flood loops). When the tab is hidden, there's no point reconnecting quickly — Chrome will throttle it anyway, so 30 seconds reduces unnecessary connection churn.

## Verification

1. **Build and start** the stack (or restart if already running).

2. **Basic visibility test:**
   - Open `https://localhost:8443` in Chrome
   - Note the "Last Update" time in the dashboard status bar
   - Switch to another application or tab for 2+ minutes
   - Switch back to the SMP tab
   - **Expected:** The "Last Update" time should refresh within 1-2 seconds of returning. The SSE stream should reconnect (check DevTools Network → EventStream). Data should be current.

3. **SSE reconnect test:**
   - Open DevTools → Network tab, filter by EventStream
   - Verify the SSE connection to `/api/stream/events` is active
   - Background the tab for 2+ minutes until the SSE connection dies
   - Return to the tab
   - **Expected:** A new EventSource connection appears within 3 seconds. The "reconnecting..." text (if briefly visible) clears once the connection opens.

4. **Console log test:**
   - Open DevTools → Console
   - Background the tab, wait, then return
   - **Expected:** You see `[visibility] Tab visible — reconnecting SSE` in the console when appropriate.

5. **No regressions:**
   - Navigate between all pages (Dashboard, Topology, Services, Discovery, Charts, Diag) — should load normally
   - SSE events should still update node states in real time when the tab is active
   - The "Updated X ago" counter should tick normally
   - Theme switching should still work
   ```bash
   python -m unittest discover -s tests
   python -m compileall -f app tests alembic
   ```

## Files to Modify

| File | Change |
|------|--------|
| `static/js/app.js` | Add `handleVisibilityRecovery()` function, register `visibilitychange` listener in `DOMContentLoaded`, update SSE `onerror` reconnect delay |
| `CHANGELOG.md` | Add entry for tab visibility recovery |
| `docs/AGENT_HANDOFF.md` | Add handoff entry |

## Files NOT to Change

- No backend Python changes
- No HTML template changes
- No CSS changes
- No nginx config changes
- No database or migration changes

## Nginx Tuning Recommendations (Optional, Separate PR)

While investigating this issue, the following nginx settings were noted as potentially contributing to idle-tab staleness. These are optional optimizations — the `visibilitychange` fix above is the primary solution.

1. **`keepalive_timeout`** — Not explicitly set in the current config (defaults to 75s). Consider setting to `120s` to reduce connection teardown during brief tab-away periods:
   ```nginx
   keepalive_timeout 120s;
   ```

2. **`http2_idle_timeout`** — Controls how long nginx keeps an idle HTTP/2 connection open. Default is 3 minutes. For a dashboard app where users step away frequently, increasing to 5 minutes could help:
   ```nginx
   http2_idle_timeout 300s;
   ```

3. **`proxy_buffering`** — Currently only disabled for SSE endpoints. The default (on) for API routes is fine and shouldn't be changed.

## Risk Assessment

- **Very low risk.** The change is additive — it adds a new event listener and a small function. No existing behavior is modified except the SSE reconnect delay (from a fixed 10s to a visibility-aware 3s/30s).
- **No performance impact.** The `visibilitychange` event fires at most once per tab show/hide cycle. The refresh calls are the same ones used during initial page load and are individually guarded by DOM element checks.
- **Fallback:** If anything goes wrong, removing the single `addEventListener("visibilitychange", ...)` line reverts to current behavior.

## Context

This is a companion fix to the nginx HTTP/2 reverse proxy work (see `NGINX_HTTP2_HANDOFF.md`). That fix solved the HTTP/1.1 head-of-line blocking that caused 30-50s page-load stalls. This fix addresses the remaining UX issue: Chrome background-tab throttling causing the tab to feel "stale" or "sluggish" when returning to it after a period of inactivity.
