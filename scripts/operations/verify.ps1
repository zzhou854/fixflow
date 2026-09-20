[CmdletBinding()]
param(
    [string]$ApiHealthUrl = "http://127.0.0.1:8000/health",
    [string]$FrontendHealthUrl = "http://127.0.0.1:5173/health"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

Push-Location $repoRoot
try {
    docker compose ps
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose ps failed."
    }

    $api = Invoke-WebRequest -UseBasicParsing -Uri $ApiHealthUrl -TimeoutSec 5
    $frontend = Invoke-WebRequest -UseBasicParsing -Uri $FrontendHealthUrl -TimeoutSec 5
    if ($api.StatusCode -ne 200 -or $frontend.StatusCode -ne 200) {
        throw "A health endpoint did not return HTTP 200."
    }

    if (-not (Test-Path -LiteralPath $python)) {
        throw "Project virtual environment is missing. Run uv sync --frozen."
    }

    & $python -m alembic current
    if ($LASTEXITCODE -ne 0) {
        throw "alembic current failed."
    }
    & $python -m alembic check
    if ($LASTEXITCODE -ne 0) {
        throw "alembic check failed."
    }

    Write-Host "FixFlow operational verification passed."
}
finally {
    Pop-Location
}
