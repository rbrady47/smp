# CLAUDE.md — SMP (Seeker Management Platform)

## Project Overview

SMP is a centralized dashboard, monitoring, and topology visualization platform for Seeker SDN nodes. It provides operators with real-time status, health monitoring, auto-discovery, and SNMPc-style authored operational maps.

**Tech stack:** Python 3.11+ · FastAPI · Async SQLAlchemy 2.0 (`AsyncSession`) · PostgreSQL (psycopg 3.x async) · Alembic · Jinja2 · vanilla JS/CSS frontend

**Status:** Prototype — actively evolving (current version ~v0.10)

## Repository Layout

```
app/                    # Backend application code
  main.py               # FastAPI app, lifespan, constants, background task launchers
  models.py             # SQLAlchemy ORM models (Mapped[] syntax)
  schemas.py            # Pydantic request/response models
  db.py                 # Database engine, session factory, get_db() dependency
  seeker_api.py         # Upstream Seeker API integration (login, bwvCfg, bwvStats, bwvChartStats)
  redis_client.py       # Optional Redis connection
  state_manager.py      # Redis pub/sub for SSE events
  poller_state.py       # PollerState dataclass — owns all mutable in-memory state
  node_dashboard_backend.py   # In-memory cache & projection engine for dashboard
  node_discovery_service.py   # Auto-discovery from tunnel/peer data
  node_projection_service.py  # Anchor node projection builder
  node_watchlist_projection_service.py  # Watchlist projection
  operational_map_service.py  # CRUD for authored operational maps
  topology.py           # Topology payload generation
  topology_editor_state_service.py  # Editor state persistence
  bwvstats_ingest.py    # Bandwidth stats ingestion
  telemetry.py          # Telemetry utilities
  services/             # Business logic services
    node_health.py      # Health check utilities
  pollers/              # Background polling loops
    seeker.py           # AN Seeker API polling (10s)
    charts.py           # Charts data polling — bwvChartStats (60s)
    dashboard.py        # Dashboard projection polling
    ping.py             # Ping monitoring
    services.py         # Service check polling
  routes/               # FastAPI route modules
    pages.py            # HTML page routes
    nodes.py            # /api/nodes CRUD, detail, refresh
    services.py         # /api/services CRUD + dashboard
    dashboard.py        # /api/dashboard, /api/node-dashboard
    topology.py         # /api/topology, links, editor-state
    maps.py             # /api/topology/maps CRUD
    discovery.py        # /api/discovered-nodes, submap discovery, DN promotion
    stream.py           # SSE endpoints
    charts.py           # /api/nodes/{id}/chart-stats, chart-summary
    system.py           # /api/status
alembic/                # Database migrations (17 versions)
templates/              # Jinja2 HTML templates (9 pages)
static/                 # Frontend assets (js/app.js, css/style.css)
tests/                  # Unit tests (unittest framework)
scripts/                # PowerShell dev scripts (bootstrap, dev, test)
docs/                   # Documentation (USER_GUIDE, AGENT_HANDOFF, CODE_DOCUMENTATION)
```

## Quick Commands

```bash
# Install dependencies (creates .venv)
python -m venv .venv && .venv/bin/pip install -r requirements.txt

# Run tests
python -m unittest discover -s tests

# Compile check (catch syntax errors)
python -m compileall app tests alembic

# Apply database migrations
DATABASE_URL="postgresql+psycopg://..." alembic upgrade head

# Start dev server
DATABASE_URL="postgresql+psycopg://..." uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## Testing

- **Framework:** Python `unittest` (no pytest)
- **Location:** `tests/test_*.py`
- **Database:** Tests use async SQLite in-memory (`sqlite+aiosqlite:///:memory:`) with `IsolatedAsyncioTestCase`
- **Run all tests:** `python -m unittest discover -s tests`
- **Always run tests and compile check before committing**

## Database

- **Engine:** PostgreSQL with async SQLAlchemy 2.0 (`create_async_engine` + `AsyncSession`)
- **Driver:** psycopg 3.x in async mode (same `postgresql+psycopg://` URL for both sync Alembic and async app)
- **ORM:** SQLAlchemy 2.0 with `Mapped[]` type annotations
- **Session:** `AsyncSessionLocal` with `expire_on_commit=False` — prevents detached-instance errors
- **Dependency:** `async def get_db()` yields `AsyncSession` via `async with AsyncSessionLocal()`
- **Migrations:** Alembic — uses its own sync engine via `engine_from_config()`, imports only `DATABASE_URL` and `Base`
- **Connection:** Set `DATABASE_URL` environment variable (see `.env.example`)
- **Key tables:** `nodes`, `discovered_nodes`, `discovered_node_observations`, `service_checks`, `node_relationships`, `topology_links`, `topology_editor_state`, `chart_samples`, `operational_map_*`
- **CRITICAL:** All DB operations in async functions MUST be awaited (`await db.scalars()`, `await db.commit()`, etc.). Only `db.add()` remains sync. See `docs/CODE_DOCUMENTATION.md` Anti-Pattern #6.

### Creating Migrations

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Code Conventions

### Python Style
- **Type hints everywhere** — use Python 3.10+ union syntax (`str | None`, not `Optional[str]`)
- **Async/await** for ALL I/O-bound operations (DB queries, ping, HTTP calls, polling)
- **Pydantic models** with `ConfigDict(extra="forbid")` for strict validation
- **SQLAlchemy mapped classes** with `Mapped[]` type annotations
- **snake_case** for functions and variables; **PascalCase** for classes
- **No linter configured** — follow existing code style

