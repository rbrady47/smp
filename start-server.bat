@echo off
title SMP Dev Server
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: No virtual environment found. Run scripts\bootstrap.ps1 first.
    pause
    exit /b 1
)

set DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/smp

echo Starting SMP on http://127.0.0.1:8000 ...
echo Press Ctrl+C to stop.
echo.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
pause
