[CmdletBinding()]
param(
    [string]$DestinationRoot
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $DestinationRoot) {
    $DestinationRoot = Join-Path (Split-Path -Parent $repoRoot) "fixflow-backups"
}
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$destination = Join-Path $DestinationRoot $timestamp
$dumpName = "fixflow-$timestamp.dump"
$remotePath = "/tmp/$dumpName"
$dumpPath = Join-Path $destination $dumpName

New-Item -ItemType Directory -Path $destination -Force | Out-Null
Push-Location $repoRoot
try {
    $containerId = (docker compose ps -q postgres).Trim()
    if (-not $containerId) {
        throw "PostgreSQL container is not running. Start it before taking a backup."
    }

    $dumpCommand = 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file="' + $remotePath + '"'
    docker compose exec -T postgres sh -c $dumpCommand
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump failed with exit code $LASTEXITCODE."
    }

    docker cp "${containerId}:${remotePath}" $dumpPath
    if ($LASTEXITCODE -ne 0) {
        throw "docker cp failed with exit code $LASTEXITCODE."
    }

    $dump = Get-Item -LiteralPath $dumpPath
    if ($dump.Length -le 0) {
        throw "Backup file is empty."
    }

    $hash = Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256
    $hashLine = "{0}  {1}" -f $hash.Hash.ToLowerInvariant(), $dump.Name
    Set-Content -LiteralPath "$dumpPath.sha256" -Value $hashLine -Encoding ascii

    Write-Host "Backup created: $dumpPath"
    Write-Host "SHA-256 manifest: $dumpPath.sha256"
}
finally {
    if ($containerId) {
        docker compose exec -T postgres rm -f $remotePath | Out-Null
    }
    Pop-Location
}
