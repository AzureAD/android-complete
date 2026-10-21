<#
.SYNOPSIS
  One-time setup for the Release Orchestrator (/release-agent) on this machine.
  Small by design: it only prepares Scout + the environment so the real work
  can run inside Scout. It does NOT run a release.

.DESCRIPTION
  Steps:
    1. Infrastructure preflight — check CLIs/host deps AND register + verify the
       MCP servers the skill needs inside Scout (from config/requirements.yaml).
       If Python 3.9+ is not discovered, ask for its actual path. -InstallPython
       explicitly opts into winget installation (per-user, no admin);
       -NoAutoInstallPython prevents it. PyYAML is installed via pip if missing.
    2. Install the /release-agent skill into the Scout skills folder — only if
       Microsoft Scout is detected (~/.scout present); the copy is then verified.
    3. Print next steps. By default the script closes and relaunches Scout so the
       new skill / MCP servers load (skipped automatically when this setup is run
       from inside a Scout session, to avoid killing itself). Pass -NoRestartScout
       to opt out and restart Scout yourself.

  HOW TO RUN
    Run this from the `release-agent` folder of your android-complete clone,
    using PowerShell 7 (pwsh):

      cd <path-to>\android-complete\release-agent
      pwsh .\setup\bootstrap.ps1

    You can launch it from any working directory as long as you give the full
    path to the script. The script locates its own clone from $PSScriptRoot.

.EXAMPLE
  cd <your-clone>\release-agent
  pwsh .\setup\bootstrap.ps1
  M365 CONNECTORS
    Setup cannot finish from MCP registrations alone. Configure/sign in to the
    hosted WorkIQ connector in Scout and supply a fresh loaded-tool inventory
    with -M365ToolsFile. Release-specific read probes and write attestations
    remain separate; bootstrap never performs test sends or uploads.
#>
[CmdletBinding()]
param(
  [string]$ScoutSkillsDir,
  [string]$ScoutRoot,
  [string]$AgencyPath,
  [string]$PythonPath,
  [string]$ScoutExe,
  [switch]$NonInteractive,
  [switch]$InstallPython,
  [switch]$SkipSkillInstall,
  [string]$M365ToolsFile,
  [switch]$NoAutoInstallPython,  # compatibility veto for -InstallPython
  [switch]$NoRestartScout        # by default we restart Scout so new skill/MCP servers load
)

$ErrorActionPreference = "Stop"
if ($M365ToolsFile) {
  $M365ToolsFile = (Resolve-Path -LiteralPath $M365ToolsFile).Path
}

# The Python engine prints UTF-8 (em-dashes, etc.). Make sure this console reads/writes
# UTF-8 so echoed output doesn't turn into mojibake like "ΓÇö".
try {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $OutputEncoding = [System.Text.Encoding]::UTF8
  $env:PYTHONIOENCODING = "utf-8"
  $env:PYTHONUTF8 = "1"
} catch {}

$AgentRoot  = Split-Path -Parent $PSScriptRoot                          # release-agent/
$RepoRoot   = Split-Path -Parent $AgentRoot                            # android-complete/
$ReqFile    = Join-Path $AgentRoot "config\requirements.yaml"

Write-Host "Release Orchestrator bootstrap`n" -ForegroundColor Cyan
Write-Host "  Running from: $AgentRoot" -ForegroundColor DarkGray
Write-Host "  (run this from the 'release-agent' folder of your android-complete clone)`n" -ForegroundColor DarkGray

# Sanity check: make sure we're actually in the release-agent folder.
if (-not (Test-Path (Join-Path $AgentRoot 'setup\bootstrap.ps1'))) {
  Write-Host "  This does not look like the release-agent folder." -ForegroundColor Red
  Write-Host "  cd into <your-clone>\android-complete\release-agent and run:  pwsh .\setup\bootstrap.ps1`n" -ForegroundColor Red
  exit 1
}

# True when THIS script was launched from a shell inside a Scout session — Scout sets
# these env vars for its child processes. Restarting Scout from there would kill us.
$InsideScout = [bool]($env:COPILOT_AGENT_SESSION_ID -or $env:COPILOT_CLI)
$CanPromptForPaths = -not ($InsideScout -or $NonInteractive -or [Console]::IsInputRedirected)

