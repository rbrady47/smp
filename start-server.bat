@echo off
title SMP Dev Server
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: No virtual environment found. Run scripts\bootstrap.ps1 first.
    pause
    exit /b 1
)

if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
    )
)

if "%DATABASE_URL%"=="" (
    echo ERROR: DATABASE_URL is not set.
    echo Create a .env file with: DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/smp
    pause
    exit /b 1
)

echo Starting SMP on http://127.0.0.1:8000 ...
echo Press Ctrl+C to stop.
echo.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
pause
