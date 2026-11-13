<#
.SYNOPSIS
    Build the combined on-call index page from the two finished weekly reports.

.DESCRIPTION
    `both` mode produces two independent reports. This script emits a single
    one-page digest that links to each and reproduces their headline KPI tiles,
    so the on-call engineer has one URL to open and one place to scan.

    It is deliberately a SCRAPER, not an author: every value on the index is
    lifted verbatim out of the report HTML. Nothing is recomputed, and no new
    finding is introduced. That guarantee is what makes the index safe -- a
    number can never disagree with the report it came from.

    Missing reports are not fatal. If only one app ran (or one failed), the
    index renders that app's card and an explicit "not generated this run"
    state for the other, so a half-delivered rotation is still publishable and
    the gap is visible rather than silent.

.PARAMETER EndDate
    The reporting window end-date (yyyy-MM-dd) shared by both reports. Defaults
    to today (UTC), matching bootstrap-report.ps1's default.

.PARAMETER ReportDir
    Folder holding the reports. Defaults to $env:USERPROFILE\android-oce-reports.

.PARAMETER MaxKpis
    How many headline KPI tiles to reproduce per app. Default 6.

.PARAMETER SkillRoot
    Path to the skill's assets folder. Defaults to this script's grandparent.

.EXAMPLE
    .\build-index.ps1
    .\build-index.ps1 -EndDate 2026-07-30

.OUTPUTS
    Prints the absolute path of the index file.
#>
[CmdletBinding()]
param(
    [string]$EndDate,
    [string]$ReportDir,
    [int]$MaxKpis = 6,
    [string]$SkillRoot
)
$ErrorActionPreference = 'Stop'

if (-not $EndDate)   { $EndDate   = [datetime]::UtcNow.Date.ToString('yyyy-MM-dd') }
if (-not $ReportDir) { $ReportDir = Join-Path $env:USERPROFILE 'android-oce-reports' }
if (-not $SkillRoot) { $SkillRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath) }

$template = Join-Path $SkillRoot 'templates\index-template.html'
if (-not (Test-Path $template)) {
    throw "Index template not found at $template. Pass -SkillRoot if running outside the skill folder."
}

$apps = @(
    @{ Key = 'broker';  Prefix = 'oncall';  Name = 'Android Broker'
       Sub  = 'Silent + interactive auth reliability, error-code attribution, latency, adoption' }
    @{ Key = 'authapp'; Prefix = 'authapp'; Name = 'Authenticator Android'
       Sub  = 'Scenario funnels, unknown/abandonment, push-notification completion, crash &amp; stability' }
)

function Get-Kpis([string]$html, [int]$max) {
    # Scrape .kpi tiles in document order. The report's first .kpi-grid is the
    # headline grid by construction (both templates open with it), so taking the
    # first $max tiles reproduces exactly what a reader sees above the fold.
    # Matches both the multi-line and single-line .kpi forms the templates use.
    $re = '(?s)<div class="kpi">\s*' +
          '<div class="label">(.*?)</div>\s*' +
          '<div class="value">(.*?)</div>' +
          '(?:\s*<div class="delta ([a-z\-]*)">(.*?)</div>)?'
    $out = @()
    foreach ($m in [regex]::Matches($html, $re)) {
        if ($out.Count -ge $max) { break }
        $out += [pscustomobject]@{
            Label      = $m.Groups[1].Value.Trim()
            Value      = $m.Groups[2].Value.Trim()
            DeltaClass = if ($m.Groups[3].Success -and $m.Groups[3].Value) { $m.Groups[3].Value } else { 'delta-flat' }
            Delta      = $m.Groups[4].Value.Trim()
        }
    }
    return $out
}

function Get-MetaLine([string]$html) {
    $m = [regex]::Match($html, '(?s)<div class="meta">(.*?)</div>')
    if (-not $m.Success) { return $null }
    # Collapse to a single line and strip tags -- the index shows it as plain text.
    $t = [regex]::Replace($m.Groups[1].Value, '<[^>]+>', '')
    $t = ($t -replace '&nbsp;', ' ') -replace '\s+', ' '
    return $t.Trim()
}

$cards    = New-Object System.Text.StringBuilder
$found    = @()
$missing  = @()
$windowLine = $null