function Resolve-SetupPath {
  param([string]$Value, [string[]]$Candidates, [string]$Label,
        [string]$Parameter, [ValidateSet('Leaf', 'Container')][string]$Kind = 'Leaf')
  $choices = if ($Value) { @($Value) } else { @($Candidates) }
  foreach ($candidate in $choices) {
    if ($candidate) {
      $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
      if (Test-Path -LiteralPath $expanded -PathType $Kind) {
        return (Resolve-Path -LiteralPath $expanded).Path
      }
    }
  }
  Write-Host "  [PATH REQUIRED] $Label was not found. Supply -$Parameter with its actual location." -ForegroundColor Yellow
  if ($CanPromptForPaths) {
    $entered = Read-Host "  Path to $Label (leave blank to keep setup incomplete)"
    if ($entered -and (Test-Path -LiteralPath $entered -PathType $Kind)) {
      return (Resolve-Path -LiteralPath $entered).Path
    }
    Write-Host "  No valid $Label path supplied; setup remains incomplete." -ForegroundColor Yellow
  } else {
    Write-Host "  In Scout, ask the user for this path and rerun; never guess or wait on a console prompt." -ForegroundColor Yellow
  }
  return $null
}

if (-not $ScoutRoot) {
  $ScoutRoot = if ($env:RELEASE_AGENT_SCOUT_ROOT) { $env:RELEASE_AGENT_SCOUT_ROOT }
               elseif ($ScoutSkillsDir) { Split-Path -Parent $ScoutSkillsDir }
               else { Join-Path $env:USERPROFILE '.scout' }
}
$ScoutRoot = Resolve-SetupPath -Value $ScoutRoot -Label 'existing Scout profile directory' -Parameter ScoutRoot -Kind Container
if (-not $ScoutRoot) { exit 1 }
if (-not $ScoutSkillsDir) { $ScoutSkillsDir = Join-Path $ScoutRoot 'm-skills' }
$ScoutSkillsDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ScoutSkillsDir)
$env:RELEASE_AGENT_SCOUT_ROOT = $ScoutRoot

$agencyCandidates = @()
foreach ($name in @('agency', 'agency.exe')) {
  $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($command) { $agencyCandidates += $command.Source }
}
$mcpConfig = Join-Path $ScoutRoot 'm-mcp-servers.json'
if (Test-Path -LiteralPath $mcpConfig -PathType Leaf) {
  $servers = (Get-Content -LiteralPath $mcpConfig -Raw | ConvertFrom-Json).servers
  foreach ($entry in $servers.PSObject.Properties.Value) {
    $command = $entry.config.command
    if ($command -and ([IO.Path]::GetFileName($command) -match '^agency(\.exe)?$')) {
      $agencyCandidates += $command
    }
  }
}
if ($env:APPDATA) { $agencyCandidates += Join-Path $env:APPDATA 'agency\CurrentVersion\agency.exe' }
if (-not $AgencyPath) { $AgencyPath = $env:RELEASE_AGENT_AGENCY_PATH }
$AgencyPath = Resolve-SetupPath -Value $AgencyPath -Candidates $agencyCandidates -Label 'Agency CLI executable' -Parameter AgencyPath
if (-not $AgencyPath) { exit 1 }

# Stop all Scout processes and relaunch the app. Returns $true if it relaunched.
function Restart-Scout {
  $procs = @(Get-Process -Name 'scout' -ErrorAction SilentlyContinue)
  $candidates = @($procs | Where-Object { $_.Path } | Select-Object -ExpandProperty Path)
  foreach ($name in @('scout', 'scout.exe')) {
    $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { $candidates += $command.Source }
  }
  if ($env:LOCALAPPDATA) { $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Microsoft Scout\scout.exe' }
  if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles 'Microsoft Scout\scout.exe' }
  $exe = Resolve-SetupPath -Value $ScoutExe -Candidates $candidates -Label 'Scout executable' -Parameter ScoutExe
  if (-not $exe) { return $false }
  if ($procs.Count) {
    Write-Host "  Closing Scout ($($procs.Count) process(es)) ..." -ForegroundColor DarkYellow
    $procs | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {} }
    # Wait for the processes to actually exit before relaunching (single-instance lock).
    for ($i = 0; $i -lt 20 -and (Get-Process -Name 'scout' -ErrorAction SilentlyContinue); $i++) {
      Start-Sleep -Milliseconds 250
    }
  }
  Write-Host "  Relaunching Scout ..." -ForegroundColor DarkYellow
  # Launch fully DETACHED so Scout does NOT inherit this console — otherwise its
  # Electron startup logs spill into the terminal after setup returns. Win32_Process
  # Create starts it in a brand-new process with no console attachment.
  $launched = $false
  try {
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "`"$exe`"" } -ErrorAction Stop
    if ($r.ReturnValue -eq 0) { $launched = $true }
  } catch {}
  if (-not $launched) {
    # Fallback: explorer.exe launches the app as its child, also detached from our console.
    try { Start-Process explorer.exe -ArgumentList "`"$exe`""; $launched = $true } catch {}
  }
  return $launched
}

