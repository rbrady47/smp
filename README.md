# Seeker Management Platform (SMP)

Centralized dashboard, monitoring, and topology visualization for Seeker SDN nodes.

## Status
Prototype – Milestone 1 (Platform Shell)

## Goals
- Central dashboard for Seeker nodes
- Web UI and SSH access launcher
- Health monitoring (HTTPS + SSH)
- Inventory and tagging
- Role-based access
- Backend using FastAPI + PostgreSQL
- Frontend dashboard (HTML/JS initially)
- Future Seeker API integration for telemetry and topology views

## Milestone Plan
- Milestone 1: Platform shell
- Milestone 2: Live backend status
- Milestone 3: Inventory
- Milestone 4: Health monitoring
- Milestone 5: Auth + Seeker API
- Milestone 6: Topology + polish

## Local Setup
- Install a full CPython 3.11+ distribution.
- From PowerShell, run `.\scripts\bootstrap.ps1`
- If Python is not on PATH, set `SMP_PYTHON` to the full `python.exe` path first, for example:
  `$env:SMP_PYTHON="C:\Path\To\Python\python.exe"`
- The bootstrap script creates `.venv`, upgrades `pip`, and installs `requirements.txt`.

## Testing
- Run `.\scripts\test.ps1`
- The test helper expects `.venv` to already exist and will tell you to bootstrap first if it does not.

## Running SMP
- Set `DATABASE_URL` in your shell. `.env.example` shows the expected format.
- Start the app with `.\scripts\dev.ps1 -Reload`
