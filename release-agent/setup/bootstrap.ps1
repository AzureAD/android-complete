<#
.SYNOPSIS
  One-time setup for the Release Orchestrator (/release-agent) on this machine.
  Small by design: it only prepares Scout + the environment so the real work
  can run inside Scout. It does NOT run a release.

.DESCRIPTION
  Steps:
    1. Infrastructure preflight — check CLIs/host deps AND register + verify the
       MCP servers the skill needs inside Scout (from config/requirements.yaml).
    2. Install the /release-agent skill into the Scout skills folder.
    3. Print next steps.

.EXAMPLE
  pwsh ./setup/bootstrap.ps1
#>
[CmdletBinding()]
param(
  [string]$ScoutSkillsDir = "$env:USERPROFILE\.scout\m-skills",
  [switch]$SkipSkillInstall
)

$ErrorActionPreference = "Stop"
$AgentRoot  = Split-Path -Parent $PSScriptRoot                          # release-agent/
$RepoRoot   = Split-Path -Parent $AgentRoot                            # android-complete/
$ReqFile    = Join-Path $AgentRoot "config\requirements.yaml"

Write-Host "Release Orchestrator bootstrap`n" -ForegroundColor Cyan

# ---- 1. Infrastructure preflight (CLIs + MCP servers), data-driven ----
# Delegates to the engine (python -m orchestrator.cli infra), which reads
# config/requirements.yaml, checks every CLI/host dependency, and REGISTERS any
# missing MCP servers into Scout's config (backing it up first). One home for the
# logic; bootstrap just needs python+pyyaml to call it.
Write-Host "1. Infrastructure preflight (from config/requirements.yaml)"
if (-not (Test-Path $ReqFile)) { Write-Host "  requirements.yaml not found at $ReqFile" -ForegroundColor Red; exit 1 }

$haveInfra = $false
try { python -c "import yaml" 2>$null; if ($LASTEXITCODE -eq 0) { $haveInfra = $true } } catch { $haveInfra = $false }

$ok = $true
$restartNeeded = $false
if (-not $haveInfra) {
  Write-Host "  [ ] Python + PyYAML ... MISSING (needed to run the preflight)" -ForegroundColor Yellow
  Write-Host "      install: Python 3.9+ then  python -m pip install pyyaml" -ForegroundColor DarkYellow
  $ok = $false
} else {
  Push-Location $AgentRoot
  try {
    $out = python -m orchestrator.cli infra 2>&1
    $out | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) { $ok = $false }
    if ($out -match "RESTART Scout") { $restartNeeded = $true }
  } finally { Pop-Location }

  # engine config presence (cheap local sanity check)
  Write-Host -NoNewline "  [ ] engine config (phases.yaml) ... "
  if (Test-Path (Join-Path $AgentRoot 'config\phases.yaml')) { Write-Host "OK" -ForegroundColor Green }
  else { Write-Host "MISSING" -ForegroundColor Yellow; $ok = $false }
}

if (-not $ok) {
  Write-Host "`nSome infrastructure is missing — resolve the items above, then re-run." -ForegroundColor Yellow
}
if ($restartNeeded) {
  Write-Host "`n>>> RESTART Scout now so the newly-registered MCP server(s) load. <<<" -ForegroundColor Cyan
}

Write-Host "`n2. Skill install"
if ($SkipSkillInstall) {
  Write-Host "  Skipped (--SkipSkillInstall)."
} else {
  $src = Join-Path $AgentRoot "skill\SKILL.md"
  $destDir = Join-Path $ScoutSkillsDir "release-agent"
  New-Item -ItemType Directory -Force -Path $destDir | Out-Null
  Copy-Item $src (Join-Path $destDir "SKILL.md") -Force
  Write-Host "  Installed /release-agent skill -> $destDir" -ForegroundColor Green
}

Write-Host "`n3. Next steps" -ForegroundColor Cyan
Write-Host "  * Open Scout and run:  /release-agent"
Write-Host "  * Or drive the engine directly from $AgentRoot :"
Write-Host "      python -m orchestrator.cli init   --release 2026-07"
Write-Host "      python -m orchestrator.cli next   --release 2026-07"
Write-Host "      python -m orchestrator.cli status --release 2026-07"
Write-Host "`n  Everything runs in DRY-RUN by default. Nothing touches production.`n"
