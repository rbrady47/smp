# CC Prompt: Fix Truncated/Corrupted Files — TAKE 2

## Context

Your previous fix attempt (commits `63a9feb`, `0d71d10`, `d15cfca`) only repaired 2 of 11 broken files. **9 files remain truncated or corrupted.** The fix commits re-truncated app.js while trying to repair it, proving the failure mode: using Write to replace entire large files exceeds context limits and silently truncates them.

Current compile check output:
```
SyntaxError: app/poller_state.py line 57
SyntaxError: app/pollers/charts.py line 263  — '(' never closed
SyntaxError: app/pollers/dashboard.py line 269 — '(' never closed
SyntaxError: app/pollers/services.py line 240 — '(' never closed
ValueError: app/routes/dashboard.py — null bytes
SyntaxError: app/routes/discovery.py line 563
ValueError: app/routes/nodes.py — null bytes
ValueError: app/routes/topology.py — null bytes
```
Plus `static/js/app.js` still truncated at line 11177 (main has 11195) → `SyntaxError: Unexpected end of input` → ALL frontend JS dead.

## CRITICAL INSTRUCTION — DO NOT USE WRITE TOOL ON ANY OF THESE FILES

The Write tool replaces the entire file content in one shot. For files over ~200 lines, this risks truncation when context is limited. **You MUST use this two-step approach for every file:**

1. `git checkout main -- <filepath>` — restores the complete, intact file from main
2. Use the **Edit tool** (targeted string replacement) to re-apply only the small legitimate changes

**Never use Write to output an entire file.** This is what caused the corruption in the first place, and what caused the re-truncation during your fix attempt.

## Files to Fix (9 files)

### Phase 1: Restore all 9 files from main

Run this single command:
```bash
git checkout main -- \
  static/js/app.js \
  app/topology.py \
  app/routes/discovery.py \
  app/routes/dashboard.py \
  app/routes/nodes.py \
  app/routes/topology.py \
  app/pollers/charts.py \
  app/pollers/dashboard.py \
  app/pollers/services.py
```

Then verify: `python -m compileall app -q` should produce **zero errors**.

### Phase 2: Re-apply legitimate changes using Edit tool ONLY

For each file below, use **targeted Edit operations** (find-and-replace on specific strings). Each change is small — a few lines at most.

---

#### 1. `app/routes/dashboard.py` — Remove 3 spurious `await db.commit()` calls

These are read-only handlers with no mutations. Remove each `await db.commit()` line:

- In `dashboard_nodes()` (around line 21)
- In `dashboard_node_watchlist()` (around line 37)  
- In `node_dashboard_payload()` (around line 47)

Search for `await db.commit()` in this file and remove all 3 occurrences (delete the line entirely).

---

#### 2. `app/routes/nodes.py` — Remove 1 spurious `await db.commit()`

In `node_detail()` (around line 174), remove the `await db.commit()` line. This is a read-only handler.

---

#### 3. `app/routes/topology.py` — Remove 2 spurious `await db.commit()` calls

- In `topology_discovery_payload()` (around line 30)
- In `topology_payload()` (around line 246)

Remove both `await db.commit()` lines.

---

#### 4. `app/pollers/charts.py` — Add sleep jitter

Add `import random` to the imports section (after `import asyncio`).

Find the line:
```python
await asyncio.sleep(CHARTS_POLL_INTERVAL_SECONDS)
```
Replace with:
```python
await asyncio.sleep(CHARTS_POLL_INTERVAL_SECONDS + random.uniform(0, 1.0))
```

---

#### 5. `app/pollers/dashboard.py` — Add sleep jitter

Add `import random` to the imports section (after `import logging`).

Find the final sleep line:
```python
await asyncio.sleep(NODE_DASHBOARD_FAST_REFRESH_SECONDS)
```
Replace with:
```python
await asyncio.sleep(NODE_DASHBOARD_FAST_REFRESH_SECONDS + random.uniform(0, 0.2))
```

---

#### 6. `app/pollers/services.py` — Add sleep jitter

