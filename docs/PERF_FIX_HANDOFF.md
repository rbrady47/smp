# Performance Fix Handoff — Claude Code Planning Session

## Context

SMP exhibits intermittent sluggishness: page loads are sometimes instant, other times 20-30 seconds. A full-stack performance audit identified the root cause as **resource contention between 7 background pollers and web request handlers**, compounded by an undersized connection pool, thread pool saturation from synchronous subprocess/socket calls, and frontend fetch patterns that amplify backend delays.

This prompt covers **Tier 1 (eliminate 20-30s hangs)** and **Tier 2 (improve general responsiveness)** fixes. Work through them in order. Each fix is scoped to specific files and lines.

**Branch:** Create `refactor/performance-fixes` off the current working branch.

---

## Tier 1 — Eliminate the 20-30 Second Hangs

### Fix 1: Size the Database Connection Pool

**File:** `app/db.py` (line 16)

**Current code:**
```python
async_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
```

**Problem:** Defaults to `pool_size=5, max_overflow=10` (15 total). With 7 pollers holding sessions during long API calls + concurrent web requests, connections exhaust and requests queue for 20-30s.

**Change to:**
```python
async_engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=30,
    max_overflow=20,
    pool_timeout=10,
    pool_recycle=3600,
)
```

**Rationale:** 30 base + 20 overflow = 50 max connections. Pollers use ~7-10 at peak, leaving 40+ for web requests. The 10s pool_timeout means a request fails fast rather than hanging 30s. pool_recycle prevents stale PG connections.

**Validation:** After applying, check `db:pool` via the diag console on `/health` to confirm pool stats under load.

---

### Fix 2: Pollers — Release DB Sessions Before External API Calls

**Files:** `app/pollers/seeker.py`, `app/pollers/charts.py`, `app/pollers/dn_seeker.py`

**Problem:** Pollers open a DB session, fetch the node list, then **hold the session open** for the entire 10-30 second polling cycle while making HTTP calls to Seeker nodes. This ties up pool connections for non-DB work.

**Pattern to apply in each poller:**

In `seeker.py` — the main seeker polling loop (around line 226-242):
```python
# BEFORE (holds session during all API calls):
async with AsyncSessionLocal() as db:
    nodes = (await db.scalars(select(Node).order_by(Node.id))).all()
    # ... 10-30s of Seeker API calls happen here with session open ...

# AFTER (release session immediately after query):
async with AsyncSessionLocal() as db:
    nodes = (await db.scalars(select(Node).order_by(Node.id))).all()
# Session closed here — API calls happen outside the session block
# ... Seeker API calls ...
```

Apply the same pattern in:
- `charts.py` — charts polling loop (fetches nodes, then holds session during chart API calls)
- `dn_seeker.py` — DN seeker polling loop

When a poller needs to write back to DB after API calls (e.g., charts ingestion), open a **new short-lived session** just for the write:
```python
async with AsyncSessionLocal() as db:
    db.add(chart_sample)
    await db.commit()
```

**Validation:** Under load, the `db:pool` diag command should show fewer checked-out connections during polling cycles.

---

### Fix 3: Stagger Poller Start Times

**File:** `app/main.py` (lines 225-231)

**Current code:**
```python
_ps.ping_monitor_task = asyncio.create_task(ping_monitor_loop(_ps))
_ps.seeker_poll_task = asyncio.create_task(seeker_polling_loop(_ps))
_ps.site_name_resolution_task = asyncio.create_task(site_name_resolution_loop(_ps))
_ps.dn_seeker_poll_task = asyncio.create_task(dn_seeker_polling_loop(_ps))
_ps.service_poll_task = asyncio.create_task(service_polling_loop(_ps))
_ps.node_dashboard_poll_task = asyncio.create_task(node_dashboard_polling_loop(_ps))
_ps.charts_poll_task = asyncio.create_task(charts_polling_loop(_ps))
```

**Problem:** All 7 pollers start at the same instant, creating a thundering herd every time their intervals align (every ~60s all converge).

**Change to:** Add staggered startup delays. Wrap each poller start in a helper:
```python
async def _start_after(delay_s: float, coro):
    """Start a polling coroutine after an initial delay to stagger load."""
    await asyncio.sleep(delay_s)
    await coro

# Stagger starts to avoid thundering herd
_ps.ping_monitor_task = asyncio.create_task(ping_monitor_loop(_ps))           # immediate — lightweight
_ps.node_dashboard_poll_task = asyncio.create_task(
    _start_after(0.5, node_dashboard_polling_loop(_ps)))
_ps.seeker_poll_task = asyncio.create_task(
    _start_after(1.0, seeker_polling_loop(_ps)))
_ps.dn_seeker_poll_task = asyncio.create_task(
    _start_after(2.0, dn_seeker_polling_loop(_ps)))
_ps.site_name_resolution_task = asyncio.create_task(
    _start_after(3.0, site_name_resolution_loop(_ps)))
_ps.service_poll_task = asyncio.create_task(
    _start_after(4.0, service_polling_loop(_ps)))
_ps.charts_poll_task = asyncio.create_task(
    _start_after(5.0, charts_polling_loop(_ps)))
```