### Architecture Patterns
- **Service layer:** `*_service.py` modules contain business logic
- **Projection services:** `*_projection_service.py` build derived API views
- **In-memory caches:** Dashboard backend maintains caches refreshed on intervals
- **Dependency injection:** `AsyncSession` via FastAPI `Depends(get_db)`
- **Poller DB access:** `async with AsyncSessionLocal() as db:` — never use sync `SessionLocal`

### API Routes
- RESTful JSON APIs under `/api/`
- Route paths use kebab-case (e.g., `/api/dashboard/nodes`, `/api/topology/discovery`)
- HTML pages served via Jinja2 templates at root paths

### Data Flow
1. **Live status:** Node → Ping → RTT → Cache → Dashboard Projection → API
2. **Discovery:** Anchor Node Detail → Tunnel Analysis → Candidate Generation → DB → Discovery Cache
3. **Charts:** bwvChartStats(startTime=0, entries=30) → `chart_samples` table → 5-min bucket API → Chart.js UI
4. **DN Promotion:** DN detail page → Promote modal → `POST /api/discovered-nodes/{site_id}/promote` → creates AN, deletes DN
5. **Refresh cadence:** ping burst 5s, seeker poll 10s, service checks 30s, charts 60s, site name resolution 30s

## Architecture Direction

- **Node Dashboard** is the root operational data source
- **Anchor Nodes (ANs)** and **Discovered Nodes (DNs)** are first-class data sources
- **DN → AN promotion** allows operators to convert discovered nodes into fully managed anchor nodes with API credentials, enabling polling, charts, and topology participation
- **Charts polling** collects per-second traffic counters (bwvChartStats) from each AN every 60s; server-side 5-min bucketing reduces payloads for weekly reporting
- **Discovery topology** (auto-discovered network state) is separate from **authored operational maps** (operator-drawn diagrams)
- **Operational maps** follow SNMPc-style patterns: blank canvas, object types (`node`, `submap`, `label`), connections, submap drill-in, live data bindings
- Keep building on `/topology`; do not reintroduce `/operational-maps` as a separate path unless directed

## Agent Coordination

- **Read `docs/CLAUDE_GIT_INSTRUCTIONS.md`** at the beginning of every session and follow them closely — it defines the mandatory git ritual (pre-work checklist, branch naming, commit protocol, end-of-session checklist)
- **Read `docs/AGENT_HANDOFF.md`** before starting work — it contains the latest session state
- **Add a handoff entry** when you stop or complete a meaningful slice
- Do not revert other agents' edits unless explicitly instructed
- Inspect current file state before editing — other agents may be working nearby

## Documentation Requirements

Every change must be fully documented for follow-on development and end users. This is not optional.

### Always update these files when behavior changes:

| File | When to update | What to include |
|------|---------------|-----------------|
| `docs/USER_GUIDE.md` | Any operator-visible change | What the feature does, how to use it, current behavior |
| `CHANGELOG.md` | Any notable change | Concise entry describing what changed and why |
| `docs/AGENT_HANDOFF.md` | Completing a meaningful slice or ending a session | Scope, branch, files touched, verification, assumptions, gaps, next steps |
| `docs/CODE_DOCUMENTATION.md` | Any backend/frontend architecture change | Updated architecture details, new endpoints, new state, new data flow |

### Documentation standards:

- **User Guide (`docs/USER_GUIDE.md`):** Write for operators, not developers. Describe what the feature does and how to use it. Keep language clear and practical. Update existing sections rather than appending duplicates.
- **Code Documentation (`docs/CODE_DOCUMENTATION.md`):** Write for follow-on developers. Document new API endpoints, backend functions, frontend state changes, data flow changes, and schema additions. Keep it current with the actual codebase.
- **Changelog (`CHANGELOG.md`):** One-line summaries grouped by type (feat, fix, refactor). Include the date.
- **Handoff (`docs/AGENT_HANDOFF.md`):** Use the standard handoff template. Be specific about what was built, what works, what doesn't, and what to do next.

### Rules:

- Do not skip documentation because the code change feels small — if it changes behavior, document it
- Do not leave documentation updates for a later commit — include them in the same commit as the code change
- Do not duplicate content across docs — each file has a distinct audience and purpose
- Review existing doc sections before adding new ones to avoid drift or contradiction

## Key Domain Terms

| Term | Meaning |
|------|---------|
| AN (Anchor Node) | Operator-managed Seeker node registered in SMP |
| DN (Discovered Node) | Auto-discovered node found via tunnel/peer analysis |
| DN Promotion | Converting a DN to a full AN with API credentials |
| Seeker | SDN node type that SMP manages |
| bwvChartStats | Seeker API endpoint returning per-second traffic counters |
| ChartSample | DB row storing one second of traffic data (user bytes, channels, tunnels) |
| Operational Map | SNMPc-style authored diagram canvas |
| Discovery Topology | Auto-discovered network graph |
| TopologyUnit | Military org unit (AGG, DIV HQ, 1BCT, 2BCT, 3BCT, etc.) |

## What NOT to Do

- Do not add pytest, ruff, or other tooling without explicit instruction
- Do not create Docker/CI configs unless asked
- Do not change the testing framework from unittest
- Do not modify `alembic/env.py` without understanding the migration chain
- Do not add unnecessary abstractions or speculative features
- Do not use synchronous `create_engine` or `SessionLocal` in application code — all DB access must be async
- Do not call `db.scalars()`, `db.commit()`, `db.execute()`, etc. without `await` in async functions