Add `import random` to the imports section (after `import asyncio`).

Find the line:
```python
await asyncio.sleep(SERVICE_POLL_INTERVAL_SECONDS)
```
Replace with:
```python
await asyncio.sleep(SERVICE_POLL_INTERVAL_SECONDS + random.uniform(0, 1.0))
```

---

#### 7. `app/topology.py` — Refactoring (helper extraction + O(1) lookups)

This one has the most changes. Read the file after checkout to understand current structure, then apply these changes:

a) In `topology_status_from_node_status()`: remove `"unknown"` from the status check (only check for actual failure states).

b) Add a new helper function `_make_entity()` that builds topology entity dicts with optional inventory overlay. The function should accept params for id, label, status, location, unit, entity_type, and optional inventory_node, and return the entity dict.

c) In `build_mock_topology_payload()`: pre-load inventory nodes into dicts indexed by location and unit for O(1) lookups instead of creating entities inline.

**IMPORTANT:** For this file, read it carefully after checkout, plan the edit, and apply changes in small targeted Edit operations. Do NOT rewrite the entire file.

---

#### 8. `app/routes/discovery.py` — Query reorder + pre-load cache optimization

a) In `get_submap_discovery()`: move the `nodes = await db.scalars(...)` query to after the cached detail check (avoid hitting DB if cache has data).

b) Add pre-loading of `all_seeker_details` and `all_dn_cache` dicts at the start of the function to batch cache lookups for O(1) access.

c) Replace individual `seeker_detail_cache.get()` calls with `all_seeker_details.get()` lookups.

**IMPORTANT:** Read the file after checkout, understand the function, then apply small targeted edits. Do NOT rewrite it.

---

#### 9. `static/js/app.js` — Visibility recovery + SSE improvements

This is an 11,195-line file. DO NOT attempt to Write it. Use Edit tool for every change.

a) Near the SSE connection variables (around line ~2940), add:
```javascript
let _sseReconnectAttempts = 0;
```

b) In the `connectNodeStateStream()` error/close handlers, change the reconnect delay to be visibility-aware:
```javascript
const delay = document.hidden ? 30000 : 3000;
```

c) Add a `handleVisibilityRecovery()` function that:
   - Reconnects SSE if disconnected
   - Refreshes stale data (dashboard, topology) after tab becomes visible
   - Resets reconnect attempt counter

d) In the `apiRequest()` function, add an AbortController with 15s timeout.

e) Change `DISCOVERY_REFRESH_MS` from `30000` to `300000`.

f) In the `DOMContentLoaded` handler (near end of file), add a single `visibilitychange` event listener that calls `handleVisibilityRecovery()` on visible and disconnects SSE on hidden.

**IMPORTANT:** Each of these is a separate Edit operation. Apply them one at a time. After ALL edits, verify the file ends with the closing `});` on its own line.

---

## Phase 3: Verification (MANDATORY — do not skip)

Run ALL of these and paste the output:

```bash
# 1. Python compile check — must be zero errors
python -m compileall app -q

# 2. Unit tests — all must pass
python -m unittest discover -s tests

# 3. app.js line count — must be >= 11195
wc -l static/js/app.js

# 4. app.js ending — must show closing });
tail -5 static/js/app.js

# 5. No null bytes in any file
for f in app/routes/dashboard.py app/routes/nodes.py app/routes/topology.py; do
  if grep -Pq '\x00' "$f"; then echo "NULL BYTES: $f"; else echo "CLEAN: $f"; fi
done

# 6. No truncated sleep statements
grep -n 'await asyncio.sleep(' app/pollers/charts.py app/pollers/dashboard.py app/pollers/services.py
```

If ANY verification fails, fix the specific failure before committing. Do NOT commit with failures and claim success.

## DO NOT

- Use the Write tool on any file listed above
- Add new features, refactoring, or imports beyond what's specified
- Touch `app/routes/stream.py` or `app/poller_state.py` — these are already fixed
- Skip verification steps
- Claim "all tests pass" without actually running them
