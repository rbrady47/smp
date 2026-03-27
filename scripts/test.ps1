param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "No local virtual environment found. Run .\scripts\bootstrap.ps1 first."
}

Push-Location $repoRoot
try {
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$repoRoot;$env:PYTHONPATH" } else { $repoRoot }
    & $venvPython -m unittest discover -s tests -t . -p "test_*.py"
}
finally {
    Pop-Location
}
