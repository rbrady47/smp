# SSE + Static Asset Fix Handoff — Claude Code Planning Session

## Context

After deploying the Tier 1/2 performance fixes (connection pool, poller staggering, circuit breaker, etc.), page-to-page navigation is generally fast. But a specific stall pattern remains:

1. **After sitting idle on a page** → next navigation stalls 10-20s
2. **After minimizing the browser** → returning to the app stalls
3. **Initial load after server restart** → slow (expected: cold caches)

**Root cause:** The SSE connection at `/api/stream/events` emits keep-alive frames every 1 second, even when the browser tab is backgrounded or throttled by Chrome. Keep-alive frames pile up in TCP buffers. When the user navigates to a new page, the browser must tear down the congested SSE socket and establish new connections for the HTML page + API fetch calls — all competing with the backed-up TCP state.

The 10-second hard-coded reconnect delay in `app.js` line 3024 compounds the problem — if the SSE connection errors during a background period, the user waits a full 10 seconds before real-time data resumes.

Additionally, `StaticFiles` serves CSS/JS with no cache headers, so every full-page navigation re-requests `style.css` and `app.js` from the server.

**Branch:** Continue on the current branch or create `fix/sse-idle-stall`.

---

## Fix 1: Add `visibilitychange` Listener to Pause/Resume SSE

**File:** `static/js/app.js`

**Problem:** There is no `visibilitychange` handler anywhere in the frontend (confirmed — no `beforeunload` handler either). When the tab goes to background, the SSE connection stays open. The server keeps emitting 1/sec keep-alive frames into a throttled client. TCP buffers fill up. When the user returns or navigates, the congested connection causes stalls.

**What to add:** Insert this block near the bottom of the `DOMContentLoaded` handler (around line 10962, right after `connectNodeStateStream()`):

```javascript
// Pause SSE when tab is hidden to prevent TCP buffer congestion.
// Chrome throttles background tabs — the server-side generator keeps
// emitting keep-alive frames the client can't consume, filling TCP
// buffers and stalling the next navigation.
document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
        disconnectNodeStateStream();
        disconnectNodeDashboardStream();
    } else {
        // Reconnect immediately when tab becomes visible — no delay.
        connectNodeStateStream();
    }
});
```

