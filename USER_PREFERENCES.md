# User Preferences — Rick Brady

This document captures how the operator (Rick) likes to work with Claude Code on SMP. Update after each session.

## Working Style

- **Iterative and visual** — Rick tests in real-time on a running server (Windows dev + OEL9 deployment). He provides screenshots and server logs to confirm behavior.
- **Ship fast, fix in flight** — Prefers to push changes, pull, restart, and test immediately rather than lengthy planning. If it breaks, we debug from logs together.
- **Numbered task lists** — Respond well to numbered checklists (P5.2.3, P5.2.4, etc.). Complete one item, confirm it works, move to the next.
- **First-time-go is the goal** — Rick praises "first time go" results. Aim for clean, working code on the first push.

## Communication Preferences

- **Be concise** — Short answers, lead with what changed, skip the preamble.
- **Show the list** — When wrapping up a task, show what's done and what's next so Rick can choose direction.
- **Don't over-explain** — Rick understands the tech stack. Explain the "what" and "why" of a fix, not basic concepts.
- **Screenshots are feedback** — When Rick sends a screenshot, look at it carefully. The visual state IS the bug report.

## Development Environment

- **Primary dev**: Windows (PowerShell), Python 3.11+, `.venv\Scripts\python.exe`
- **Deployment target**: Oracle Enterprise Linux 9 (OEL9)
- **PowerShell quirks**: `VAR=value command` doesn't work. Use `$env:VAR="value"` or change code instead.
- **Server restart**: After `git pull`, Rick manually restarts uvicorn. Changes aren't live until he says he pulled.
- **Browser caching**: CSS/JS can be cached. If a visual fix doesn't seem to work, suggest Ctrl+Shift+R (hard refresh) before re-investigating code.
- **Cache bust**: `app.js` uses a `?v=` query param in topology.html. Bump it when making significant JS changes.

## Code Preferences

- **No new tooling** — Don't add pytest, ruff, Docker, CI/CD, or new frameworks.
- **unittest only** — Python unittest, SQLite in-memory for tests.
- **Compile check before commit** — Always run `python -m compileall app tests alembic` before pushing.
- **Keep it simple** — Prefer straightforward solutions over elegant abstractions. Three similar lines > a premature helper.
- **Commit often** — Small, focused commits with clear messages. Push after each logical change.

## UX / Design Preferences

- **Consistency matters** — ANs and DNs should behave the same way (double-click = web session, right-click = detail panel, hover = tooltip).
- **Reduce clutter** — Discovery links hidden by default, revealed on hover/pin. Only down/red links leave a persistent indicator (AP dot color).
- **Flash then fade** — State changes flash briefly to draw attention, then fade. Don't leave things permanently visible.
- **Edit mode shows everything** — When in Edit Map mode, all links should be visible for configuration.
- **Operator-centric** — The map is for military operators. Status should be obvious at a glance. Red = problem, green = good.

## Domain Knowledge

- Rick is building SMP for Army/military Seeker SDN node management.
- AN = Anchor Node (operator-managed, registered in SMP, has API credentials)
- DN = Discovered Node (auto-found via tunnel analysis, no direct management)
- S&T = Sites and Tunnels (Seeker's tunnel peer list)
- Topology units: AGG, DIV HQ, 1BCT, 2BCT, 3BCT, CAB/DIVARTY, Sustainment
- DN-DN tunnels are often temporary (line-of-sight radio, come and go)
- When Seeker removes a tunnel from S&T, SMP should remove the link too

## Session Patterns

- Rick typically works in multi-hour sessions, testing as we go.
- He names tasks with version-style numbers (P5.2.3, P5.2.4, etc.) and works through them sequentially.
- At end of session, he wants: checkpoint saved, handoff documented, code documented.
- He may pull the same branch to different machines (Windows dev, OEL9 deployment) for testing.

## Things That Have Bitten Us

- **CSS animation vs opacity** — SVG animations override static opacity. Use `visibility: hidden` instead.
- **Pydantic `extra="forbid"`** — Any new field in a dict payload MUST be added to the schema or it throws 500.
- **SVG rebuild flicker** — Discovery links are SVG elements rebuilt every render. Pre-reveal pinned links at creation time.
- **`_tunnel_row_is_eligible` filters too aggressively** — It requires ping=up. Use `_tunnel_row_exists` for links to already-known peers.
- **`seeker_detail_cache` timing** — On cold start, cache is empty for the first few seconds. Inventory filters that depend on it will miss ANs temporarily.
- **`node_id` can be NULL** — Not all Node records have `node_id` populated. The backfill in `seeker_polling_loop` fixes this over time.

---
*Last updated: 2026-04-01*
