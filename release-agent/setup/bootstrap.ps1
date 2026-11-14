<#
.SYNOPSIS
  Install or refresh the Release Orchestrator (/release-agent) on this machine.
  Small by design: it only prepares Scout + the environment so the real work
  can run inside Scout. It does NOT run a release.

.DESCRIPTION
  Steps:
    1. Refresh the /release-agent skill, its reference resources and Scout's local
       skill registry record, verifying files and registered instructions.
       This happens before infrastructure
       checks so missing prerequisites cannot prevent a skill update.
       -SkillOnly stops here: no dependency checks, connection changes or restart.
    2. Infrastructure preflight — check CLIs/host deps AND register + verify the
       MCP servers the skill needs inside Scout (from config/requirements.yaml).
       If Python 3.9+ is not discovered, ask for its actual path. -InstallPython
       explicitly opts into winget installation (per-user, no admin);
       -NoAutoInstallPython prevents it. PyYAML is installed via pip if missing.
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
.EXAMPLE
  pwsh .\setup\bootstrap.ps1 -SkillOnly
  Refresh only the installed skill; reload Scout and start a new conversation.
  M365 CONNECTORS
    The terminal installs the skill and registers command-based MCP servers.
    Finish guided onboarding in Scout with: /release-agent check setup
    Scout handles connection guidance and read checks; users do not create JSON files.
    -M365ToolsFile is an advanced automation handoff, not a normal user step.
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
  [switch]$SkillOnly,
  [string]$M365ToolsFile,
  [switch]$NoAutoInstallPython,  # compatibility veto for -InstallPython
  [switch]$NoRestartScout        # by default we restart Scout so new skill/MCP servers load
)

$ErrorActionPreference = "Stop"
if ($SkillOnly -and $SkipSkillInstall) {
  throw "-SkillOnly cannot be combined with -SkipSkillInstall."
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

function Resolve-ScoutExecutable {
  $procs = @(Get-Process -Name 'scout' -ErrorAction SilentlyContinue)
  $candidates = @($procs | Where-Object { $_.Path } | Select-Object -ExpandProperty Path)
  foreach ($name in @('scout', 'scout.exe')) {
    $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { $candidates += $command.Source }
  }
  if ($env:LOCALAPPDATA) {
    $candidates += Join-Path $env:LOCALAPPDATA 'Programs\scout\scout.exe'
    $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Microsoft Scout\scout.exe'
  }
  if ($env:ProgramFiles) {
    $candidates += Join-Path $env:ProgramFiles 'scout\scout.exe'
    $candidates += Join-Path $env:ProgramFiles 'Microsoft Scout\scout.exe'
  }
  return Resolve-SetupPath -Value $ScoutExe -Candidates $candidates -Label 'Scout executable' -Parameter ScoutExe
}

function Stop-RunningScout {
  $procs = @(Get-Process -Name 'scout' -ErrorAction SilentlyContinue)
  if ($procs.Count) {
    Write-Host "  Closing running Scout ($($procs.Count) process(es)) before updating files ..." -ForegroundColor DarkYellow
    $procs | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {} }
    for ($i = 0; $i -lt 24 -and (Get-Process -Name 'scout' -ErrorAction SilentlyContinue); $i++) {
      Start-Sleep -Milliseconds 250
    }
  }
  return ($procs.Count -gt 0)
}

function Start-ScoutDetached {
  param([Parameter(Mandatory = $true)][string]$Exe)
  Write-Host "  Relaunching Scout ..." -ForegroundColor DarkYellow
  $launched = $false
  try {
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "`"$Exe`"" } -ErrorAction Stop
    if ($r.ReturnValue -eq 0) { $launched = $true }
  } catch {}
  if (-not $launched) {
    try { Start-Process explorer.exe -ArgumentList "`"$Exe`""; $launched = $true } catch {}
  }
  return $launched
}

function ConvertFrom-ScoutSkillJson {
  param([Parameter(Mandatory = $true)][string]$Json)
  $options = @{ InputObject = $Json; NoEnumerate = $true; ErrorAction = 'Stop' }
  if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $options.DateKind = 'String' }
  $value = ConvertFrom-Json @options
  return ,$value
}

