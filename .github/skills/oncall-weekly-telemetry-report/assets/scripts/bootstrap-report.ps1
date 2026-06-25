<#
.SYNOPSIS
    Bootstrap a new OCE weekly report file from the canonical template.

.DESCRIPTION
    Implements SKILL.md Step 1 as a script so the workflow doesn't drift across
    runs:
      1. Computes the reporting-week Sunday from the current date (most recent
         complete Sun-Sat week unless -ReportingSunday is passed explicitly).
      2. Creates ~/android-oce-reports/_data/<sunday>/ for raw query payloads.
      3. Decides what to do if the target report file already exists:
         - If the existing file is an UNFILLED template stub (header dates
           still match the canonical template's reference week), silently
           re-bootstrap from the template — there's nothing to preserve.
         - If the existing file contains real per-week content (the dates
           inside differ from the template's reference week), HALT and
           require the caller to explicitly delete or rename the file first.
           This is the "filename collision rule" from SKILL.md.
      4. Prunes _data/<sunday>/ folders older than -DataRetentionDays (default 60)
         so the directory doesn't accumulate stale payloads indefinitely.

.PARAMETER ReportingSunday
    Sunday of the reporting week (yyyy-MM-dd). If omitted, defaults to the most
    recent complete Sun-Sat week relative to the system clock.

.PARAMETER Force
    Skip the collision check and overwrite any existing file.

.PARAMETER DataRetentionDays
    How many days of _data/<sunday>/ folders to keep before pruning. Default 60.

.PARAMETER SkillRoot
    Path to the skill folder. Defaults to the location of this script's parent.

.EXAMPLE
    .\bootstrap-report.ps1
    # Default: latest complete week, halt on collision

.EXAMPLE
    .\bootstrap-report.ps1 -ReportingSunday 2026-05-31 -Force

.OUTPUTS
    Prints the absolute path of the newly created report file.
#>
[CmdletBinding()]
param(
  [string]$ReportingSunday,
  [switch]$Force,
  [int]$DataRetentionDays = 60,
  [string]$SkillRoot
)
$ErrorActionPreference = 'Stop'

# Locate the skill folder + canonical template
if (-not $SkillRoot) {
  # This script lives at <skill>/assets/scripts/bootstrap-report.ps1, so go up 2 levels
  # to reach <skill>/assets/. Templates live at <skill>/assets/templates/.
  $SkillRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
}
$template = Join-Path $SkillRoot 'templates\report-template.html'
if (-not (Test-Path $template)) {
  throw "Canonical template not found at $template. Pass -SkillRoot if running outside the skill folder."
}

# Compute the reporting Sunday
if (-not $ReportingSunday) {
  $today = [datetime]::Today
  # Most recent Sunday strictly before today, OR today if today is Sunday
  $offset = ($today.DayOfWeek.value__ + 7) % 7  # 0..6 days back to the previous Sunday
  $sunday = $today.AddDays(-$offset)
  # If today is Sunday but it's still early in the day, prefer the prior complete week
  if ($today.DayOfWeek -eq [DayOfWeek]::Sunday -and (Get-Date).Hour -lt 6) {
    $sunday = $sunday.AddDays(-7)
  }
  $ReportingSunday = $sunday.ToString('yyyy-MM-dd')
}
[void][datetime]::Parse($ReportingSunday) # validate format

# Paths
$reportDir = Join-Path $env:USERPROFILE 'android-oce-reports'
$dataDir   = Join-Path $reportDir "_data\$ReportingSunday"
$out       = Join-Path $reportDir "oncall-wow-report-$ReportingSunday.html"
New-Item -ItemType Directory -Force $reportDir | Out-Null
New-Item -ItemType Directory -Force $dataDir   | Out-Null

# Read the template's reference dates so we can detect "unfilled stub" collisions.
# A reliable signal of "this file is the template stub": MULTIPLE markers all
# still match the template. We check title, the meta-line dates, AND the first
# KPI value — any divergence means real content has been written.
$templateText = [IO.File]::ReadAllText($template)

function Get-FingerprintMarkers([string]$text) {
  $m = @{}
  if ($text -match '<title>([^<]+?)</title>')                                                              { $m['title']     = $Matches[1].Trim() }
  if ($text -match '<div class="meta">\s*<strong>([^<]+)</strong>')                                        { $m['metaDate']  = $Matches[1].Trim() }
  # NOTE: the "Generated" date is intentionally NOT a fingerprint marker — bootstrap
  # stamps it to the actual run date below, so it never matches the template after copy.
  # First KPI tile's value (e.g. "10.58 B"). Differs week-to-week.
  if ($text -match '<div class="kpi">\s*<div class="label">[^<]+</div>\s*<div class="value">([^<]+?)</div>') { $m['firstKpi']  = $Matches[1].Trim() }
  return $m
}

$templateMarkers = Get-FingerprintMarkers $templateText

# Collision check
if ((Test-Path $out) -and -not $Force) {
  $existingText    = [IO.File]::ReadAllText($out)
  $existingMarkers = Get-FingerprintMarkers $existingText

  # "Unfilled stub" requires ALL markers to match the template AND the file size
  # to be within 5% of the template's. ANY divergence (a single value updated,
  # a single KPI populated, sections added) means real content exists.
  $allMatch = $true
  foreach ($k in $templateMarkers.Keys) {
    if ($existingMarkers[$k] -ne $templateMarkers[$k]) { $allMatch = $false; break }
  }
  $sizeRatio = (Get-Item $out).Length / [Math]::Max(1, (Get-Item $template).Length)
  $sizeClose = ($sizeRatio -ge 0.95) -and ($sizeRatio -le 1.05)

  $isUnfilledStub = $allMatch -and $sizeClose

  if ($isUnfilledStub) {
    Write-Warning "Existing $out is an unfilled template stub (all template fingerprints match, size within 5%). Re-bootstrapping silently."
  } else {
    $divergence = @()
    foreach ($k in $templateMarkers.Keys) {
      if ($existingMarkers[$k] -ne $templateMarkers[$k]) {
        $divergence += "    $k`: template='$($templateMarkers[$k])' existing='$($existingMarkers[$k])'"
      }
    }
    if (-not $sizeClose) {
      $divergence += "    size: template=$((Get-Item $template).Length) bytes  existing=$((Get-Item $out).Length) bytes  ratio=$([Math]::Round($sizeRatio,2))x"
    }
    Write-Error @"
A populated report already exists for the same Sunday bucket:
  $out

Divergence from the template (which is why this is NOT classified as an unfilled stub):
$($divergence -join "`n")

Per the SKILL.md filename-collision rule, do NOT silently overwrite. Either:
  1. Open the existing report, list its top-3 findings, and confirm what changed
     in the new data before regenerating. Then re-run with -Force.
  2. Rename / delete the existing file and re-run.
"@
    exit 2
  }
}

# Bootstrap
Copy-Item $template $out -Force
Write-Host "Bootstrapped $out from $template"
Write-Host "Data folder:   $dataDir"

# Stamp the actual run date into the "Generated <strong>...</strong>" banner so the
# report never carries a stale template date (the v8 bug where it read 2026-06-15
# on a file produced 2026-06-18). This is purely mechanical — today's clock date —
# and has zero off-by-one risk. The reporting-week / baseline / 60-day meta dates
# are still AUTHOR-set (see template-readme.md "Date fields"); bootstrap does not
# touch them because they must be verified against the user's intended week bucket.
# Use UTF8-no-BOM read/write so the report's emojis/arrows survive (the UTF-8 trap).
$today   = (Get-Date).ToString('yyyy-MM-dd')
$outText = [IO.File]::ReadAllText($out)
$outText = [regex]::Replace($outText, 'Generated\s+<strong>[^<]*</strong>', "Generated <strong>$today</strong>")
[IO.File]::WriteAllText($out, $outText, [System.Text.UTF8Encoding]::new($false))
Write-Host "Stamped Generated date: $today"

# Prune old _data folders
$dataRoot = Join-Path $reportDir '_data'
if (Test-Path $dataRoot) {
  $cutoff = (Get-Date).AddDays(-$DataRetentionDays)
  $oldFolders = Get-ChildItem $dataRoot -Directory | Where-Object {
    # Folder name should look like a date; skip the current run's folder
    $_.FullName -ne $dataDir -and
    $_.LastWriteTime -lt $cutoff
  }
  if ($oldFolders) {
    Write-Host "Pruning $($oldFolders.Count) _data folder(s) older than $DataRetentionDays days:"
    $oldFolders | ForEach-Object {
      Write-Host "  removing $($_.FullName) (last write $($_.LastWriteTime.ToString('yyyy-MM-dd')))"
      Remove-Item -Recurse -Force $_.FullName
    }
  }
}

# Print the path so callers can capture it
Write-Output $out
