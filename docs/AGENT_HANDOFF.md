# Agent Handoff

This file is the shared handoff log for agents working on SMP.

## How To Use
- Add new entries at the top.
- Keep entries short and concrete.
- Record only what another agent needs to continue safely.
- Do not delete older entries unless they are clearly obsolete and superseded.

## Entry Template
```md
## YYYY-MM-DD HH:MM ET - Agent/Task
- Scope:
- Branch/worktree:
- Latest validated commit:
- Files touched:
- Verification run:
- Assumptions:
- Open risks / blockers:
- Next recommended step:
```

## 2026-03-29 10:11 ET - Node Dashboard / AN+DN fail-closed health
- Scope: Extended the disconnected-network fix so both anchor and discovered rows fail closed instead of reusing stale live state when current reachability is unavailable.
- Branch/worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit: `d792da9` plus local uncommitted sandbox changes
- Files touched:
  [app/main.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\main.py)
  [app/node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_dashboard_backend.py)
  [app/node_discovery_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_discovery_service.py)
  [tests/test_node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_node_dashboard_backend.py)
  [tests/test_node_dashboard_summary.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_node_dashboard_summary.py)
  [docs/USER_GUIDE.md](C:\Users\rick4\.codex\worktrees\601a\smp\docs\USER_GUIDE.md)
  [CHANGELOG.md](C:\Users\rick4\.codex\worktrees\601a\smp\CHANGELOG.md)
- Verification run:
  - `.\scripts\test.ps1`
  - `.\.venv\Scripts\python.exe -m compileall app tests alembic`
- Assumptions:
  - The Node Dashboard should be pessimistic when disconnected from the Seeker network: stale live indicators are worse than temporarily showing down.
  - Last-known identity metadata may remain visible, but current reachability indicators must be sourced from fresh checks.
- Open risks / blockers:
  - No local HTTP server was running in the sandbox during this slice, so runtime verification still depends on restarting the app instance the user is actually viewing.
  - Existing unrelated local edits remain in the worktree.
- Next recommended step:
  - Restart the running SMP app from the intended checkout and verify `/nodes/dashboard` against the disconnected-network condition before pushing or syncing to the real repo.

## 2026-03-29 09:52 ET - Node Dashboard / DN stale-live-state fix
- Scope: Fixed discovered-node refresh behavior so off-network or unreachable DNs stop inheriting stale `Up`, RTT, Web, SSH, and traffic state from cache; documented the behavior.
- Branch/worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit: `d792da9` plus local uncommitted sandbox changes
- Files touched:
  [app/node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_dashboard_backend.py)
  [app/node_discovery_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_discovery_service.py)
  [tests/test_node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_node_dashboard_backend.py)
  [docs/USER_GUIDE.md](C:\Users\rick4\.codex\worktrees\601a\smp\docs\USER_GUIDE.md)
  [CHANGELOG.md](C:\Users\rick4\.codex\worktrees\601a\smp\CHANGELOG.md)
- Verification run:
  - `.\scripts\test.ps1`
  - `.\.venv\Scripts\python.exe -m compileall app tests alembic`
- Assumptions:
  - For the Node Dashboard, stale DN health should fail closed once the short ping TTL expires.
  - Last-known DN identity/version can remain visible, but live-state indicators must not remain optimistic when disconnected.
- Open risks / blockers:
  - Anchor nodes use a separate ping/status pipeline and were not changed in this slice.
  - Existing unrelated local edits remain in the sandbox worktree; do not revert them blindly.
- Next recommended step:
  - Fix discovered-node provenance rendering in `/nodes/dashboard` so surfaced-by context is always visible and consistent with the backend relationship data.

## 2026-03-29 10:30 ET - Operational Maps / Sandbox-to-Real Sync
- Scope: Reconciled the sandbox worktree to the latest real-repo baseline, then layered the first authored operational-map slice on top.
- Branch/worktree: `C:\Users\rick4\.codex\worktrees\601a\smp`
- Latest validated commit: `d792da9` pushed to `origin/main`
- Files touched:
  [app/main.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\main.py)
  [app/models.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\models.py)
  [app/schemas.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\schemas.py)
  [app/topology.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\topology.py)
  [app/node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_dashboard_backend.py)
  [app/node_discovery_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\node_discovery_service.py)
  [app/operational_map_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\app\operational_map_service.py)
  [static/js/app.js](C:\Users\rick4\.codex\worktrees\601a\smp\static\js\app.js)
  [static/js/operational_maps.js](C:\Users\rick4\.codex\worktrees\601a\smp\static\js\operational_maps.js)
  [static/css/style.css](C:\Users\rick4\.codex\worktrees\601a\smp\static\css\style.css)
  [templates/topology.html](C:\Users\rick4\.codex\worktrees\601a\smp\templates\topology.html)
  [templates/operational_maps.html](C:\Users\rick4\.codex\worktrees\601a\smp\templates\operational_maps.html)
  [alembic/versions/20260327_0007_add_node_topology_columns.py](C:\Users\rick4\.codex\worktrees\601a\smp\alembic\versions\20260327_0007_add_node_topology_columns.py)
  [alembic/versions/20260327_0008_add_node_relationships_table.py](C:\Users\rick4\.codex\worktrees\601a\smp\alembic\versions\20260327_0008_add_node_relationships_table.py)
  [alembic/versions/20260329_0009_add_operational_map_tables.py](C:\Users\rick4\.codex\worktrees\601a\smp\alembic\versions\20260329_0009_add_operational_map_tables.py)
  [tests/test_node_dashboard_backend.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_node_dashboard_backend.py)
  [tests/test_topology.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_topology.py)
  [tests/test_operational_map_schemas.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_operational_map_schemas.py)
  [tests/test_operational_map_service.py](C:\Users\rick4\.codex\worktrees\601a\smp\tests\test_operational_map_service.py)
- Verification run:
  - `.\scripts\test.ps1`
  - `.\.venv\Scripts\python.exe -m compileall app tests alembic`
  - Real repo runtime smoke on port `8012` against local Postgres
- Assumptions:
  - Sandbox worktree is the build source.
  - Real repo is updated only with validated sandbox commits.
  - Node objects in operational maps must resolve to an anchor `node_id` or discovered `site_id`.
- Open risks / blockers:
  - Real Postgres schema had drifted from Alembic history. DB was stamped to `20260329_0009` because the tables already existed.
  - The authored-map UI is only the first slice: create/select maps, place objects, drag/save, basic inspector.
- Next recommended step:
- Add link authoring and binding UI on `/operational-maps`.

## 2026-03-29 13:05 ET - Operational Maps Reframed Back Into Topology

- Decision:
  - removed the standalone `/operational-maps` page from the sandbox app
  - retained the SNMPc-style ideas as reference material instead of continuing a separate user-facing feature
- Current direction:
  - build on `/topology`
  - keep discovery truth first-class
  - fold authored-layout and binding ideas into topology incrementally
- New references:
  - [CURRENTP_TO_SNMPc_MAP_REFERENCE.md](C:\Users\rick4\.codex\worktrees\601a\smp\CURRENTP_TO_SNMPc_MAP_REFERENCE.md)
  - [TOPOLOGY_EVOLUTION_PLAN.md](C:\Users\rick4\.codex\worktrees\601a\smp\TOPOLOGY_EVOLUTION_PLAN.md)