function Read-ScoutSkillDefinition {
  param([Parameter(Mandatory = $true)][string]$SkillFile)
  $text = [Text.UTF8Encoding]::new($false, $true).GetString([IO.File]::ReadAllBytes($SkillFile)).Replace("`r`n", "`n")
  $document = [regex]::Match($text, '\A---\n(?<metadata>[\s\S]*?)\n---\n?(?<instructions>[\s\S]*)\z')
  if (-not $document.Success) { throw "Skill file must start with YAML frontmatter: $SkillFile" }
  $values = @{}
  foreach ($field in @('name', 'description')) {
    $match = [regex]::Match($document.Groups['metadata'].Value, "(?m)^${field}:[ `t]*(?<value>[^`n]+)$")
    if (-not $match.Success) { throw "Skill frontmatter is missing '$field': $SkillFile" }
    $value = $match.Groups['value'].Value.Trim()
    if ($value.StartsWith('"')) {
      try { $value = ConvertFrom-ScoutSkillJson -Json $value }
      catch { throw "Skill '$field' must be a valid quoted string: $SkillFile" }
    } elseif ($value.StartsWith("'")) {
      if ($value.Length -lt 2 -or -not $value.EndsWith("'")) {
        throw "Skill '$field' has an unterminated quoted string: $SkillFile"
      }
      $value = $value.Substring(1, $value.Length - 2).Replace("''", "'")
    } elseif ($value -match '^[>|[{]') {
      throw "Skill '$field' must be a single-line text value: $SkillFile"
    }
    if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value)) {
      throw "Skill '$field' must be nonempty text: $SkillFile"
    }
    $values[$field] = $value
  }
  if ($values.name -cne 'release-agent') { throw "Skill name must be 'release-agent': $SkillFile" }
  $instructions = $document.Groups['instructions'].Value.Trim()
  if ([string]::IsNullOrWhiteSpace($instructions)) { throw "Skill instructions are empty: $SkillFile" }
  return [PSCustomObject]@{
    name = $values.name
    description = $values.description
    instructions = $instructions
  }
}

function Update-ScoutSkillSyncState {
  param(
    [Parameter(Mandatory = $true)][string]$ScoutRoot,
    [Parameter(Mandatory = $true)]$Entry
  )
  $syncFile = Join-Path $ScoutRoot 'm-sync-state.json'
  if (-not (Test-Path -LiteralPath $syncFile -PathType Leaf)) { return $false }
  try {
    $state = ConvertFrom-ScoutSkillJson -Json ([IO.File]::ReadAllText($syncFile))
  } catch {
    return $false
  }
  if ($null -eq $state -or $state.deviceId -isnot [string]) { return $false }
  $syncKey = "clawpilot/v1/skills/$($Entry.id)"
  $nowIso = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
  $syncedValue = [ordered]@{
    id = [string]$Entry.id
    name = [string]$Entry.name
    description = [string]$Entry.description
    instructions = [string]$Entry.instructions
    createdAt = if ($Entry.createdAt) { [string]$Entry.createdAt } else { $nowIso }
    scope = 'local'
  }
  $existingWrites = @()
  if ($null -ne $state.pendingWrites) {
    $existingWrites = @($state.pendingWrites | Where-Object { $null -ne $_ -and $_.key -cne $syncKey })
  }
  $writeRecord = [PSCustomObject][ordered]@{
    key = $syncKey
    value = [PSCustomObject]$syncedValue
    clientUpdatedAt = $nowIso
    deletedAt = $null
    schemaVersion = 1
  }
  $state | Add-Member -NotePropertyName pendingWrites -NotePropertyValue ([object[]]@($existingWrites + $writeRecord)) -Force
  if ($null -eq $state.lastApplied) {
    $state | Add-Member -NotePropertyName lastApplied -NotePropertyValue ([PSCustomObject]@{}) -Force
  }
  $appliedStamp = [PSCustomObject][ordered]@{
    clientUpdatedAt = $nowIso
    serverUpdatedAt = ''
  }
  $state.lastApplied | Add-Member -NotePropertyName $syncKey -NotePropertyValue $appliedStamp -Force
  $temporary = "$syncFile.$([Guid]::NewGuid().ToString('N')).tmp"
  try {
    $json = ConvertTo-Json -InputObject $state -Depth 100
    [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($temporary, $syncFile, $true)
  } finally {
    if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force }
  }
  return $true
}

