# Start PeopleIntel.
#
#   .\run.ps1              start on the first free port from 8000
#   .\run.ps1 -Port 8080   start on a port you pick
#   .\run.ps1 -NoReload    don't restart on file changes
#
# Run it from anywhere — it moves to its own folder first. That is the point of
# it: uvicorn imports the app as "app.main", which only resolves when the
# working directory is this one, and the most common way to fail to start this
# project is to run the command from the folder above.
#
# It also calls the virtualenv's python by full path rather than relying on
# Activate.ps1, so there is no PATH to get wrong and no execution policy to
# trip over.

param(
    [int]$Port = 0,
    [switch]$NoReload
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# ---- the interpreter ------------------------------------------------------
$venvPython = Join-Path $PSScriptRoot 'venvbd\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host "No virtualenv found at venvbd\." -ForegroundColor Yellow
    Write-Host "Create it and install the dependencies with:" -ForegroundColor Yellow
    Write-Host "    python -m venv venvbd"
    Write-Host "    .\venvbd\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

# ---- a port that is actually free -----------------------------------------
function Test-PortFree([int]$p) {
    -not (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
}

if ($Port -eq 0) {
    $Port = 8000
    while (-not (Test-PortFree $Port) -and $Port -lt 8020) {
        Write-Host "Port $Port is busy, trying $($Port + 1)..." -ForegroundColor DarkGray
        $Port++
    }
} elseif (-not (Test-PortFree $Port)) {
    Write-Host "Port $Port is already in use. Pick another with -Port, or stop what is on it." -ForegroundColor Red
    exit 1
}

# ---- go --------------------------------------------------------------------
$argsList = @('-m', 'uvicorn', 'app.main:app', '--port', $Port)
if (-not $NoReload) { $argsList += '--reload' }

Write-Host ""
Write-Host "  PeopleIntel is starting on http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

& $venvPython @argsList
