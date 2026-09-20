[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("test", "lint", "typecheck", "build", "dev")]
    [string]$Task,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TaskArguments
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$frontendRoot = Join-Path $repoRoot "frontend"
$bundledNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"

if (Test-Path -LiteralPath $bundledNode) {
    $node = $bundledNode
}
else {
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if ($null -eq $nodeCommand) {
        throw "Node.js 20 or newer is required."
    }
    $node = $nodeCommand.Source
}

$versionText = (& $node --version).TrimStart("v")
$majorVersion = [int]($versionText.Split(".")[0])
if ($majorVersion -lt 20) {
    throw "Node.js 20 or newer is required; found $versionText at $node."
}

$commands = @{
    test = @("node_modules\vitest\vitest.mjs", "run")
    lint = @("node_modules\eslint\bin\eslint.js", ".")
    typecheck = @("node_modules\typescript\bin\tsc", "--noEmit")
    dev = @("node_modules\vite\bin\vite.js")
}

Push-Location $frontendRoot
try {
    if ($Task -eq "build") {
        & $node "node_modules\typescript\bin\tsc" "-b"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $node "node_modules\vite\bin\vite.js" "build" @TaskArguments
    }
    else {
        & $node @($commands[$Task]) @TaskArguments
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
