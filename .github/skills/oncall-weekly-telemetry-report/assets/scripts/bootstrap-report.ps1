<#
.SYNOPSIS
    Bootstrap a new OCE weekly report file from the canonical template.

.DESCRIPTION
    Implements SKILL.md Step 1 as a script so the workflow doesn't drift across
    runs. The reporting window is a ROLLING 7-DAY window ending at start-of-day
    (UTC) on -EndDate (defaults to today):

        curStart  = EndDate - 7d      (inclusive)
        curEnd    = EndDate           (exclusive, == 00:00 UTC of the end date)
        prevStart = EndDate - 14d
        prevEnd   = curStart

    Rationale: the prior
    implementation aligned the reporting window on Sun-Sat calendar weeks and
    defaulted to "the Sunday of the currently in-progress week". Run on a
    Thursday it emitted a 4-day partial window and dropped the last complete
    week -- the exact "missed the last 6 days" the developer reported. The
    rolling-window model always covers the 7 fully-elapsed days immediately
    before the invocation with no user prompting.

    The script also:
      1. Creates ~/android-oce-reports/_data/<end-date>/ for raw query payloads.
      2. Copies the canonical template into
         ~/android-oce-reports/oncall-wow-report-<end-date>.html.
      3. Stamps the resolved window into the <title>, the <div class="meta">
         block, and the "Generated <strong>...</strong>" banner so the header
         can never drift from what was actually queried (the resolved window
         is echoed in the report header for transparency).
      4. Decides what to do if the target report file already exists:
         - If the existing file is an UNFILLED template stub (multiple
           fingerprint markers still match the canonical template), silently
           re-bootstrap -- nothing to preserve.
         - Otherwise HALT and require -Force.
      5. Prunes _data/<old-end-date>/ folders older than -DataRetentionDays
         (default 60).

.PARAMETER EndDate
    End of the reporting window (yyyy-MM-dd, UTC). Exclusive upper bound: data
    is queried up to but not including 00:00 UTC on this date. Defaults to
    today (UTC). Example: -EndDate 2026-07-09 on a run at any local time on
    2026-07-09 UTC produces the same window [2026-07-02 00:00, 2026-07-09 00:00).

.PARAMETER Force
    Skip the collision check and overwrite any existing file.

.PARAMETER DataRetentionDays
    How many days of _data/<end-date>/ folders to keep before pruning. Default 60.

.PARAMETER SkillRoot
    Path to the skill folder. Defaults to the location of this script's parent.

.EXAMPLE
    .\bootstrap-report.ps1
    # Default: rolling 7 days ending today (UTC), halt on collision.

.EXAMPLE
    .\bootstrap-report.ps1 -EndDate 2026-07-09 -Force
    # Reproduce the report for a specific window end.

.OUTPUTS
    Prints the absolute path of the newly created report file.

.NOTES
    Breaking change vs pre-fix versions:
      * -ReportingSunday parameter has been REMOVED; use -EndDate instead.
        (The old name encoded the exact semantic mismatch that caused the bug.)
      * Filename is now oncall-wow-report-<end-date>.html (was <sunday>.html).
#>
[CmdletBinding()]
param(
  [string]$EndDate,
  [switch]$Force,
  [int]$DataRetentionDays = 60,
  [string]$SkillRoot
)
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Locate the skill folder + canonical template
# ---------------------------------------------------------------------------
if (-not $SkillRoot) {
  # This script lives at <skill>/assets/scripts/bootstrap-report.ps1, so go up
  # 2 levels to reach <skill>/assets/. Templates live at <skill>/assets/templates/.
  $SkillRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
}
$template = Join-Path $SkillRoot 'templates\report-template.html'
if (-not (Test-Path $template)) {
  throw "Canonical template not found at $template. Pass -SkillRoot if running outside the skill folder."
}

