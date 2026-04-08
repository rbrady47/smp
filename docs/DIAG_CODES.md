# SMP Diagnostic Codes

This is the living catalog of all diagnostic codes available in the SMP Diag Console (`/health` page).

## Usage

Type a diag code into the console input and press Enter or click Run. Codes accept optional arguments in `key=value` format.

```
poller:status
cache:detail name=seeker_detail_cache
node:detail node_id=42
```

## Code Catalog

| Code | Type | Args | Description |
|------|------|------|-------------|
| `help` | query | — | List all available diag codes with descriptions |
| `poller:status` | query | — | Show all poller task states (running/done/cancelled), intervals, and error info |
| `cache:stats` | query | — | Show entry counts for all in-memory caches (seeker, ping, service, charts, dashboard) |
| `cache:detail` | query | `name` (required) | Dump contents of a specific cache (first 50 entries). Available: `seeker_detail_cache`, `service_status_cache`, `ping_snapshot_by_node`, `dn_ping_snapshots`, `charts_last_le` |
| `db:pool` | query | — | Show async SQLAlchemy connection pool stats (size, checked in/out, overflow) |
| `redis:status` | query | — | Show Redis connection status, version, uptime, memory usage, connected clients |
| `system:info` | query | — | Show Python version, platform, hostname, PID, app uptime |
| `node:detail` | query | `node_id` (required) | Show cached Seeker API detail for a specific anchor node |
| `ping:detail` | query | `node_id` (required) | Show ping state for a node: snapshot, recent samples, consecutive misses |

## Adding New Codes

1. Add an async handler function in `app/diag.py`:
   ```python
   async def _diag_my_code(args: dict, ps: PollerState) -> dict:
       """One-line description shown in help output."""
       return {"key": "value"}
   ```

2. Register it in the `DIAG_HANDLERS` dict:
   ```python
   DIAG_HANDLERS = {
       ...
       "my:code": _diag_my_code,
   }
   ```

3. Update this catalog with the new code, type, args, and description.

## Code Types

- **query**: Read-only diagnostic information. Safe to run at any time.
- **control**: Mutates runtime state (e.g., flush cache, change log level). Use with care.

All current codes are query type. Control codes will be clearly marked when added.
