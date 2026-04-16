# CC Prompt: Fix SSE EventSource Listener Leak

## Problem

Pages become unresponsive after sitting idle for extended periods. Root cause is a **memory/listener leak** in the SSE reconnection logic in `static/js/app.js`. When the EventSource connection errors and reconnects, new event listeners are added to the new EventSource object without issue — but the reconnection pattern itself causes unbounded growth if the connection is unstable.

More critically, there is **no page visibility handling** — SSE connections stay open when the tab is backgrounded, and no keepalive/timeout mechanism exists on either end to detect stale connections.

## Scope

**Primary fix:** `static/js/app.js` — lines ~2931-3032 (SSE connection functions)  
**Secondary hardening:** `app/routes/stream.py`, `app/db.py`, `app/redis_client.py`

## What to Change

### 1. Frontend: Refactor `connectNodeStateStream()` / `disconnectNodeStateStream()` (CRITICAL)

**File:** `static/js/app.js`  
**Lines:** 2931-3032

**Current problem:** The `onerror` handler calls `es.close()` then `setTimeout(() => connectNodeStateStream(), 10000)`. Each reconnection creates a brand new EventSource with 9 fresh `addEventListener()` calls. While the old EventSource is closed (so its listeners won't fire), the pattern has no backoff ceiling and no visibility awareness — it will reconnect every 10 seconds forever even if the tab is in the background, burning server-side SSE connections.

**Required changes:**

a) **Extract listener functions to named references** so they can be cleanly managed:

```javascript
// Declare at module scope (near line 2931)
const sseHandlers = {
    snapshot: (e) => {
        try { applyFullSnapshot(JSON.parse(e.data)); } catch (err) { /* non-fatal */ }
    },
    node_update: (e) => {
        try { const { id, state } = JSON.parse(e.data); applyNodeUpdate(id, state); } catch (err) { /* non-fatal */ }
    },
    dn_update: (e) => {
        try { const { id, state } = JSON.parse(e.data); applyDnUpdate(id, state); } catch (err) { /* non-fatal */ }
    },
    node_offline: (e) => {
        try { const { id } = JSON.parse(e.data); applyNodeOffline(id); } catch (err) { /* non-fatal */ }
    },
    service_snapshot: (e) => {
        try { applyServiceSnapshot(JSON.parse(e.data)); } catch (err) { /* non-fatal */ }
    },
    service_update: (e) => {
        try { const { id, state } = JSON.parse(e.data); applyServiceUpdate(id, state); } catch (err) { /* non-fatal */ }
    },
    dn_discovered: (e) => {
        try { applyDnDiscovered(JSON.parse(e.data)); } catch (err) { /* non-fatal */ }
    },
    dn_removed: (e) => {
        try { const { site_id } = JSON.parse(e.data); applyDnRemoved(site_id); } catch (err) { /* non-fatal */ }
    },
    structure_changed: (e) => {
        try { applyStructureChanged(JSON.parse(e.data)); } catch (err) { /* non-fatal */ }
    },
};
```

b) **Rewrite `disconnectNodeStateStream()` to remove listeners explicitly:**

```javascript
function disconnectNodeStateStream() {
    if (nodeStateEventSource) {
        for (const [event, handler] of Object.entries(sseHandlers)) {
            nodeStateEventSource.removeEventListener(event, handler);
        }
        nodeStateEventSource.close();
        nodeStateEventSource = null;
    }
}
```

c) **Rewrite `connectNodeStateStream()` to use the named handlers and add exponential backoff:**

```javascript
let sseReconnectDelay = 2000;          // start at 2s
const SSE_RECONNECT_MAX = 60000;       // cap at 60s

function connectNodeStateStream() {
    if (nodeStateEventSource && nodeStateEventSource.readyState !== EventSource.CLOSED) {
        return;
    }
    disconnectNodeStateStream();
    const es = new EventSource("/api/stream/events");

    for (const [event, handler] of Object.entries(sseHandlers)) {
        es.addEventListener(event, handler);
    }

    es.onerror = () => {
        const ageEl = document.querySelector(".topology-updated-ago");
        if (ageEl) ageEl.textContent = "reconnecting\u2026";
        es.close();
        nodeStateEventSource = null;
        setTimeout(() => connectNodeStateStream(), sseReconnectDelay);
        sseReconnectDelay = Math.min(sseReconnectDelay * 2, SSE_RECONNECT_MAX);
    };

    es.onopen = () => {
        sseReconnectDelay = 2000;  // reset on successful connect
        markTopologyLastUpdated();
    };

    nodeStateEventSource = es;
}
```

