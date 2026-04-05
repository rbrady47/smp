# CLAUDE.md — SMP (Seeker Management Platform)

## Project Overview

SMP is a centralized dashboard, monitoring, and topology visualization platform for Seeker SDN nodes. It provides operators with real-time status, health monitoring, auto-discovery, and SNMPc-style authored operational maps.

**Tech stack:** Python 3.11+ · FastAPI · SQLAlchemy 2.0 · PostgreSQL · Alembic · Jinja2 · vanilla JS/CSS frontend

**Status:** Prototype — actively evolving (current version ~v0.9.5)

## Repository Layout

```
app/                    # Backend application code
  main.py               # FastAPI app, routes, background tasks, constants
  models.py             # SQLAlchemy ORM models (Mapped[] syntax)
  schemas.py            # Pydantic request/response models
  db.py                 # Database engine, session factory, get_db() dependency
  seeker_api.py         # Upstream Seeker API integration
  node_dashboard_backend.py   # In-memory cache & projection engine for dashboard
  node_discovery_service.py   # Auto-discovery from tunnel/peer data
  node_projection_service.py  # Anchor node projection builder
  node_watchlist_projection_service.py  # Watchlist projection
  operational_map_service.py  # CRUD for authored operational maps
  topology.py           # Topology payload generation
  topology_editor_state_service.py  # Editor state persistence
  bwvstats_ingest.py    # Bandwidth stats ingestion
  telemetry.py          # Telemetry utilities
alembic/                # Database migrations (13 versions)
templates/              # Jinja2 HTML templates
static/                 # Frontend assets (js/app.js, css/style.css)
tests/                  # Unit tests (unittest framework)
scripts/                # PowerShell dev scripts (bootstrap, dev, test)
docs/                   # Documentation (USER_GUIDE, AGENT_INSTRUCTIONS, AGENT_HANDOFF)
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
- **Database:** Tests use SQLite in-memory (`sqlite:///:memory:`) for isolation
- **Run all tests:** `python -m unittest discover -s tests`
- **Always run tests and compile check before committing**

## Database

- **Engine:** PostgreSQL (required for production; SQLite used in tests only)
- **ORM:** SQLAlchemy 2.0 with `Mapped[]` type annotations
- **Migrations:** Alembic — migrations live in `alembic/versions/`
- **Connection:** Set `DATABASE_URL` environment variable (see `.env.example`)
- **Key tables:** `nodes`, `discovered_nodes`, `discovered_node_observations`, `service_checks`, `node_relationships`, `topology_links`, `topology_editor_state`, `operational_map_*`

### Creating Migrations

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Code Conventions

### Python Style
- **Type hints everywhere** — use Python 3.10+ union syntax (`str | None`, not `Optional[str]`)
- **Async/await** for I/O-bound operations (ping, HTTP calls, polling)
- **Pydantic models** with `ConfigDict(extra="forbid")` for strict validation
- **SQLAlchemy mapped classes** with `Mapped[]` type annotations
- **snake_case** for functions and variables; **PascalCase** for classes
- **No linter configured** — follow existing code style

### Architecture Patterns
- **Service layer:** `*_service.py` modules contain business logic
- **Projection services:** `*_projection_service.py` build derived API views
- **In-memory caches:** Dashboard backend maintains caches refreshed on intervals
- **Dependency injection:** Database sessions via FastAPI `Depends(get_db)`

### API Routes
- RESTful JSON APIs under `/api/`
- Route paths use kebab-case (e.g., `/api/dashboard/nodes`, `/api/topology/discovery`)
- HTML pages served via Jinja2 templates at root paths

### Data Flow
1. **Live status:** Node → Ping → RTT → Cache → Dashboard Projection → API
2. **Discovery:** Anchor Node Detail → Tunnel Analysis → Candidate Generation → DB → Discovery Cache
3. **Refresh cadence:** ping burst 15s, seeker poll 15s, service checks 30s

## Architecture Direction

- **Node Dashboard** is the root operational data source
- **Anchor Nodes (ANs)** and **Discovered Nodes (DNs)** are first-class data sources
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
| Seeker | SDN node type that SMP manages |
| Operational Map | SNMPc-style authored diagram canvas |
| Discovery Topology | Auto-discovered network graph |
| TopologyUnit | Military org unit (AGG, DIV HQ, 1BCT, 2BCT, 3BCT, etc.) |

## What NOT to Do

- Do not add pytest, ruff, or other tooling without explicit instruction
- Do not create Docker/CI configs unless asked
- Do not change the testing framework from unittest
- Do not modify `alembic/env.py` without understanding the migration chain
- Do not add unnecessary abstractions or speculative features
