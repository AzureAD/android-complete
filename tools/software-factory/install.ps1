# Copyright (c) Microsoft Corporation. All rights reserved.
[CmdletBinding()]
param([switch]$WebUI, [switch]$CheckRuntime)

$ErrorActionPreference = 'Stop'
$factoryRoot = $PSScriptRoot
$version = & node --version
if ($LASTEXITCODE -ne 0 -or [int](($version -replace '^v', '') -split '\.')[0] -lt 24) {
    throw 'Software Factory requires Node.js 24+. Install it before running setup.'
}

# npm.ps1 re-evaluates its invocation; pass an ordinary variable, not $PSScriptRoot.
& npm --prefix $factoryRoot ci --include=optional --include=dev --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Software Factory core dependency installation failed.' }
& node (Join-Path $factoryRoot 'cli.ts') doctor --require-runtime
if ($LASTEXITCODE -ne 0) { throw 'The Copilot SDK platform runtime is missing or incomplete. Setup is not complete; see the repair command above.' }
& npm --prefix $factoryRoot run check
if ($LASTEXITCODE -ne 0) { throw 'Software Factory type check failed.' }

if ($WebUI) {
    $web = Join-Path $factoryRoot 'web'
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
if ($WebUI) { Write-Host "Optional UI: node `"$factoryRoot\cli.ts`" serve" }
Write-Host 'No paid workflow, work item creation, or coding-agent dispatch was started.'