function Update-ScoutSkillRegistration {
  param(
    [Parameter(Mandatory = $true)][string]$ScoutSkillsDir,
    [Parameter(Mandatory = $true)]$Definition
  )
  $metadataFile = Join-Path $ScoutSkillsDir 'skills-metadata.json'
  $originalHash = $null
  $metadata = @()
  if (Test-Path -LiteralPath $metadataFile -PathType Leaf) {
    $originalHash = (Get-FileHash -LiteralPath $metadataFile -Algorithm SHA256).Hash
    try { $metadata = ConvertFrom-ScoutSkillJson -Json ([IO.File]::ReadAllText($metadataFile)) }
    catch { throw "Scout skill metadata is unreadable: $($_.Exception.Message)" }
    if ($metadata -isnot [array]) { throw "Scout skill metadata must be a JSON array; no registry changes made." }
    foreach ($item in $metadata) {
      if ($null -eq $item -or $item.id -isnot [string] -or $item.name -isnot [string] -or
          [string]::IsNullOrWhiteSpace($item.id) -or [string]::IsNullOrWhiteSpace($item.name)) {
        throw "Scout skill metadata contains an invalid entry; no registry changes made."
      }
    }
  }
  $matches = @($metadata | Where-Object { $_.name -eq $Definition.name })
  if ($matches.Count -gt 1) { throw "Scout has duplicate '$($Definition.name)' registry entries; no registry changes made." }
  $changed = $false
  if ($matches.Count -eq 1) {
    $entry = $matches[0]
    if ($null -ne $entry.enabled -and $entry.enabled -isnot [bool]) {
      throw "Scout skill enabled state must be boolean; no registry changes made."
    }
    foreach ($field in @('name', 'description', 'instructions')) {
      if ($entry.$field -cne $Definition.$field) {
        $entry | Add-Member -NotePropertyName $field -NotePropertyValue $Definition.$field -Force
        $changed = $true
      }
    }
    if ($null -eq $entry.enabled) {
      $entry | Add-Member -NotePropertyName enabled -NotePropertyValue $true -Force
      $changed = $true
    }
    if ($entry.scope -cne 'local') {
      $entry | Add-Member -NotePropertyName scope -NotePropertyValue 'local' -Force
      $changed = $true
    }
  } else {
    $id = "local-$($Definition.name)"
    if (@($metadata | Where-Object { $_.id -eq $id }).Count) {
      throw "Scout skill ID '$id' belongs to another entry; no registry changes made."
    }
    $entry = [PSCustomObject]@{
      id = $id
      name = $Definition.name
      description = $Definition.description
      instructions = $Definition.instructions
      enabled = $true
      createdAt = [DateTime]::UtcNow.ToString('o')
      scope = 'local'
    }
    $metadata += $entry
    $changed = $true
  }
  if (-not $changed) { return [PSCustomObject]@{ entry = $entry; changed = $false; backup = $null } }
  $temporary = "$metadataFile.$([Guid]::NewGuid().ToString('N')).tmp"
  $backup = $null
  try {
    $json = ConvertTo-Json -InputObject @($metadata) -Depth 100
    [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
    if ($originalHash) {
      if ((Get-FileHash -LiteralPath $metadataFile -Algorithm SHA256).Hash -ne $originalHash) {
        throw "Scout skill metadata changed during refresh; rerun instead of overwriting concurrent changes."
      }
      $backup = "$metadataFile.backup-$([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmssfff'))-$([Guid]::NewGuid().ToString('N'))"
      [IO.File]::Replace($temporary, $metadataFile, $backup)
    } else {
      [IO.File]::Move($temporary, $metadataFile)
    }
  } finally {
    if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force }
  }
  return [PSCustomObject]@{ entry = $entry; changed = $true; backup = $backup }
}