### 2. Frontend: Add Page Visibility Handling (HIGH)

**File:** `static/js/app.js`  
**Location:** Near the `beforeunload` handler at line ~11190

**Add a `visibilitychange` listener** that disconnects SSE when the tab is hidden and reconnects when visible again. This prevents phantom server-side connections from backgrounded tabs:

```javascript
document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
        disconnectNodeStateStream();
        disconnectNodeDashboardStream();
    } else {
        // Small delay to avoid reconnect thrash on rapid tab switching
        setTimeout(() => {
            connectNodeStateStream();
            // Re-trigger dashboard polling if on a dashboard page
            if (document.getElementById("nodeGrid") || document.getElementById("mainNodeGrid")) {
                applyDashboardRefreshInterval();
            }
        }, 500);
    }
});
```

### 3. Backend: Add SSE Keepalive and Max Lifetime (MEDIUM)

**File:** `app/routes/stream.py`  
**Functions:** `redis_event_generator()` (line 49) and `fallback_event_generator()` (line 72)

The Redis event generator has no keepalive — if no events are published for a while, nginx or the browser may silently drop the connection. Add a periodic keepalive comment and an optional max connection lifetime:

In `redis_event_generator()`, wrap the `async for` loop with a timeout so a keepalive comment is sent every 30 seconds of silence:

```python
async def redis_event_generator():
    try:
        # ... snapshot phase unchanged ...

        # Live phase with keepalive
        async for event in state_manager.subscribe_channels(redis_channels):
            event_type = event.get("type", "node_update")
            yield f"event: {event_type}\ndata: {json.dumps(event, default=str)}\n\n"
    except asyncio.CancelledError:
        return
    except Exception:
        logger.debug("Redis SSE subscription ended, falling back to polling", exc_info=True)
        async for chunk in fallback_event_generator():
            yield chunk
```

**Note:** The `subscribe_channels` generator in `state_manager.py` (line 336) is a blocking `async for` on `pubsub.listen()` — it has no timeout. To add keepalive, you'll need to wrap it with `asyncio.wait_for` or switch to a pattern that yields keepalive comments every ~30s of inactivity. A simple approach:

In `state_manager.py`, modify `subscribe_channels` to yield a sentinel on timeout:

```python
async def subscribe_channels(
    channels: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    r = await get_redis()
    if r is None:
        raise RuntimeError("Redis unavailable for pub/sub subscription")
    target_channels = channels or ALL_CHANNELS
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(*target_channels)
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=30.0
            )
            if message is None:
                # No message in 30s — yield keepalive sentinel
                yield {"type": "__keepalive__"}
                continue
            if message["type"] != "message":
                continue
            try:
                yield json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
    finally:
        await pubsub.unsubscribe(*target_channels)
        await pubsub.aclose()
```

Then in `stream.py`, handle the sentinel:

```python
async for event in state_manager.subscribe_channels(redis_channels):
    if event.get("type") == "__keepalive__":
        yield ": keep-alive\n\n"
        continue
    event_type = event.get("type", "node_update")
    yield f"event: {event_type}\ndata: {json.dumps(event, default=str)}\n\n"
```

### 4. Backend: Harden Connection Pool Defaults (LOW)

**File:** `app/db.py` (line 16)

Add explicit pool sizing:

```python
async_engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=5,
    pool_timeout=30,
)
```

**File:** `app/redis_client.py` (line 29)

Add connection limits:

```python
_pool = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    max_connections=20,
    socket_timeout=10,
    socket_connect_timeout=5,
)
```

## Execution Order

1. **Do item 1 first** (named handlers + cleanup) — this is the critical fix
2. **Do item 2** (visibility handling) — immediate UX improvement
3. **Do item 3** (keepalive) — prevents silent connection death
4. **Do item 4** (pool hardening) — defense in depth

## Validation

After applying changes:
1. Open the topology page, verify SSE connects (check "Updated Xs ago" advances)
2. Switch to another tab for 30+ seconds, switch back — page should reconnect within 2 seconds and resume updating
3. Open browser DevTools → Network → filter `EventSource` — verify only ONE active SSE connection exists at any time
4. Open DevTools → Console — trigger a reconnect by stopping/starting the SMP container — verify no duplicate event processing in console logs
5. Leave the page open for 5+ minutes on topology — verify no memory growth in DevTools → Memory tab

## Files Modified

- `static/js/app.js` — SSE handler refactor + visibility listener
- `app/routes/stream.py` — keepalive handling
- `app/state_manager.py` — pub/sub timeout + keepalive sentinel
- `app/db.py` — pool sizing
- `app/redis_client.py` — connection limits