**Additionally:** In each poller's sleep, add jitter to prevent re-synchronization:
```python
import random
await asyncio.sleep(INTERVAL + random.uniform(0, 1.0))
```

Apply jitter to the sleep in: `seeker.py`, `dn_seeker.py`, `charts.py`, `services.py`, `dashboard.py`. Do NOT add jitter to `ping.py` — it has its own per-node scheduling.

**Validation:** Watch the application logs at startup — pollers should start 0.5-5s apart instead of all at once.

---

### Fix 4: Reduce Seeker API Timeout + Add Circuit Breaker

**File:** `app/pollers/seeker.py` (around line 235) and `app/seeker_api.py` (line 17)

**Problem:** Each node poll has a 30-second `asyncio.wait_for()` timeout. Unreachable nodes hold semaphore slots for 30s, starving other nodes. Inside, `seeker_api.py` has a 10s per-request timeout, but 3-4 sequential requests per node means 40s worst case.

**Changes:**

1. In `seeker.py`, reduce the per-node timeout from 30s to 15s:
```python
return await asyncio.wait_for(
    refresh_seeker_detail_for_node(ps, node), timeout=15.0,
)
```

2. Add a simple circuit breaker to `PollerState` (`app/poller_state.py`):
```python
# Add to PollerState dataclass:
node_failure_counts: dict[int, int] = field(default_factory=dict)
node_backoff_until: dict[int, float] = field(default_factory=dict)
```

3. In the seeker polling loop, skip nodes that are in backoff:
```python
import time

now = time.monotonic()
if ps.node_backoff_until.get(node.id, 0) > now:
    continue  # Skip — node is in backoff

# ... attempt poll ...
# On timeout/failure:
failures = ps.node_failure_counts.get(node.id, 0) + 1
ps.node_failure_counts[node.id] = failures
backoff_seconds = min(30 * (2 ** (failures - 1)), 300)  # 30s, 60s, 120s, 300s max
ps.node_backoff_until[node.id] = now + backoff_seconds
logger.warning("Node %s failed %d times, backing off %ds", node.name, failures, backoff_seconds)

# On success:
ps.node_failure_counts.pop(node.id, None)
ps.node_backoff_until.pop(node.id, None)
```

**Validation:** With a known-unreachable node in the system, confirm it gets skipped after first failure and the poller cycle completes in seconds instead of 30s.

---

### Fix 5: Increase Thread Pool for Subprocess/Socket Operations

**File:** `app/main.py` (add near top, before lifespan)

**Problem:** `ping_host()` and `check_tcp_port()` use `asyncio.to_thread()` which shares the default ThreadPoolExecutor (typically 5 threads). With 20+ concurrent pings (2s timeout each) + TCP port checks, threads exhaust and web requests block.

**Add:**
```python
import concurrent.futures

# Dedicated thread pool for blocking I/O (ping, TCP checks, nslookup)
_blocking_io_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=40, thread_name_prefix="smp-blocking-io"
)
```

Then expose a helper or set it as the default loop executor in lifespan:
```python
# Inside lifespan, before starting pollers:
loop = asyncio.get_running_loop()
loop.set_default_executor(_blocking_io_pool)
```

**Validation:** Under load with multiple unreachable nodes, web page loads should no longer stall waiting for thread availability.

---

## Tier 2 — Improve General Responsiveness

### Fix 6: Remove `await db.commit()` From Read-Only Routes

**Files:** `app/routes/dashboard.py`, `app/routes/topology.py`, `app/routes/discovery.py`, `app/routes/nodes.py`

**Problem:** 18+ GET route handlers call `await db.commit()` at the end despite making no modifications. Each is a wasted DB round-trip.

**Action:** Search all route files for `await db.commit()` in GET handlers. Remove every instance where the handler only reads data (no `db.add()`, no `db.delete()`, no `db.execute(update/insert/delete)`).

Known instances:
- `dashboard.py` — lines near `dashboard_nodes()`, `dashboard_watchlist()`, `node_dashboard()`
- `topology.py` — line near end of `topology_payload()`
- `discovery.py` — multiple GET handlers

**Validation:** Run the test suite (`python -m unittest discover -s tests`). All tests should pass unchanged — removing no-op commits has no behavioral effect.

---

### Fix 7: Add Composite Index on chart_samples

**Action:** Create an Alembic migration:
```bash
alembic revision -m "add composite index on chart_samples for query performance"
```

**Migration content:**
```python
def upgrade():
    op.create_index(
        "ix_chart_samples_node_ts_type",
        "chart_samples",
        ["node_id", "timestamp", "sample_type"],
    )

def downgrade():
    op.drop_index("ix_chart_samples_node_ts_type", table_name="chart_samples")
```

