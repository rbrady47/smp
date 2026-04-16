# CC Task: Merge fix branch into main

## Task

Merge `claude/fix-topology-nodes-refresh-nuPrV` into `main` and push.

## Steps

```bash
git fetch origin
git checkout main
git pull origin main
git merge origin/claude/fix-topology-nodes-refresh-nuPrV --no-edit
git push origin main
```

## Context

This branch contains the full incremental DOM rendering feature plus all related bugfixes, built and tested over multiple sessions:

1. `snapshot: capture local working state` — baseline
2. `fix: tab visibility recovery + repair truncated snapshot files`
3. `fix: repair truncated docker-compose.yml from snapshot`
4. `fix: restore truncated app.js ending, deduplicate event listeners`
5. `fix: restore intact app.js and topology.py, re-apply all changes via Edit`
6. `fix: SSE listener leak, keepalive hardening, Redis connection limits`
7. `fix: apiRequest timeout, discovery RAF leak, visibility pause/resume`
8. `fix: add overlap guards to all poller loops`
9. `feat: incremental DOM rendering for topology stage`
10. `fix: clear stale DOM nodes when topology cache is empty before differential render`
11. `fix: clear topology DOM cache in loadTopologyPage error handler`

All commits have been tested via Chrome automation on a running Docker instance. No merge conflicts expected — the branch was built on top of main.

## Post-merge

Delete the remote branch after merge:

```bash
git push origin --delete claude/fix-topology-nodes-refresh-nuPrV
```
