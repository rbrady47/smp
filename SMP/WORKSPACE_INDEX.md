# SMP Workspace Index

## 2026-04-11

### CC Handoff: Phase 1 — Incremental DOM Rendering
- **Task:** Write comprehensive CC handoff prompt for Phase 1 of the SMP performance architecture fix (incremental DOM rendering to eliminate renderTopologyStage() innerHTML wipe-and-rebuild pattern)
- **Files created:** `CC_PHASE1_INCREMENTAL_DOM_RENDERING.md`
- **Files referenced:** `CC_ARCHITECTURE_FIX_PLAN.md`, `CC_SSE_LISTENER_LEAK_FIX.md`, `CC_FRONTEND_FREEZE_FIX.md`
- **Source files audited:** `static/js/app.js` (all 38 renderTopologyStage() call sites, drawTopologyLinks(), _updateTopologyEntityDOM()), `app/pollers/dashboard.py`, `app/pollers/seeker.py`, `app/pollers/dn_seeker.py`
- **Decisions:** Categorized all 38 call sites into Category A (9 sites → replace with targeted DOM updates) and Category B (29 sites → keep renderTopologyStage(), now efficient via differential update). Included poller overlap guards as Commit 0 pre-work. Structured as 7 independent commits for safe rollback.
- **Assumptions:** Working branch is `cowork/working-state-2026-04-11` with Phase 3 already complete. `smp-dev` mount is on `main` (line numbers may differ slightly on the working branch due to prior CC edits).

### CC Bugfix: DOM Cache Clear Duplication Bug
- **Task:** Test incremental DOM rendering via Chrome automation, discovered and documented a DOM duplication bug where `_topologyDomCache.clear()` leaves stale DOM nodes in the layer
- **Files created:** `CC_FIX_DOM_CACHE_CLEAR_BUG.md`
- **Bug:** When the cache is cleared at any of the 5 `topologyPayload =` assignment sites, the next `renderTopologyStage()` creates 24 new DOM buttons without removing the existing 24 — resulting in 48 buttons, broken click targeting, and growing DOM count each refresh cycle
- **Fix:** Two-part: (1) when cache is empty and layer has children, remove all `[data-topology-id]` elements before the differential loop; (2) add `isConnected` guard in the per-entity loop for robustness
- **Test results:** Page load ✓, edit mode toggle ✓, node click/tooltip ✓, SSE updates ✓ (Updated timer advancing), memory flat (~8.3MB). Bug only manifests after first `refreshTopologyStructure()` timer fires (~60s)

## 2026-04-12

### CC Bugfix: Topology Nodes Vanish After Refresh
- **Task:** Diagnose and write CC handoff for bug where all topology entities disappear from the map after a refresh cycle, leaving an empty stage with no recovery
- **Files created:** `CC_FIX_TOPOLOGY_VANISH_BUG.md`
- **Source files read:** `static/js/app.js` from working branch `cowork/working-state-2026-04-11` (mounted at `/mnt/smp`)
- **Root cause:** `loadTopologyPage()` error handler (line 7549) clears `layer.innerHTML = ""` but does NOT clear `_topologyDomCache` or `_topologyLinkDomCache`. Cache retains 24 detached references pointing to destroyed DOM nodes. No re-render is triggered. Triggered by `handleVisibilityRecovery()` → `loadTopologyPage()` when API requests fail (tab backgrounding, network timeout, cold start delay).
- **Fix:** Three parts: (1) Add `_topologyDomCache.clear()` + `_topologyLinkDomCache.clear()` in error handler; (2) Defensive all-detached sweep in `renderTopologyStage()` as safety net; (3) Delete dead `refreshTopologyData()` function.
- **Testing performed:** Chrome automation — fresh load 24/24 ✓, forced refresh 24/24 ✓, 5 rapid-fire refreshes 24/24 ✓, edit mode toggle 24/24 ✓, click targeting after refresh ✓, memory stable 9.55MB. Instrumentation traces confirmed cache-clear + render flow works on success path; bug only fires on error path.
- **Also discovered:** `refreshTopologyData()` (line 6038) is dead code — clears caches with no call sites and no render. Recommended deletion.