**Also add a `beforeunload` handler** to ensure clean SSE teardown on page navigation (full page loads in a multi-page app don't trigger `visibilitychange`):

```javascript
window.addEventListener("beforeunload", () => {
    disconnectNodeStateStream();
    disconnectNodeDashboardStream();
});
```

These use the existing `disconnectNodeStateStream()` (line 2933) and `disconnectNodeDashboardStream()` (line 2920) functions — no new teardown logic needed.

**Validation:** 
- Open browser devtools → Network tab → filter by "stream"
- Navigate to any SMP page — you should see one SSE connection
- Minimize the browser or switch to another tab — the SSE connection should close
- Return to the SMP tab — a new SSE connection should open immediately
- Navigate between pages — no stall

---

## Fix 2: Reduce SSE Reconnect Delay from 10s to Exponential Backoff Starting at 2s

**File:** `static/js/app.js` (lines 3017-3025)

**Current code:**
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

**Problem:** Hard-coded 10-second delay on every error. When the SSE drops due to a transient issue (tab background, network blip), the user waits 10 seconds for real-time data to resume.

**Replace with exponential backoff:**

First, add a reconnect state variable near the top of the file (near line 44-45 where the EventSource variables are declared):

```javascript
let _sseReconnectAttempts = 0;
```

Then replace the `es.onerror` handler:

```javascript
es.onerror = () => {
    const ageEl = document.querySelector(".topology-updated-ago");
    if (ageEl) ageEl.textContent = "reconnecting\u2026";
    es.close();
    nodeStateEventSource = null;

    // Exponential backoff: 2s, 4s, 8s, cap at 30s
    const delay = Math.min(2000 * Math.pow(2, _sseReconnectAttempts), 30000);
    _sseReconnectAttempts++;
    setTimeout(() => connectNodeStateStream(), delay);
};
```

And in the `es.onopen` handler (line 3027-3029), reset the counter on successful connection:

```javascript
es.onopen = () => {
    _sseReconnectAttempts = 0;  // Reset backoff on successful connect
    markTopologyLastUpdated();
};
```

**Validation:** First reconnect after an error should happen in ~2s, not 10s. Rapid reconnect floods are prevented by backoff to 30s max.

---

## Fix 3: Add Cache Headers to Static File Serving

**File:** `app/main.py` (line 271)

**Current code:**
```python
app.mount("/static", StaticFiles(directory="static"), name="static")
```

**Problem:** FastAPI's `StaticFiles` serves files with no `Cache-Control` header. Every full-page navigation (this is a multi-page app — every click is a new page load) re-requests `app.js` (~11000 lines) and `style.css` from the server. The templates already use cache-busting query strings (`?v=20260330-rttsync1`), so caching is safe.

**Replace with a custom middleware that adds cache headers.** FastAPI's `StaticFiles` doesn't support cache header configuration natively, so use a simple middleware approach:

```python
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send

class StaticCacheMiddleware:
    """Add Cache-Control headers to /static/ responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/static/"):
            async def send_with_cache(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"cache-control", b"public, max-age=86400"))
                    message["headers"] = headers
                await send(message)
            await self.app(scope, receive, send_with_cache)
        else:
            await self.app(scope, receive, send)
```

Add this class in `app/main.py` (above the `app = FastAPI(...)` line), then register it:

```python
app = FastAPI(title="Seeker Management Platform", version="0.1.0", lifespan=lifespan)
app.add_middleware(StaticCacheMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")
```

This sets `Cache-Control: public, max-age=86400` (24 hours) on all `/static/` responses. The cache-busting `?v=` query strings in templates ensure fresh assets are fetched after deploys.

**Validation:** In browser devtools → Network tab, static assets should show `(disk cache)` or `304 Not Modified` on subsequent page navigations instead of `200` with full transfer.

---

## Fix 4: Add Server-Side SSE Keep-Alive Interval Increase (Optional but Recommended)

**File:** `app/routes/stream.py`

**Current behavior:** Both `fallback_event_generator()` (line 88) and `node_dashboard_stream()` (line 110) emit keep-alive every 1 second:
```python
await asyncio.sleep(1.0)
```

**Problem:** 1-second keep-alive is unnecessarily aggressive. SSE connections typically survive with 15-30 second keep-alive intervals. At 1Hz, each idle SSE client generates 60 TCP writes per minute of zero-value data.

**Change:** Increase the sleep to 15 seconds in the keep-alive path only (the data-changed path should still emit immediately):

In `fallback_event_generator()` (lines 76-88):
```python
while True:
    cache = node_dashboard_backend.node_dashboard_cache
    payload = {
        "anchors": {str(a["id"]): a for a in (cache.get("anchors") or []) if isinstance(a, dict) and a.get("id")},
        "discovered": {str(d["site_id"]): d for d in (cache.get("discovered") or []) if isinstance(d, dict) and d.get("site_id")},
    }
    serialized = json.dumps(payload, default=str)
    if serialized != last_sent:
        yield f"event: snapshot\ndata: {serialized}\n\n"
        last_sent = serialized
        await asyncio.sleep(1.0)   # Check for changes every 1s
    else:
        yield ": keep-alive\n\n"
        await asyncio.sleep(15.0)  # No changes — slow keep-alive
```

Apply the same pattern to the `node_dashboard_stream()` generator (lines 102-110) and the `fallback_event_generator()` in `stream_node_states()` (lines 137-153).

**Rationale:** When data is changing, the generator still checks every 1 second. When idle, it slows to 15-second keep-alive — reducing TCP writes by 15x for idle connections and dramatically reducing buffer congestion when tabs are backgrounded.

**Validation:** In devtools → Network → select the SSE connection → EventStream tab. During idle periods, keep-alive comments should appear every ~15s instead of every 1s.

---

## Documentation Updates

| File | What to update |
|------|---------------|
| `CHANGELOG.md` | `fix: pause SSE on background tab, add static cache headers, reduce reconnect delay` |
| `docs/AGENT_HANDOFF.md` | New entry with branch, files touched, what changed |
| `docs/CODE_DOCUMENTATION.md` | Update SSE section with visibility handling and keep-alive behavior |

---

## Commit Strategy

Single commit is fine for this scope:
```
fix: pause SSE on background tab, add static cache headers, reduce keep-alive frequency
```

Run `python -m unittest discover -s tests` and `python -m compileall app tests alembic` before committing.

---

## Testing Checklist

- [ ] `python -m compileall app tests alembic` — no syntax errors
- [ ] `python -m unittest discover -s tests` — all 45 tests pass
- [ ] Navigate between pages — no stall
- [ ] Minimize browser for 30s, return — SSE reconnects within 2s, page is responsive
- [ ] Switch to another tab for 60s, return — same behavior
- [ ] Check devtools Network tab: static assets cached on subsequent page loads
- [ ] Check devtools Network tab: SSE connection closes on tab background, reopens on focus
- [ ] Check devtools EventStream: keep-alive interval is ~15s during idle, data events still immediate