# ---- 1. Infrastructure preflight (CLIs + MCP servers), data-driven ----
# Delegates to the engine (python -m orchestrator.cli infra), which reads
# config/requirements.yaml, checks every CLI/host dependency, and REGISTERS any
# missing MCP servers into Scout's config (backing it up first). One home for the
# logic; bootstrap just needs python+pyyaml to call it.
Write-Host "1. Infrastructure preflight (from config/requirements.yaml)"
if (-not (Test-Path $ReqFile)) { Write-Host "  requirements.yaml not found at $ReqFile" -ForegroundColor Red; exit 1 }

# Resolve a working Python (3.9+). Prefer `python`/`python3`, fall back to `py -3`,
# then probe well-known winget/python.org install dirs (PATH may be stale in this session).
function Resolve-Python {
  if ($PythonPath) {
    $candidate = Resolve-SetupPath -Value $PythonPath -Label 'Python executable' -Parameter PythonPath
    if (-not $candidate) { return $null }
    & $candidate -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)"
    if ($LASTEXITCODE -ne 0) { throw "The selected Python executable must support Python 3.9 or newer." }
    return "`"$candidate`""
  }
  foreach ($cand in @('python','python3')) {
    try { & $cand -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)" 2>$null; if ($LASTEXITCODE -eq 0) { return $cand } } catch {}
  }
  try { & py -3 -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)" 2>$null; if ($LASTEXITCODE -eq 0) { return 'py -3' } } catch {}
  # Direct probe of standard per-user install locations (newest first)
  $globs = @(
    "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
    "$env:ProgramFiles\Python3*\python.exe"
  )
  foreach ($g in $globs) {
    $hit = Get-ChildItem $g -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
    if ($hit) {
      try { & $hit.FullName -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)" 2>$null; if ($LASTEXITCODE -eq 0) { return "`"$($hit.FullName)`"" } } catch {}
    }
  }
  return $null
}

$PyExe = Resolve-Python
if (-not $PyExe -and -not $PythonPath -and (-not $InstallPython -or $NoAutoInstallPython)) {
  $PythonPath = Resolve-SetupPath -Label 'Python 3.9+ executable' -Parameter PythonPath
  if ($PythonPath) { $PyExe = Resolve-Python }
}

