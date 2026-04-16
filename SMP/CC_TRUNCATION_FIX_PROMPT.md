# CC Prompt: Fix Truncated Files on cowork/working-state-2026-04-11

## Situation

Your previous two commits on `cowork/working-state-2026-04-11` (`63a9feb` and `0d71d10`) contain correct code changes — visibility recovery, SSE interruptible sleep, apiRequest timeout, etc. — but **11 files were truncated during the write process**. They are cut off mid-line with no closing braces, no final newline, and some contain null bytes. The result is:

- `app.js` throws `SyntaxError: Unexpected end of input` — **all JavaScript fails**, meaning zero frontend functionality (no theme, no SSE, no API calls, no dashboard refresh)
- Multiple Python backend files are incomplete — the server may still run because Python already compiled the prior versions, but any restart will fail

## The 11 truncated files

| File | Truncated at | Main branch lines | Current lines |
|------|-------------|-------------------|---------------|
| `static/js/app.js` | `if (dashboardRefreshBu` | 11195 | 11177 |
| `app/topology.py` | `    l` | 351 | 336 |
| `app/routes/stream.py` | `media_type="text/eve` | 158 | ~155 |
| `app/routes/discovery.py` | `async def save_dn_position` | 564 | 562 |
| `app/routes/dashboard.py` | null byte | 48 | ~45 |
| `app/routes/nodes.py` | null byte | 317 | ~314 |
| `app/routes/topology.py` | null byte | 250 | ~248 |
| `app/pollers/charts.py` | `await asyncio.sleep(CHARTS_POLL_INTE` | 262 | 262 |
| `app/pollers/dashboard.py` | `await asyncio.sleep(NODE_DASHBOARD_FAST_REF` | 268 | 268 |
| `app/pollers/services.py` | `summary[status_value] = summary.get(st` | 240 | ~239 |
| `app/poller_state.py` | `service_poll_task: ` | 55 | 56 |

## What to do

For each truncated file, **restore the complete version from `main`**, then **re-apply only your legitimate changes on top**. Do NOT attempt to patch or append to the truncated files — start from the known-good `main` version each time.

Your commits from `63a9feb` contain these real changes that need to be preserved (verified via `git diff --ignore-cr-at-eol main 63a9feb`):

### `static/js/app.js` (90 insertions, 73 deletions vs main)
- Added `let _sseReconnectAttempts = 0;` global
- SSE `onerror`: visibility-aware reconnect delay (3s visible / 30s hidden)
- SSE `onopen`: reset `_sseReconnectAttempts`
- Added `handleVisibilityRecovery()` function (reconnects SSE, refreshes active page)
- `apiRequest()`: added `AbortController` with 15s timeout
- Registered `visibilitychange` listener in `DOMContentLoaded`
- **CRITICAL:** The file MUST end with the complete `DOMContentLoaded` closing — check `main` branch for the correct ending (lines ~11178-11195 include the `dashboardRefreshButton` handler, `applyDashboardRefreshInterval`, `startTopologyTimers`, `beforeunload` handler, and the closing `});`)

### `app/routes/stream.py` (28 insertions, 3 deletions)
- Added `_KEEPALIVE_INTERVAL = 15.0` and `_KEEPALIVE_TICK = 1.0` constants
- Added `_interruptible_sleep()` helper function
- Changed all 3 SSE generators: data-changed path sleeps 1s, keep-alive path uses `_interruptible_sleep(_KEEPALIVE_INTERVAL)`

### `app/topology.py` (89 insertions, 41 deletions)
- Significant refactoring — review the diff with `git diff --ignore-cr-at-eol main 63a9feb -- app/topology.py`

### `app/routes/discovery.py` (17 insertions, 6 deletions)
- Batched cache lookups and deferred query optimizations

### `app/poller_state.py` (4 insertions)
- Added circuit breaker fields

### `app/pollers/charts.py`, `app/pollers/dashboard.py`, `app/pollers/services.py` (1-3 lines each)
- Sleep jitter additions

### `app/routes/dashboard.py` (3 deletions), `app/routes/nodes.py` (1 deletion), `app/routes/topology.py` (2 deletions)
- Removed `await db.commit()` from read-only GET handlers

## Approach

1. Read `docs/AGENT_HANDOFF.md` for full context
2. For each of the 11 files: `git show main:<filepath>` to get the intact version, then apply the changes listed above
3. Ensure **every file ends with a proper newline** and has no null bytes
4. Verify `app.js` parses without errors: the `DOMContentLoaded` block must close properly with `});`
5. Run `python -m compileall -f app tests alembic` — must pass
6. Run `python -m unittest discover -s tests` — must pass
7. Test in browser: theme toggle should appear, SSE should connect, dashboard should load data, "Last Update" should show a timestamp (not "Loading...")
8. Commit with a clear message, update `CHANGELOG.md` and `docs/AGENT_HANDOFF.md`

## What NOT to do

- Do not rewrite or refactor any code beyond restoring + re-applying the changes listed above
- Do not change line endings (keep CRLF if that's what the working copy uses)
- Do not add new features
- Do not modify files that aren't in the truncated list
