# Copyright (c) Microsoft Corporation. All rights reserved.
[CmdletBinding()]
param([switch]$WebUI, [switch]$CheckRuntime)

$ErrorActionPreference = 'Stop'
$factoryRoot = $PSScriptRoot
$cli = Join-Path $factoryRoot 'cli.ts'
$web = Join-Path $factoryRoot 'web'
$version = & node --version
if ($LASTEXITCODE -ne 0 -or [int](($version -replace '^v', '') -split '\.')[0] -lt 24) {
    throw 'Software Factory requires Node.js 24+. Install it before running setup.'
}
$manifest = Get-Content -LiteralPath (Join-Path $factoryRoot 'package.json') -Raw | ConvertFrom-Json
if ($WebUI -and -not (Test-Path -LiteralPath (Join-Path $web 'package.json'))) {
    throw 'Optional Software Factory Web UI package (web/package.json) is not present in this checkout.'
}
if ($CheckRuntime -and (-not $manifest.scripts -or -not $manifest.scripts.PSObject.Properties['check:runtime'])) {
    throw 'The check:runtime script is not present in this checkout.'
}

# npm.ps1 re-evaluates its invocation; pass an ordinary variable, not $PSScriptRoot.
& npm --prefix $factoryRoot ci --include=optional --include=dev --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Software Factory core dependency installation failed.' }
if (Test-Path -LiteralPath $cli) {
    & node $cli doctor --require-runtime
} else {
    & node --input-type=module -e '
        import { pathToFileURL } from "node:url";
        const { FactoryError } = await import(pathToFileURL(process.argv[1]).href);
        const { requireRuntime } = await import(pathToFileURL(process.argv[2]).href);
        const { runtimeReport } = await import(pathToFileURL(process.argv[3]).href);
        try {
            requireRuntime();
            console.log(JSON.stringify(runtimeReport(), null, 2));
        } catch (error) {
            console.error(error instanceof FactoryError ? `${error.code}: ${error.message}` : error);
            process.exitCode = 1;
        }
    ' (Join-Path $factoryRoot 'core\contracts.ts') (Join-Path $factoryRoot 'core\runtime-health.ts') (Join-Path $factoryRoot 'core\doctor.ts')
}
if ($LASTEXITCODE -ne 0) { throw 'The Copilot SDK platform runtime is missing or incomplete. Setup is not complete; see the repair command above.' }
& npm --prefix $factoryRoot run check
if ($LASTEXITCODE -ne 0) { throw 'Software Factory type check failed.' }

if ($WebUI) {
    & npm --prefix $web ci --include=optional --include=dev --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Optional UI dependency installation failed.' }
    & npm --prefix $web run build
    if ($LASTEXITCODE -ne 0) { throw 'Optional UI build failed.' }
}

if ($CheckRuntime) {
    & npm --prefix $factoryRoot run check:runtime
    if ($LASTEXITCODE -ne 0) { throw 'Native workflow registration/recovery check failed. No paid agents were launched.' }
}

Write-Host 'Software Factory core is installed. Start a new trusted Copilot app/CLI session in this repository.'
if ($WebUI -and (Test-Path -LiteralPath $cli)) { Write-Host "Optional UI: node `"$cli`" serve" }
Write-Host 'No paid workflow, work item creation, or coding-agent dispatch was started.'
