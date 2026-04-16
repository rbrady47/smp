# SMP Agent Instructions

Use these instructions for every agent working on SMP, including newly onboarded agents.

## Project Workflow
- Work in the sandbox repo by default:
  [C:\Users\rick4\.codex\worktrees\601a\smp](C:\Users\rick4\.codex\worktrees\601a\smp)
- Build there first.
- Test in the sandbox before proposing or pushing anything.
- Commit and push only validated changes.
- Pull validated changes into the real repo after they are on `origin/main`.
- Validate against the real runtime/network only after the pull into the real repo.

## Source Of Truth
- Sandbox build workspace:
  [C:\Users\rick4\.codex\worktrees\601a\smp](C:\Users\rick4\.codex\worktrees\601a\smp)
- Real repo:
  [C:\Users\rick4\source\repos\smp](C:\Users\rick4\source\repos\smp)
- Shared handoff log:
  [docs/AGENT_HANDOFF.md](C:\Users\rick4\.codex\worktrees\601a\smp\docs\AGENT_HANDOFF.md)

## Required Coordination Rules
- Read the latest entry in `docs/AGENT_HANDOFF.md` before starting work.
- Add a new handoff entry when you stop or when you complete a meaningful slice.
- Do not revert someone else’s edits unless explicitly instructed.
- Assume other agents may be editing nearby code; inspect current file state before making changes.
- Prefer owning a feature area, not just a file.

## Git Rules
- Work from the sandbox repo unless explicitly told otherwise.
- Test before commit.
- Use clear commit messages describing the validated slice.
- Push validated changes to `origin/main`.
- In the real repo, stash local work before pulling if the tree is dirty.

## Validation Rules
- Default repo checks:
  - `.\scripts\test.ps1`
  - `.\.venv\Scripts\python.exe -m compileall app tests alembic`
- If the task affects app startup, migrations, or live behavior, also do a runtime smoke test in the real repo.
- If database schema already exists but Alembic history is behind, inspect before applying migrations blindly.

## Documentation Rules
- Update user-facing documentation when behavior changes:
  - [docs/USER_GUIDE.md](C:\Users\rick4\.codex\worktrees\601a\smp\docs\USER_GUIDE.md)
  - [CHANGELOG.md](C:\Users\rick4\.codex\worktrees\601a\smp\CHANGELOG.md)
- Update `docs/AGENT_HANDOFF.md` for engineering coordination.

## Current Architecture Direction
- Node Dashboard is the root operational data source.
- ANs and DNs are first-class data sources.
- Discovery/truth topology is separate from authored operational maps.
- Operational maps are SNMPc-style authored canvases with:
  - blank canvas start
  - object types: `node`, `submap`, `label`
  - connection points on objects
  - submap drill-in and back navigation
  - object/link bindings to controlled SMP data fields

## Current Priority
- Continue operational-map implementation from the validated sandbox baseline.
- Current topology direction: keep building on `/topology`; do not reintroduce `/operational-maps` as a separate user-facing path unless explicitly directed.

## Handoff Format
Use this in `docs/AGENT_HANDOFF.md`:
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

## Copy/Paste Kickoff Prompt
Use this prompt to onboard a new agent quickly:

```text
You are working on SMP.

Follow this workflow:
- Work in the sandbox repo by default: C:\Users\rick4\.codex\worktrees\601a\smp
- Build there first
- Test there before proposing or pushing anything
- Commit and push only validated changes
- Pull validated changes into the real repo only after they are on origin/main: C:\Users\rick4\source\repos\smp
- Validate against the real runtime/network only after the real repo is updated

Before starting:
- Read docs/AGENT_HANDOFF.md
- Read docs/AGENT_INSTRUCTIONS.md
- Do not revert other agents’ work
- Inspect current file state before editing

Coordination requirements:
- Add a handoff entry to docs/AGENT_HANDOFF.md when you stop or complete a meaningful slice
- Report files touched, assumptions, verification run, blockers, and next recommended step

Validation defaults:
- .\scripts\test.ps1
- .\.venv\Scripts\python.exe -m compileall app tests alembic

Current architecture direction:
- Node Dashboard is the root operational data source
- Discovery/truth topology is separate from authored operational maps
- Operational maps are SNMPc-style authored canvases with node, submap, and label objects

Current priority:
- Continue from the latest validated sandbox baseline unless instructed otherwise
```