foreach ($app in $apps) {
    $file = Join-Path $ReportDir "$($app.Prefix)-wow-report-$EndDate.html"
    [void]$cards.AppendLine('  <div class="report-card">')
    [void]$cards.AppendLine('    <div class="card-head">')
    [void]$cards.AppendLine("      <h3>$($app.Name)</h3>")

    if (-not (Test-Path $file)) {
        $missing += $app.Key
        [void]$cards.AppendLine('    </div>')
        [void]$cards.AppendLine("    <div class=`"card-sub`">$($app.Sub)</div>")
        [void]$cards.AppendLine("    <p class=`"empty`">Not generated this run &mdash; no <code>$($app.Prefix)-wow-report-$EndDate.html</code> found in $ReportDir.</p>")
        [void]$cards.AppendLine('  </div>')
        Write-Warning "$($app.Name): report not found at $file - rendering an explicit empty state."
        continue
    }

    $found += $app.Key
    $html = [IO.File]::ReadAllText($file)

    # Refuse to index an unpopulated stub -- publishing template numbers under a
    # real-looking heading is worse than showing the gap.
    if ($html.Contains('OCE-UNPOPULATED-STUB')) {
        throw "$file still carries the OCE-UNPOPULATED-STUB sentinel (it was bootstrapped but never populated). Refusing to build an index over template data. Populate and validate the report first."
    }

    if (-not $windowLine) { $windowLine = Get-MetaLine $html }

    $leaf = Split-Path $file -Leaf
    [void]$cards.AppendLine("      <a class=`"open-link`" href=`"$leaf`">Open full report &rarr;</a>")
    [void]$cards.AppendLine('    </div>')
    [void]$cards.AppendLine("    <div class=`"card-sub`">$($app.Sub)</div>")

    $kpis = Get-Kpis $html $MaxKpis
    if ($kpis.Count -eq 0) {
        [void]$cards.AppendLine('    <p class="empty">No KPI tiles found to summarise &mdash; open the report directly.</p>')
        Write-Warning "$($app.Name): no .kpi tiles matched in $leaf."
    } else {
        [void]$cards.AppendLine('    <div class="kpi-grid">')
        foreach ($k in $kpis) {
            [void]$cards.AppendLine('      <div class="kpi">')
            [void]$cards.AppendLine("        <div class=`"label`">$($k.Label)</div>")
            [void]$cards.AppendLine("        <div class=`"value`">$($k.Value)</div>")
            if ($k.Delta) { [void]$cards.AppendLine("        <div class=`"delta $($k.DeltaClass)`">$($k.Delta)</div>") }
            [void]$cards.AppendLine('      </div>')
        }
        [void]$cards.AppendLine('    </div>')
        Write-Host "$($app.Name): reproduced $($kpis.Count) KPI tile(s) from $leaf"
    }
    [void]$cards.AppendLine('  </div>')
}

if ($found.Count -eq 0) {
    Write-Error "No reports found for end-date $EndDate in $ReportDir. Nothing to index. Run bootstrap-report.ps1 + the playbooks first."
    exit 2
}

if (-not $windowLine) { $windowLine = "Reporting window ending $EndDate" }

$out  = Join-Path $ReportDir "oce-index-$EndDate.html"
$text = [IO.File]::ReadAllText($template)
$today = [datetime]::UtcNow.ToString('yyyy-MM-dd')

$text = $text.Replace('<!--INDEX:TITLE-->',     "Android Auth $([char]0x00B7) Weekly On-Call Digest $([char]0x00B7) $EndDate")
$text = $text.Replace('<!--INDEX:WINDOW-->',    $windowLine)
$text = $text.Replace('<!--INDEX:GENERATED-->', $today)
$text = $text.Replace('<!--INDEX:CARDS-->',     $cards.ToString().TrimEnd())

# UTF-8 without BOM -- same trap as the reports: a heredoc/Set-Content path would
# strip the multi-byte arrows and middle-dots stamped above.
[IO.File]::WriteAllText($out, $text, [System.Text.UTF8Encoding]::new($false))

Write-Host "Built index: $out"
if ($missing.Count -gt 0) {
    Write-Warning "Index published with $($missing.Count) app(s) missing: $($missing -join ', '). Say so explicitly in chat."
}
Write-Output $out
