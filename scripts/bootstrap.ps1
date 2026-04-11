param(
    [string]$Python = $env:SMP_PYTHON
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvDir = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$commonCandidates = @(
    "C:\Program Files\Python313\python.exe",
    "C:\Program Files\Python312\python.exe",
    "C:\Program Files\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "C:\Program Files\PostgreSQL\18\pgAdmin 4\python\python.exe"
)

function Resolve-PythonInterpreter {
    param(
        [string]$RequestedPython
    )

    if ($RequestedPython -and (Test-Path $RequestedPython)) {
        return (Resolve-Path $RequestedPython).Path
    }

    foreach ($candidate in @("python", "py", "python3")) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($command) {
            if ($command.Source) {
                return $command.Source
            }
            if ($command.Path) {
                return $command.Path
            }
            if ($command.Name) {
                return $command.Name
            }
        }
    }

    foreach ($candidate in $commonCandidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return (Resolve-Path $candidate).Path
        }
    }

    return $null
}

function Test-PythonVersion {
    param(
        [string]$Interpreter
    )

    $stdoutPath = Join-Path $env:TEMP "smp-python-version.txt"
    $stderrPath = Join-Path $env:TEMP "smp-python-version.err.txt"
    if (Test-Path $stdoutPath) { Remove-Item $stdoutPath -Force }
    if (Test-Path $stderrPath) { Remove-Item $stderrPath -Force }

    $process = Start-Process -FilePath $Interpreter -ArgumentList "--version" -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    if ($process.ExitCode -ne 0) {
        return $false
    }

    $versionText = ""
    if (Test-Path $stdoutPath) {
        $versionText = (Get-Content $stdoutPath -Raw).Trim()
    }
    if (-not $versionText -and (Test-Path $stderrPath)) {
        $versionText = (Get-Content $stderrPath -Raw).Trim()
    }
    if ($versionText -match 'Python\s+(\d+)\.(\d+)') {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        return ($major -gt 3) -or ($major -eq 3 -and $minor -ge 11)
    }

    return $false
}

function Test-PythonSupportsVenv {
    param(
        [string]$Interpreter
    )

    $stdoutPath = Join-Path $env:TEMP "smp-python-venv.txt"
    $stderrPath = Join-Path $env:TEMP "smp-python-venv.err.txt"
    if (Test-Path $stdoutPath) { Remove-Item $stdoutPath -Force }
    if (Test-Path $stderrPath) { Remove-Item $stderrPath -Force }

    $process = Start-Process -FilePath $Interpreter -ArgumentList "-m", "venv", "--help" -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    return $process.ExitCode -eq 0
}

$Python = Resolve-PythonInterpreter -RequestedPython $Python

if (-not $Python) {
    throw "Python was not found. Install CPython 3.11+ and rerun this script, or set SMP_PYTHON to the full path of python.exe."
}

if (-not (Test-PythonVersion -Interpreter $Python)) {
    throw "The selected Python is too old. Use CPython 3.11+."
}

if (-not (Test-PythonSupportsVenv -Interpreter $Python)) {
    throw "The selected Python does not provide the 'venv' module. Install a full CPython 3.11+ distribution and rerun bootstrap. The pgAdmin-bundled Python is not sufficient for SMP development."
}

Push-Location $repoRoot
try {
    & $Python -m venv .venv
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r requirements.txt

    Write-Host ""
    Write-Host "Bootstrap complete."
    Write-Host "Activate with: .\.venv\Scripts\Activate.ps1"
    Write-Host "Run tests with: .\scripts\test.ps1"
    Write-Host "Run the app with: .\scripts\dev.ps1"
}
finally {
    Pop-Location
}
