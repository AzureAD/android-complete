<#
.SYNOPSIS
    Bootstrap a new release-monitoring report file from the canonical template.

.DESCRIPTION
    Implements SKILL.md Step 1 as a script so the workflow doesn't drift:
      1. Builds the output filename from the version(s) under test:
           release-report-broker-<bv>-auth-<av>-<yyyy-MM-dd>.html
         Omitted apps are dropped from the name (broker-only or auth-only runs are fine).
      2. Creates ~/android-release-reports/_data/<stamp>/ for raw query payloads, where
         <stamp> is the version-pair slug (so two different releases on the same day get
         separate data folders).
      3. Collision rule: if the target file already exists and is an UNFILLED template stub
         (its fingerprint markers still match the canonical template AND size within 5%),
         re-bootstrap silently. Otherwise HALT — a populated report must be explicitly
         deleted/renamed or regenerated with -Force.
      4. Prunes _data/* folders older than -DataRetentionDays (default 60).
      5. Stamps today's date into the "Generated <strong>...</strong>" banner.

    At least one of -BrokerVersion / -AuthVersion is REQUIRED.

.PARAMETER BrokerVersion
    Broker version rolling out (e.g. 16.1.0). Optional if -AuthVersion is given.

.PARAMETER AuthVersion
    Authenticator version rolling out (e.g. 6.2606.3817). Optional if -BrokerVersion is given.

.PARAMETER Force
    Skip the collision check and overwrite any existing file.

.PARAMETER DataRetentionDays
    How many days of _data/* folders to keep before pruning. Default 60.

.PARAMETER SkillRoot
    Path to the skill's assets folder. Defaults to two levels up from this script.

.EXAMPLE
    .\bootstrap-report.ps1 -BrokerVersion 16.1.0 -AuthVersion 6.2606.3817

.EXAMPLE
    .\bootstrap-report.ps1 -BrokerVersion 16.1.0 -Force   # broker-only

.OUTPUTS
    Prints the absolute path of the newly created report file (last line).
#>
[CmdletBinding()]
param(
  [string]$BrokerVersion,
  [string]$AuthVersion,
  [switch]$Force,
  [int]$DataRetentionDays = 60,
  [string]$SkillRoot
)
$ErrorActionPreference = 'Stop'

if (-not $BrokerVersion -and -not $AuthVersion) {
  throw "Provide at least one of -BrokerVersion / -AuthVersion."
}

# Locate the skill's assets folder + canonical template
if (-not $SkillRoot) {
  # This script lives at <skill>/assets/scripts/bootstrap-report.ps1 -> go up 2 to <skill>/assets
  $SkillRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
}
$template = Join-Path $SkillRoot 'templates\report-template.html'
if (-not (Test-Path $template)) {
  throw "Canonical template not found at $template. Pass -SkillRoot if running outside the skill folder."
}

# Build filename + data-folder slug
$today = (Get-Date).ToString('yyyy-MM-dd')
$parts = @()
$slugParts = @()
if ($BrokerVersion) { $parts += "broker-$BrokerVersion"; $slugParts += "b$BrokerVersion" }
if ($AuthVersion)   { $parts += "auth-$AuthVersion";    $slugParts += "a$AuthVersion" }
$nameCore = ($parts -join '-')
$slug = (($slugParts -join '-') -replace '[^0-9A-Za-z\.\-]', '_')

$reportDir = Join-Path $env:USERPROFILE 'android-release-reports'
$dataDir   = Join-Path $reportDir "_data\$slug-$today"
$out       = Join-Path $reportDir "release-report-$nameCore-$today.html"
New-Item -ItemType Directory -Force $reportDir | Out-Null
New-Item -ItemType Directory -Force $dataDir   | Out-Null

# Fingerprint markers to detect an unfilled stub
$templateText = [IO.File]::ReadAllText($template)
function Get-FingerprintMarkers([string]$text) {
  $m = @{}
  if ($text -match '<title>([^<]+?)</title>')                                                              { $m['title']    = $Matches[1].Trim() }
  if ($text -match '<div class="meta">\s*<strong>([^<]+)</strong>')                                        { $m['metaVer']  = $Matches[1].Trim() }
  if ($text -match '<div class="kpi">\s*<div class="label">[^<]+</div>\s*<div class="value">([^<]+?)</div>') { $m['firstKpi'] = $Matches[1].Trim() }
  return $m
}
$templateMarkers = Get-FingerprintMarkers $templateText

if ((Test-Path $out) -and -not $Force) {
  $existingText    = [IO.File]::ReadAllText($out)
  $existingMarkers = Get-FingerprintMarkers $existingText
  $allMatch = $true
  foreach ($k in $templateMarkers.Keys) {
    if ($existingMarkers[$k] -ne $templateMarkers[$k]) { $allMatch = $false; break }
  }
  $sizeRatio = (Get-Item $out).Length / [Math]::Max(1, (Get-Item $template).Length)
  $isUnfilledStub = $allMatch -and ($sizeRatio -ge 0.95) -and ($sizeRatio -le 1.05)
  if ($isUnfilledStub) {
    Write-Warning "Existing $out is an unfilled template stub. Re-bootstrapping silently."
  } else {
    Write-Error @"
A populated report already exists at:
  $out
Per the filename-collision rule, do NOT silently overwrite. Either:
  1. Open it, confirm what changed vs the new data, then re-run with -Force.
  2. Rename / delete it and re-run.
"@
    exit 2
  }
}

Copy-Item $template $out -Force
Write-Host "Bootstrapped $out"
Write-Host "Data folder:   $dataDir"

# Stamp the actual run date (UTF8-no-BOM to preserve emoji/arrows)
$outText = [IO.File]::ReadAllText($out)
$outText = [regex]::Replace($outText, 'Generated\s+<strong>[^<]*</strong>', "Generated <strong>$today</strong>")
[IO.File]::WriteAllText($out, $outText, [System.Text.UTF8Encoding]::new($false))
Write-Host "Stamped Generated date: $today"

# Prune old _data folders
$dataRoot = Join-Path $reportDir '_data'
if (Test-Path $dataRoot) {
  $cutoff = (Get-Date).AddDays(-$DataRetentionDays)
  $old = Get-ChildItem $dataRoot -Directory | Where-Object { $_.FullName -ne $dataDir -and $_.LastWriteTime -lt $cutoff }
  if ($old) {
    Write-Host "Pruning $($old.Count) _data folder(s) older than $DataRetentionDays days."
    $old | ForEach-Object { Remove-Item -Recurse -Force $_.FullName }
  }
}

Write-Output $out