**Rationale:** The charts endpoint filters on all three columns. Without a composite index, it's a full table scan that gets worse as samples accumulate.

**Validation:** `alembic upgrade head` succeeds. Query plans for chart queries should show index scan instead of seq scan.

---

### Fix 8: Batch Discovery Cache Lookups

**File:** `app/routes/discovery.py`

**Problem:** `get_submap_discovery()` and related functions iterate over all inventory nodes and make individual `seeker_detail_cache.get()` calls per node, often for the same node multiple times across loops. With 300+ nodes this is O(n²).

**Pattern to apply:** At the top of `get_submap_discovery()`, pre-build lookup dicts:
```python
# Pre-load all caches into local dicts for O(1) lookups
all_seeker_details = {
    node_id: ps.seeker_detail_cache.get(node_id)
    for node_id in ps.seeker_detail_cache
}
all_dn_cache = {
    site_id: ps.dashboard_backend.get_cached_discovered_node(site_id)
    for site_id in ps.dashboard_backend.discovered_node_cache
}
```
Then replace all `seeker_detail_cache.get(node.id)` calls with `all_seeker_details.get(node.id)` and similarly for DN cache lookups.

Apply the same pattern to `discovered_node_detail()` — remove the full `select(Node)` query at line 34 and replace with a targeted lookup by ID.

**Validation:** Discovery page and submap loads should be noticeably faster. No functional change.

---

### Fix 9: Remove Redundant Discovery Polling in Frontend

**File:** `static/js/app.js`

**Problem:** The discovery page sets a 30-second `setInterval` to re-crawl and re-fetch the full discovery graph, **on top of** SSE which already pushes state changes. This doubles the backend load.

**Action:** Find the `setInterval` call in the discovery page initialization (around line 9542-9563, look for `DISCOVERY_REFRESH_MS` or similar). Remove it or change to a much longer safety-net interval (300s / 5 minutes). The SSE stream should be the primary update mechanism.

**Validation:** Discovery page should still update in real-time via SSE. Network tab in browser devtools should show no periodic `/api/discovery/crawl` calls.

---

### Fix 10: Add AbortController + Timeouts to Frontend Fetches

**File:** `static/js/app.js`

**Problem:** Fetch calls have no timeout. If the backend is in a resource crunch, fetches hang indefinitely, making the page appear frozen.

**Action:** Add a timeout wrapper to the existing `apiRequest()` function (or wherever fetch is centralized):
```javascript
async function apiRequest(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000); // 15s timeout
    try {
        const response = await fetch(url, { ...options, signal: controller.signal });
        clearTimeout(timeout);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (err) {
        clearTimeout(timeout);
        if (err.name === 'AbortError') {
            console.warn(`Request to ${url} timed out after 15s`);
            return null; // or throw, depending on caller expectations
        }
        throw err;
    }
}
```

**Also:** On page navigation (if using full page loads), cancel any in-flight requests. Add to any `beforeunload` or navigation handler:
```javascript
// Cancel pending requests on navigation
window.addEventListener('beforeunload', () => {
    // Any active AbortControllers should be aborted
});
```

**Validation:** With a slow backend, pages should show a timeout/error state within 15s instead of hanging indefinitely.

---

## Documentation Updates Required

Per CLAUDE.md, every behavioral change requires doc updates in the same commit:

| File | What to update |
|------|---------------|
| `CHANGELOG.md` | Add entries for each fix under `refactor:` |
| `docs/AGENT_HANDOFF.md` | New entry at top with branch, files touched, what changed |
| `docs/CODE_DOCUMENTATION.md` | Update connection pool config docs, poller architecture section |

---

## Commit Strategy

Break into 2-3 logical commits:
1. `refactor: size connection pool, stagger pollers, increase thread pool` (Fixes 1, 3, 5)
2. `refactor: release DB sessions in pollers, add circuit breaker, reduce timeouts` (Fixes 2, 4)
3. `refactor: remove read-only commits, add chart index, batch discovery lookups` (Fixes 6, 7, 8)
4. `fix: remove redundant discovery polling, add fetch timeouts` (Fixes 9, 10)

Run `python -m unittest discover -s tests` and `python -m compileall app tests alembic` after each commit.

---

## Testing Checklist

After all fixes applied:
- [ ] `python -m compileall app tests alembic` — no syntax errors
- [ ] `python -m unittest discover -s tests` — all tests pass
- [ ] Start app locally or on dev server, verify all pages load
- [ ] Check `db:pool` diag command under load — pool usage should stay well below 50
- [ ] With a known-unreachable node, confirm circuit breaker skips it after first failure
- [ ] Verify discovery page updates via SSE without 30s polling
- [ ] Verify topology page loads within 5s even with slow/unreachable nodes