# ---------------------------------------------------------------------------
# Resolve the rolling window
# ---------------------------------------------------------------------------
if (-not $EndDate) {
  # UTC "today" so the window boundaries are stable across time zones and
  # match Kusto's default datetime semantics (EventInfo_Time is UTC).
  $EndDate = [datetime]::UtcNow.Date.ToString('yyyy-MM-dd')
}
try {
  $curEnd = [datetime]::ParseExact($EndDate, 'yyyy-MM-dd',
                                   [System.Globalization.CultureInfo]::InvariantCulture,
                                   [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
                                   [System.Globalization.DateTimeStyles]::AdjustToUniversal)
} catch {
  throw "Invalid -EndDate '$EndDate'. Use yyyy-MM-dd."
}
$curStart  = $curEnd.AddDays(-7)
$prevStart = $curEnd.AddDays(-14)
$prevEnd   = $curStart

# 60-day trend spans the LITERAL last 60 days ending curEnd (today), so BOTH
# bounds move with -EndDate (the trend ends today rather than 3-6 days ago on
# the last complete Sunday). The trend CHART shows the
# current in-progress week as its final (partial) bar, but bucket-trends.js still
# computes regression/improvement DELTAS on complete Sun-Sat weeks only -- a
# partial week as "last" would read as a fake -99% improvement. So we resolve two
# distinct things:
#   * data/chart window   [sixtyDayStart, sixtyDayEnd) = [curEnd-60d, curEnd)
#                         -> queried and charted (final bucket is the partial week)
#   * classification cutoff  trendClassEnd = startofweek(curEnd)
#                         -> passed to bucket-trends.js as --end so the partial
#                            current week is excluded from the delta math while
#                            still being drawn.
# Per-week bucketing stays Sun-Sat aligned (Kusto startofweek() is Sunday-based),
# so the first and last buckets are partial by construction.
$curEndDow      = [int]$curEnd.DayOfWeek  # Sun=0 .. Sat=6
$sixtyDayStart  = $curEnd.AddDays(-60)             # literal 60 days ending today
$sixtyDayEnd    = $curEnd                          # exclusive upper bound == today; chart includes the partial current week
$trendClassEnd  = $curEnd.AddDays(-$curEndDow)     # startofweek(curEnd): weeks >= this are the in-progress (partial) week, excluded from delta classification

# Sanity check: curEnd is an exclusive 00:00-UTC date boundary and must be today
# (UTC) or earlier. Compare date-to-date -- a sub-day clock slack here would let
# *tomorrow* through as -EndDate when the script runs in the last hour before
# midnight UTC, which would include today's partial data and shift the window.
$todayUtc = [datetime]::UtcNow.Date
if ($curEnd -gt $todayUtc) {
  throw "Resolved curEnd $($curEnd.ToString('yyyy-MM-dd')) UTC is in the future (today UTC = $($todayUtc.ToString('yyyy-MM-dd'))). -EndDate must be today or earlier. Refusing to bootstrap a report with a future window."
}

$curEndStr    = $curEnd.ToString('yyyy-MM-dd')
$curStartStr  = $curStart.ToString('yyyy-MM-dd')
$prevStartStr = $prevStart.ToString('yyyy-MM-dd')
$prevEndStr   = $prevEnd.ToString('yyyy-MM-dd')

Write-Host "Resolved reporting window (UTC):"
Write-Host "  Last 7 days:   $curStartStr -> $curEndStr  (exclusive upper bound)"
Write-Host "  Baseline:      $prevStartStr -> $prevEndStr"
Write-Host "  60-day trend:  $($sixtyDayStart.ToString('yyyy-MM-dd')) -> $($sixtyDayEnd.ToString('yyyy-MM-dd'))  (literal 60d ending today; chart includes current partial week)"
Write-Host "  Trend delta cutoff: weeks < $($trendClassEnd.ToString('yyyy-MM-dd'))  (startofweek(curEnd); pass as bucket-trends.js --end)"
# NOTE: Console output uses ASCII '->'; the HTML stamp below uses U+2192 arrows
# and U+00B7 middle-dots to match the template's canonical visual style. This
# is safe because $outText is written via [System.Text.UTF8Encoding]::new($false)
# which preserves multi-byte code points end-to-end (per the UTF-8 trap in
# template-readme.md, only the '@...@' heredoc-to-Set-Content path strips them).

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
$reportDir = Join-Path $env:USERPROFILE 'android-oce-reports'
$dataDir   = Join-Path $reportDir "_data\$curEndStr"
$out       = Join-Path $reportDir "oncall-wow-report-$curEndStr.html"
New-Item -ItemType Directory -Force $reportDir | Out-Null
New-Item -ItemType Directory -Force $dataDir   | Out-Null

# ---------------------------------------------------------------------------
# Collision detection -- "unfilled stub" vs "real report"
#
# Fail-safe by design: we only silently overwrite when we can POSITIVELY
# identify the existing file as a freshly-bootstrapped, never-populated stub.
# The authoritative signal is the OCE-UNPOPULATED-STUB sentinel that bootstrap
# injects into every stub (see the write step below) and that validate-report.ps1
# refuses to let a report publish with. A populated (validated) report can never
# carry it, so real work can never be misclassified as a stub. As a second guard
# we also require the first KPI to still hold the template's value. Anything that
# is not a positively-identified stub requires an explicit -Force to overwrite;
# when in doubt we refuse and exit rather than risk clobbering real work.
# ---------------------------------------------------------------------------
$stubSentinel = 'OCE-UNPOPULATED-STUB'
$templateText = [IO.File]::ReadAllText($template)

function Get-FirstKpi([string]$text) {
  if ($text -match '<div class="kpi">\s*<div class="label">[^<]+</div>\s*<div class="value">([^<]+?)</div>') { return $Matches[1].Trim() }
  return $null
}
$templateFirstKpi = Get-FirstKpi $templateText

if ((Test-Path $out) -and -not $Force) {
  $existingText      = [IO.File]::ReadAllText($out)
  $hasStubSentinel   = $existingText.Contains($stubSentinel)
  $existingFirstKpi  = Get-FirstKpi $existingText
  $firstKpiUnchanged = ($null -ne $templateFirstKpi) -and ($existingFirstKpi -eq $templateFirstKpi)
  $isUnfilledStub    = $hasStubSentinel -and $firstKpiUnchanged

  if ($isUnfilledStub) {
    Write-Warning "Existing $out is an unpopulated bootstrap stub (carries the $stubSentinel sentinel and still holds the template's first-KPI value). Re-bootstrapping silently."
  } else {
    $reasons = @()
    if (-not $hasStubSentinel)   { $reasons += "    - the $stubSentinel sentinel is absent (a populated/validated report never carries it)" }
    if (-not $firstKpiUnchanged) { $reasons += "    - the first KPI differs from the template (existing='$existingFirstKpi' template='$templateFirstKpi')" }
    Write-Error @"
A report already exists for the same end-date bucket and is NOT a positively-identified unfilled stub:
  $out

Why this is not treated as a re-bootstrappable stub:
$($reasons -join "`n")

Refusing to overwrite to avoid clobbering real work. Either:
  1. Open the existing report, confirm what it contains, then re-run with -Force to overwrite.
  2. Rename / delete the existing file and re-run.
"@
    exit 2
  }
}

# ---------------------------------------------------------------------------
# Bootstrap: copy the template
# ---------------------------------------------------------------------------
Copy-Item $template $out -Force
Write-Host "Bootstrapped $out from $template"
Write-Host "Data folder:   $dataDir"

# ---------------------------------------------------------------------------
# Stamp the resolved window into the report header
#
# The template ships with hard-coded dates from a real prior week. We rewrite
# them mechanically so the header always matches what the queries actually
# targeted. This removes the last hand-authored date field and closes the
# window-drift class of bugs entirely (the resolved window is echoed in the
# report header for transparency).
#
# Uses UTF8-no-BOM read/write so the report's emojis/arrows survive (the
# UTF-8 trap called out in template-readme.md).
# ---------------------------------------------------------------------------
function Format-DateHuman([datetime]$d, [switch]$IncludeYear) {
  # e.g. "Thu Jul 2, 2026" or "Thu Jul 2"
  $dow = $d.ToString('ddd', [System.Globalization.CultureInfo]::InvariantCulture)
  $mon = $d.ToString('MMM', [System.Globalization.CultureInfo]::InvariantCulture)
  $day = $d.Day
  if ($IncludeYear) { return "$dow $mon $day, $($d.Year)" }
  return "$dow $mon $day"
}
# Display dates in the header. For a half-open [curStart, curEnd) window the
# calendar dates covered are [curStart, curEnd - 1 day], but the user-facing
# convention (matching the ADO issue "Jul 2 - Jul 9 when run on Jul 9") shows
# the interval endpoints -- so we render curStart -> curEnd literally.
# U+2192 RIGHTWARDS ARROW and U+00B7 MIDDLE DOT, matching the template's style.
$arrow = [char]0x2192
$dot   = [char]0x00B7
$curLabel        = "$(Format-DateHuman $curStart -IncludeYear:$false) $arrow $(Format-DateHuman $curEnd -IncludeYear:$true)"
$prevLabel       = "$(Format-DateHuman $prevStart -IncludeYear:$false) $arrow $(Format-DateHuman $prevEnd -IncludeYear:$false)"
# 60-day trend: literal 60 days ending curEnd (today). curEnd is the exclusive
# upper bound, so the last calendar day carrying data is curEnd - 1 (yesterday).
# The final weekly bucket is the current in-progress week (partial) -- see
# bootstrap's $trendClassEnd note and bucket-trends.js --include-partial-end.
$sixtyDayLabel   = "$(Format-DateHuman $sixtyDayStart -IncludeYear:$false) $arrow $(Format-DateHuman ($sixtyDayEnd.AddDays(-1)) -IncludeYear:$true)"
$todayStr         = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')

$outText = [IO.File]::ReadAllText($out)

# 1) <title>...</title>
$newTitle = "Android Broker $dot On-Call Report $([char]0x2014) Last 7 days ending $curEndStr"
$outText  = [regex]::Replace($outText, '<title>[^<]*</title>', "<title>$newTitle</title>")

# 2) The <div class="meta"> block up through the closing </div> immediately
#    before the badge. We use a single-line non-greedy match on the full block.
$newMeta = @"
<div class="meta">
      <strong>Last 7 days: $curLabel</strong> &nbsp;vs&nbsp; <strong>$prevLabel</strong> &nbsp;$dot&nbsp;
      60-day trend: <strong>$sixtyDayLabel</strong> (last 60 days; final bar in progress) &nbsp;$dot&nbsp;
      Source: <code>android_spans</code> materialized views &nbsp;$dot&nbsp;
      Generated <strong>$todayStr</strong>
    </div>
"@
# The template's meta div is multi-line; use single-line mode with .*? non-greedy.
$outText = [regex]::Replace($outText, '(?s)<div class="meta">.*?</div>', $newMeta)

# 3) Belt-and-suspenders: if for some reason the meta-div rewrite above didn't
#    hit (e.g. the template was restructured), still update the Generated banner
#    so the file never carries a stale template date.
$outText = [regex]::Replace($outText, 'Generated\s+<strong>[^<]*</strong>', "Generated <strong>$todayStr</strong>")

# 4) Inject the unpopulated-stub sentinel right after the DOCTYPE so a later
#    bootstrap can POSITIVELY identify this file as a never-populated stub that
#    is safe to silently overwrite. The author replaces the template's KPI /
#    table / prose values and DELETES this line while filling the report;
#    validate-report.ps1 refuses to pass a report that still carries it, so a
#    published report can never be mistaken for a stub.
$stubMarker = "<!-- OCE-UNPOPULATED-STUB: freshly bootstrapped, not yet populated. Replace the template KPI/table/prose values with the current window's data and DELETE this line before validating/publishing. -->"
if ($outText -notmatch 'OCE-UNPOPULATED-STUB') {
  $outText = [regex]::Replace($outText, '(<!DOCTYPE html>)', "`$1`n$stubMarker", 1)
}

[IO.File]::WriteAllText($out, $outText, [System.Text.UTF8Encoding]::new($false))
Write-Host "Stamped resolved window into <title> and meta block. Generated=$todayStr."

# ---------------------------------------------------------------------------
# Prune old _data folders
# ---------------------------------------------------------------------------
$dataRoot = Join-Path $reportDir '_data'
if (Test-Path $dataRoot) {
  $cutoff = (Get-Date).AddDays(-$DataRetentionDays)
  $oldFolders = Get-ChildItem $dataRoot -Directory | Where-Object {
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