# Explicit opt-in avoids installing a second copy when a custom Python was not discovered.
if (-not $PyExe -and $InstallPython -and -not $NoAutoInstallPython -and -not $PythonPath) {
  $winget = (Get-Command winget -ErrorAction SilentlyContinue)
  if ($winget) {
    Write-Host "  [ ] Python 3.9+ not found - installing Python 3.12 via winget (no admin needed) ..." -ForegroundColor DarkYellow
    winget install --id Python.Python.3.12 -e --source winget --scope user `
      --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 |
      ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    # Refresh PATH from the registry so the just-installed python is visible this session.
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path','User')
    $PyExe = Resolve-Python
    if ($PyExe) { Write-Host "  [x] Python installed" -ForegroundColor Green }
    else { Write-Host "  [ ] Python installed but not detected yet - close and reopen your terminal, then re-run setup." -ForegroundColor Yellow }
  } else {
    Write-Host "  [ ] Python 3.9+ missing and winget is unavailable for auto-install." -ForegroundColor Yellow
  }
}

$haveInfra = $false
if ($PyExe) {
  # Python is present. Ensure PyYAML — install it silently via pip, no user action needed.
  & cmd /c "$PyExe -c ""import yaml"" 2>nul"
  if ($LASTEXITCODE -eq 0) {
    $haveInfra = $true
  } else {
    Write-Host "  [ ] PyYAML missing - installing via pip ..." -ForegroundColor DarkYellow
    & cmd /c "$PyExe -m pip install --quiet --disable-pip-version-check pyyaml"
    & cmd /c "$PyExe -c ""import yaml"" 2>nul"
    if ($LASTEXITCODE -eq 0) { $haveInfra = $true; Write-Host "  [x] PyYAML installed" -ForegroundColor Green }
  }
}

$ok = $true
$restartNeeded = $false
if (-not $haveInfra) {
  if (-not $PyExe) {
    Write-Host "  [ ] Python 3.9+ ... still MISSING (needed to run the preflight)" -ForegroundColor Yellow
    if ($NoAutoInstallPython) {
      Write-Host "      Auto-install was disabled (-NoAutoInstallPython)." -ForegroundColor DarkYellow
    }
    Write-Host "      Install Python 3.9+ ('winget install Python.Python.3.12' or https://aka.ms/python)," -ForegroundColor DarkYellow
    Write-Host "      reopen your terminal, then re-run setup." -ForegroundColor DarkYellow
  } else {
    Write-Host "  [ ] PyYAML ... could not be installed automatically" -ForegroundColor Yellow
  }
  $ok = $false
} else {
  Push-Location $AgentRoot
  try {
    $infraArgs = @("-m", "orchestrator.cli", "infra", "--scout-root", $ScoutRoot, "--agency-path", $AgencyPath)
    if ($M365ToolsFile) {
      $infraArgs += @("--tools-file", $M365ToolsFile)
    }
    if ($PyExe -eq 'py -3') {
      $out = & py -3 @infraArgs 2>&1
    } else {
      $out = & ($PyExe.Trim('"')) @infraArgs 2>&1
    }
    $out | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) { $ok = $false }
    if ($out -match "RESTART Scout") { $restartNeeded = $true }
  } finally { Pop-Location }

  # engine config presence (cheap local sanity check)
  if (Test-Path (Join-Path $AgentRoot 'config\phases.yaml')) {
    Write-Host "  [OK] engine config (phases.yaml)" -ForegroundColor Green
  } else {
    Write-Host "  [MISSING] engine config (phases.yaml)" -ForegroundColor Yellow; $ok = $false
  }
}

if (-not $ok) {
  Write-Host "`nSome infrastructure is missing — resolve the items above, then re-run." -ForegroundColor Yellow
}

Write-Host "`n2. Skill install"
if ($SkipSkillInstall) {
  Write-Host "  Skipped (--SkipSkillInstall)."
} else {
  $srcDir    = Join-Path $AgentRoot "skill"
  $src       = Join-Path $srcDir "SKILL.md"
  $destDir   = Join-Path $ScoutSkillsDir "release-agent"
  $destFile  = Join-Path $destDir "SKILL.md"

  if (-not (Test-Path $src)) {
    Write-Host "  [ ] Source skill not found at $src" -ForegroundColor Red
    $ok = $false
  }
  # Is Scout actually installed? Its per-user data folder (~/.scout) is created on
  # first launch. If it's absent, Scout isn't installed/run yet — don't fabricate it.
  elseif (-not (Test-Path $ScoutRoot)) {
    Write-Host "  [ ] Microsoft Scout not detected ($ScoutRoot is missing)." -ForegroundColor Yellow
    Write-Host "      Install Microsoft Scout and launch it once, then re-run this setup" -ForegroundColor DarkYellow
    Write-Host "      to install the /release-agent skill." -ForegroundColor DarkYellow
    $ok = $false
  }
  else {
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    foreach ($file in Get-ChildItem -LiteralPath $srcDir -Recurse -File) {
      $relative = [IO.Path]::GetRelativePath($srcDir, $file.FullName)
      $target = Join-Path $destDir $relative
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
      Copy-Item -LiteralPath $file.FullName -Destination $target -Force
      if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath $target).Hash) {
        throw "Installed skill resource differs from its source: $relative"
      }
    }
    if ((Get-Item -LiteralPath $destFile).Length -eq 0) { throw "Installed skill instructions are empty." }
    Write-Host "  [x] Installed /release-agent and reference resources -> $destDir" -ForegroundColor Green
    $skillInstalled = $true
  }

  # Report every release-agent skill Scout can currently see.
  if (Test-Path $ScoutSkillsDir) {
    $installed = Get-ChildItem $ScoutSkillsDir -Directory -ErrorAction SilentlyContinue |
                 Where-Object { Test-Path (Join-Path $_.FullName 'SKILL.md') } |
                 Select-Object -ExpandProperty Name
    if ($installed) { Write-Host "  Skills currently installed in Scout: $($installed -join ', ')" -ForegroundColor DarkGray }
  }
}

$needsReload = ($restartNeeded -or $skillInstalled)
if ($needsReload) {
  Write-Host "`nScout must reload to pick up the newly-installed skill / MCP server(s)."
  if ($NoRestartScout) {
    Write-Host "  Auto-restart disabled (-NoRestartScout). Restart Scout yourself so the changes load." -ForegroundColor DarkYellow
  } elseif ($InsideScout) {
    Write-Host "  Skipping auto-restart: this setup is running inside a Scout session," -ForegroundColor Yellow
    Write-Host "  so restarting would kill it. Close and reopen Scout manually." -ForegroundColor Yellow
  } else {
    $done = Restart-Scout
    if ($done) { Write-Host "  Scout restarted — the skill/MCP servers will load on launch." -ForegroundColor Green }
    else { Write-Host "  Please restart Scout manually so the changes load." -ForegroundColor Yellow }
  }
}

Write-Host "`n3. Next steps" -ForegroundColor Cyan
if (-not $ok) {
  Write-Host "  Setup is INCOMPLETE. Do not initialize a release yet." -ForegroundColor Yellow
  Write-Host "  Resolve the reported dependencies/connectors. In Scout, observe the loaded"
  Write-Host "  tools and rerun infra --no-register --tools-file <inventory.json>."
  Write-Host "  For bootstrap, pass the same fresh inventory with -M365ToolsFile."
  exit 1
}
Write-Host "  * Open Scout and run:  /release-agent"
Write-Host "  * Setup prerequisites are ready; release-specific read probes and attestations still follow.`n"