function Test-ScoutSkillRegistration {
  param(
    [Parameter(Mandatory = $true)][string]$ScoutSkillsDir,
    [Parameter(Mandatory = $true)][string]$SkillName
  )
  $skillDir = Join-Path $ScoutSkillsDir $SkillName
  $skillFile = Join-Path $skillDir 'SKILL.md'
  if (-not (Test-Path -LiteralPath $skillFile -PathType Leaf)) {
    return [PSCustomObject]@{ ok = $false; detail = "Installed skill file is missing: $skillFile" }
  }

  $metadataFile = Join-Path $ScoutSkillsDir 'skills-metadata.json'
  if (Test-Path -LiteralPath $metadataFile -PathType Leaf) {
    try {
      $metadata = ConvertFrom-ScoutSkillJson -Json ([IO.File]::ReadAllText($metadataFile))
      if ($metadata -isnot [array]) { throw "Skill metadata must be a JSON array." }
      $entries = @($metadata | Where-Object { $_.name -ceq $SkillName })
      if ($entries.Count -ne 1) {
        return [PSCustomObject]@{ ok = $false; detail = "Scout must have exactly one '$SkillName' registry entry." }
      }
      $entry = $entries[0]
      if ($entry.enabled -isnot [bool] -or -not $entry.enabled) {
        return [PSCustomObject]@{ ok = $false; detail = "Scout metadata has '$SkillName' disabled or invalid." }
      }
      $definition = Read-ScoutSkillDefinition -SkillFile $skillFile
      if ([string]::IsNullOrWhiteSpace($entry.id) -or $entry.scope -cne 'local' -or
          $entry.description -cne $definition.description -or $entry.instructions -cne $definition.instructions) {
        return [PSCustomObject]@{ ok = $false; detail = "Scout registry does not match the installed '$SkillName' instructions." }
      }
    } catch {
      return [PSCustomObject]@{ ok = $false; detail = "Scout skill metadata is unreadable: $($_.Exception.Message)" }
    }
  } else {
    return [PSCustomObject]@{ ok = $false; detail = "Scout skill registry is missing: $metadataFile" }
  }

  $disabledFile = Join-Path $ScoutSkillsDir 'disabled-skills.json'
  if (Test-Path -LiteralPath $disabledFile -PathType Leaf) {
    try {
      $disabled = ConvertFrom-ScoutSkillJson -Json ([IO.File]::ReadAllText($disabledFile))
      if ($disabled -isnot [array]) { throw "Disabled skills must be a JSON array." }
      if ($disabled -contains $SkillName -or $disabled -contains $entry.id) {
        return [PSCustomObject]@{ ok = $false; detail = "Scout disabled-skills list contains '$SkillName'." }
      }
    } catch {
      return [PSCustomObject]@{ ok = $false; detail = "Scout disabled-skills list is unreadable: $($_.Exception.Message)" }
    }
  }

  return [PSCustomObject]@{
    ok = $true
    detail = "matching local registry entry '$($entry.id)', enabled instructions and skill files verified."
  }
}

function Wait-ScoutSkillRegistration {
  param(
    [Parameter(Mandatory = $true)][string]$ScoutSkillsDir,
    [Parameter(Mandatory = $true)][string]$SkillName,
    [int]$TimeoutSeconds = 45,
    [int]$PollMilliseconds = 1000
  )
  $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
  do {
    $result = Test-ScoutSkillRegistration -ScoutSkillsDir $ScoutSkillsDir -SkillName $SkillName
    if ($result.ok) { return $result }
    Start-Sleep -Milliseconds ([Math]::Max(100, $PollMilliseconds))
  } while ((Get-Date) -lt $deadline)
  return $result
}

$ok = $true
$infraReport = $null
$setupReady = $false
$restartNeeded = $false
$skillInstalled = $false
$skillRegistrationChecked = $false
$skillRegistrationPending = $false
$skillRegistrationResult = $null

$ResolvedScoutExe = $null
$ScoutStoppedBeforeInstall = $false
if (-not $SkillOnly -and -not $NoRestartScout -and -not $InsideScout) {
  $ResolvedScoutExe = Resolve-ScoutExecutable
  if ($ResolvedScoutExe) {
    $ScoutStoppedBeforeInstall = Stop-RunningScout
  }
}

