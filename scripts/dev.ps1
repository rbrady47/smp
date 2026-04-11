param(
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "No local virtual environment found. Run .\scripts\bootstrap.ps1 first."
}

if (-not $env:DATABASE_URL) {
    throw "DATABASE_URL is not set. Copy .env.example into your shell environment before starting SMP."
}

Push-Location $repoRoot
try {
    $args = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000")
    if ($Reload) {
        $args += "--reload"
    }
    & $venvPython @args
}
finally {
    Pop-Location
}