Write-Host "1. Skill install/refresh"
if ($SkipSkillInstall) {
  Write-Host "  Skipped (-SkipSkillInstall)."
} else {
  $srcDir = Join-Path $AgentRoot 'skill'
  $src = Join-Path $srcDir 'SKILL.md'
  $destDir = Join-Path $ScoutSkillsDir 'release-agent'
  $destFile = Join-Path $destDir 'SKILL.md'
  Write-Host "  Source: $srcDir" -ForegroundColor DarkGray
  Write-Host "  Destination: $destDir" -ForegroundColor DarkGray
  if (-not (Test-Path -LiteralPath $src -PathType Leaf) -or (Get-Item -LiteralPath $src).Length -eq 0) {
    Write-Host "  [ERROR] Source skill is missing or empty: $src" -ForegroundColor Red
    $ok = $false
  } else {
    $definition = Read-ScoutSkillDefinition -SkillFile $src
    $sourcePath = [IO.Path]::GetFullPath($srcDir).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $destinationPath = [IO.Path]::GetFullPath($destDir).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $separator = [IO.Path]::DirectorySeparatorChar
    if ($sourcePath.Equals($destinationPath, [StringComparison]::OrdinalIgnoreCase) -or
        $destinationPath.StartsWith("$sourcePath$separator", [StringComparison]::OrdinalIgnoreCase) -or
        $sourcePath.StartsWith("$destinationPath$separator", [StringComparison]::OrdinalIgnoreCase)) {
      throw "The installed skill directory must not overlap the source skill directory."
    }
    $files = @(Get-ChildItem -LiteralPath $srcDir -Recurse -File)
    foreach ($file in $files) {
      $relative = [IO.Path]::GetRelativePath($srcDir, $file.FullName)
      $target = Join-Path $destDir $relative
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
      Copy-Item -LiteralPath $file.FullName -Destination $target -Force
      if ((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -ne
          (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) {
        throw "Installed skill resource differs from its source: $relative"
      }
    }
    if ((Get-Item -LiteralPath $destFile).Length -eq 0) { throw "Installed skill instructions are empty." }
    $skillInstalled = $true
    Write-Host "  [OK] Skill files refreshed and SHA-256 verified ($($files.Count) files)." -ForegroundColor Green
    $registration = Update-ScoutSkillRegistration -ScoutSkillsDir $ScoutSkillsDir -Definition $definition
    $syncedToCloudQueue = Update-ScoutSkillSyncState -ScoutRoot $ScoutRoot -Entry $registration.entry
    if ($registration.backup) { Write-Host "  Registry backup: $($registration.backup)" -ForegroundColor DarkGray }
    Write-Host "  [OK] Local skill record saved: $($registration.entry.id)$(if ($syncedToCloudQueue) { ' (queued for Scout sync)' })" -ForegroundColor Green
    Write-Host "  Reload Scout and start a new conversation to load the updated instructions." -ForegroundColor DarkYellow
    $skillRegistrationResult = Test-ScoutSkillRegistration -ScoutSkillsDir $ScoutSkillsDir -SkillName 'release-agent'
    if ($skillRegistrationResult.ok) {
      $skillRegistrationChecked = $true
      Write-Host "  [OK] Installed files checked ($($skillRegistrationResult.detail))" -ForegroundColor Green
    } else {
      Write-Host "  [ACTION NEEDED] $($skillRegistrationResult.detail)" -ForegroundColor Yellow
      $ok = $false
    }
  }
}

if ($SkillOnly) {
  if (-not $ok -or -not $skillInstalled) { exit 1 }
  Write-Host "`nSkill refresh complete. No infrastructure checks, connection changes or app restart were performed." -ForegroundColor Green
  exit 0
}

if ($M365ToolsFile) {
  $M365ToolsFile = (Resolve-Path -LiteralPath $M365ToolsFile).Path
}
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

# ---- 2. Infrastructure preflight (CLIs + MCP servers), data-driven ----
# Delegates to the engine (python -m orchestrator.cli infra), which reads
# config/requirements.yaml, checks every CLI/host dependency, and REGISTERS any
# missing MCP servers into Scout's config (backing it up first). One home for the
# logic; bootstrap just needs python+pyyaml to call it.
Write-Host "`n2. Infrastructure preflight (from config/requirements.yaml)"
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
  # Python is present. Ensure bootstrap-critical packages before running infra.
  $requiredPyPackages = @(
    @{ Name = "PyYAML"; PipName = "pyyaml"; ImportCheck = "import yaml" },
    @{ Name = "tzdata"; PipName = "tzdata"; ImportCheck = "from zoneinfo import ZoneInfo; ZoneInfo('America/Los_Angeles')" }
  )
  $missingInfraPackages = @()
  foreach ($pkg in $requiredPyPackages) {
    & cmd /c "$PyExe -c ""$($pkg.ImportCheck)"" 2>nul"
    if ($LASTEXITCODE -ne 0) {
      Write-Host "  [ ] $($pkg.Name) missing - installing via pip ..." -ForegroundColor DarkYellow
      & cmd /c "$PyExe -m pip install --quiet --disable-pip-version-check $($pkg.PipName)"
      & cmd /c "$PyExe -c ""$($pkg.ImportCheck)"" 2>nul"
      if ($LASTEXITCODE -eq 0) {
        Write-Host "  [x] $($pkg.Name) installed" -ForegroundColor Green
      } else {
        $missingInfraPackages += $pkg.Name
      }
    }
  }
  if ($missingInfraPackages.Count -eq 0) { $haveInfra = $true }
}

if (-not $haveInfra) {
  if (-not $PyExe) {
    Write-Host "  [ ] Python 3.9+ ... still MISSING (needed to run the preflight)" -ForegroundColor Yellow
    if ($NoAutoInstallPython) {
      Write-Host "      Auto-install was disabled (-NoAutoInstallPython)." -ForegroundColor DarkYellow
    }
    Write-Host "      Install Python 3.9+ ('winget install Python.Python.3.12' or https://aka.ms/python)," -ForegroundColor DarkYellow
    Write-Host "      reopen your terminal, then re-run setup." -ForegroundColor DarkYellow
  } else {
    Write-Host "  [ ] Python bootstrap dependencies (PyYAML/tzdata) ... could not be installed automatically" -ForegroundColor Yellow
  }
  $ok = $false
} else {
  Push-Location $AgentRoot
  try {
    $infraArgs = @("-m", "orchestrator.cli", "infra", "--scout-root", $ScoutRoot, "--agency-path", $AgencyPath, "--json")
    if ($M365ToolsFile) {
      $infraArgs += @("--tools-file", $M365ToolsFile)
    }
    if ($PyExe -eq 'py -3') {
      $out = & py -3 @infraArgs
    } else {
      $out = & ($PyExe.Trim('"')) @infraArgs
    }
    $infraExit = $LASTEXITCODE
    $infraReport = ($out -join "`n") | ConvertFrom-Json
    if ($infraReport.error) {
      Write-Host "  Setup check failed: $($infraReport.error)" -ForegroundColor Red
      $ok = $false
    } elseif ($infraReport.ok -isnot [bool] -or $infraReport.infrastructure_ok -isnot [bool] -or
              $infraReport.setup_status -notin @('ready', 'needs_scout_check', 'blocked') -or
              [string]::IsNullOrWhiteSpace($infraReport.next_action) -or
              $infraExit -ne $(if ($infraReport.ok) { 0 } else { 1 })) {
      throw "Setup check returned an invalid result. No setup completion was assumed."
    } else {
      $setupReady = $infraReport.ok
      $ok = $ok -and $infraReport.infrastructure_ok
      $restartNeeded = $infraReport.restart_needed
      $missingDeps = @($infraReport.requirements | Where-Object { -not $_.ok })
      if ($missingDeps.Count -eq 0) { Write-Host "  [OK] Command-line dependencies" -ForegroundColor Green }
      foreach ($item in $missingDeps) {
        Write-Host "  [MISSING] $($item.name) - $($item.install)" -ForegroundColor Yellow
      }
      $missingServers = @($infraReport.mcp_servers | Where-Object { $_.status -notin @('present', 'registered') })
      if ($missingServers.Count -eq 0) { Write-Host "  [OK] Required MCP servers are registered" -ForegroundColor Green }
      foreach ($item in $missingServers) {
        Write-Host "  [ACTION NEEDED] $($item.name) - $($item.detail)" -ForegroundColor Yellow
      }
      foreach ($request in $infraReport.path_requests) {
        Write-Host "  [PATH REQUIRED] $($request.question)" -ForegroundColor Yellow
      }
    }
  } finally { Pop-Location }

  # engine config presence (cheap local sanity check)
  if (Test-Path (Join-Path $AgentRoot 'config\phases.yaml')) {
    Write-Host "  [OK] engine config (phases.yaml)" -ForegroundColor Green
  } else {
    Write-Host "  [MISSING] engine config (phases.yaml)" -ForegroundColor Yellow; $ok = $false
  }
}

$needsReload = ($restartNeeded -or $skillInstalled -or $ScoutStoppedBeforeInstall)
if ($needsReload) {
  Write-Host "`nScout must reload to pick up the newly-installed skill / MCP server(s)."
  if ($NoRestartScout) {
    Write-Host "  Auto-restart disabled (-NoRestartScout). Restart Scout yourself so the changes load." -ForegroundColor DarkYellow
    if ($skillInstalled -and -not $skillRegistrationChecked) { $skillRegistrationPending = $true }
  } elseif ($InsideScout) {
    Write-Host "  Skipping auto-restart: this setup is running inside a Scout session," -ForegroundColor Yellow
    Write-Host "  so restarting would kill it. Close and reopen Scout manually." -ForegroundColor Yellow
    if ($skillInstalled -and -not $skillRegistrationChecked) { $skillRegistrationPending = $true }
  } else {
    if (-not $ResolvedScoutExe) { $ResolvedScoutExe = Resolve-ScoutExecutable }
    if (-not $ScoutStoppedBeforeInstall) { [void](Stop-RunningScout) }
    $done = if ($ResolvedScoutExe) { Start-ScoutDetached -Exe $ResolvedScoutExe } else { $false }
    if ($done) {
      Write-Host "  Scout restarted — verifying skill persistence after startup sync ..." -ForegroundColor Green
      if ($skillInstalled) {
        Start-Sleep -Seconds 6
        $skillRegistrationResult = Wait-ScoutSkillRegistration -ScoutSkillsDir $ScoutSkillsDir -SkillName 'release-agent' -TimeoutSeconds 15
        if ($skillRegistrationResult.ok) {
          $skillRegistrationChecked = $true
          Write-Host "  [OK] Scout registry contains release-agent ($($skillRegistrationResult.detail))" -ForegroundColor Green
        } else {
          Write-Host "  [ ] Scout did not retain release-agent after restart: $($skillRegistrationResult.detail)" -ForegroundColor Yellow
          $ok = $false
        }
      }
    }
    else {
      Write-Host "  Please restart Scout manually so the changes load." -ForegroundColor Yellow
      if ($skillInstalled -and -not $skillRegistrationChecked) { $skillRegistrationPending = $true }
    }
  }
}

if ($skillRegistrationPending) {
  Write-Host "  [ACTION NEEDED] release-agent registration could not be verified in this run." -ForegroundColor Yellow
  Write-Host "  Restart Scout, then rerun setup without -NoRestartScout so registration can be verified." -ForegroundColor DarkYellow
  $ok = $false
}

Write-Host "`n3. Next steps" -ForegroundColor Cyan
if (-not $ok) {
  Write-Host "  Installation needs attention. Fix the reported items, then rerun setup." -ForegroundColor Yellow
  Write-Host "  No release has been started by setup."
  exit 1
}
if (-not $setupReady) {
  if ($infraReport.setup_status -eq 'needs_scout_check') {
    Write-Host "  Installation complete. One check remains in Scout." -ForegroundColor Cyan
    Write-Host "  The terminal cannot check Scout's Microsoft 365 connections."
    Write-Host "  This does not mean a connector is missing."
  } else {
    Write-Host "  Installation complete. A Microsoft 365 connection needs attention." -ForegroundColor Yellow
    Write-Host "  $($infraReport.m365_connectors.detail)"
  }
  Write-Host "  $($infraReport.next_action)"
  Write-Host "  No release has been started by setup."
  exit 1
}
Write-Host "  Setup prerequisites are ready; release-specific readiness checks still follow."
Write-Host "  $($infraReport.next_action)`n"
