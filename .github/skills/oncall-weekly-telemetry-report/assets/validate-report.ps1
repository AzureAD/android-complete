<#
.SYNOPSIS
    Validate a generated OCE weekly report HTML before publishing.

.DESCRIPTION
    Runs all required pre-publish checks per SKILL.md "Output checklist":
      1. No stale-template tokens ({{...}} placeholders or "EXAMPLE CONTENT BELOW" sentinel).
      2. No `devs` / `reqs` in user-facing text (only allowed inside <pre><code> KQL blocks).
      3. No U+FFFD (Unicode replacement character) — catches mojibake from emoji edits.
      4. Section 2 callouts are siblings, NOT nested. Tracks <div> open/close depth
         from #attention to #trend60d; the depth must return to 0 between callouts.
      5. (Informational) Reports HTML size and number of <div class="callout"> openings.

    Exits with non-zero status if any HARD check fails (stale tokens, devs/reqs leak,
    U+FFFD, or unbalanced div depth in the attention block).

.PARAMETER Path
    Absolute path to the report file. Defaults to the current week's report under
    $env:USERPROFILE\android-oce-reports\.

.EXAMPLE
    .\validate-report.ps1
    .\validate-report.ps1 -Path C:\path\to\oncall-wow-report-2026-05-03.html
#>
[CmdletBinding()]
param(
    [string]$Path
)

# Default: pick the most-recent oncall-wow-report-*.html in the user's reports folder
if (-not $Path) {
    $reportDir = Join-Path $env:USERPROFILE 'android-oce-reports'
    $latest = Get-ChildItem $reportDir -Filter 'oncall-wow-report-*.html' -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) {
        Write-Error "No oncall-wow-report-*.html found in $reportDir. Pass -Path explicitly."
        exit 2
    }
    $Path = $latest.FullName
}

if (-not (Test-Path $Path)) {
    Write-Error "Report file not found: $Path"
    exit 2
}

$failures = @()
$warnings = @()

function Add-Fail($msg) { $script:failures += $msg; Write-Host "  [FAIL] $msg" -ForegroundColor Red }
function Add-Warn($msg) { $script:warnings += $msg; Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Pass($msg)     { Write-Host "  [OK]   $msg" -ForegroundColor Green }

Write-Host ""
Write-Host "Validating: $Path"
Write-Host ("Size: {0:N0} bytes" -f (Get-Item $Path).Length)
Write-Host ""

# ---- 1. Stale tokens / EXAMPLE sentinel ----
$stale = Select-String -Path $Path -Pattern '\{\{|EXAMPLE CONTENT BELOW|EXAMPLE_'
if ($stale.Count -gt 0) {
    Add-Fail "Stale template tokens found ($($stale.Count)). First few:"
    $stale | Select-Object -First 5 | ForEach-Object { Write-Host "         L$($_.LineNumber): $($_.Line.Trim().Substring(0, [Math]::Min(110, $_.Line.Trim().Length)))" }
} else {
    Pass "No stale {{...}} tokens or EXAMPLE sentinel"
}

# ---- 2. devs / reqs in user-facing text ----
# Allowed: occurrences inside <pre><code>...</code></pre> KQL blocks.
$content = [System.IO.File]::ReadAllText($Path)
$contentNoCode = [regex]::Replace($content, '(?s)<pre[^>]*>.*?</pre>', '')
$contentNoCode = [regex]::Replace($contentNoCode, '(?s)<code[^>]*>.*?</code>', '')
$drMatches = [regex]::Matches($contentNoCode, '\b(devs|reqs)\b', 'IgnoreCase')
if ($drMatches.Count -gt 0) {
    Add-Fail "Found $($drMatches.Count) devs/reqs occurrence(s) in user-facing text (use 'devices' / 'requests'). First few contexts:"
    $drMatches | Select-Object -First 5 | ForEach-Object {
        $ctxStart = [Math]::Max(0, $_.Index - 40)
        $ctxLen = [Math]::Min(100, $contentNoCode.Length - $ctxStart)
        $ctx = $contentNoCode.Substring($ctxStart, $ctxLen) -replace '\s+', ' '
        Write-Host "         ...$ctx..."
    }
} else {
    Pass "No devs/reqs in user-facing text"
}

# ---- 3. U+FFFD (mojibake from emoji edits) ----
$bytes = [System.IO.File]::ReadAllBytes($Path)
$text = [System.Text.Encoding]::UTF8.GetString($bytes)
$ufffd = ($text.ToCharArray() | Where-Object { $_ -eq [char]0xFFFD }).Count
if ($ufffd -gt 0) {
    Add-Fail "$ufffd U+FFFD replacement character(s) found (mojibake). First context:"
    $i = $text.IndexOf([char]0xFFFD)
    $start = [Math]::Max(0, $i - 30); $end = [Math]::Min($text.Length, $i + 30)
    Write-Host "         ...$($text.Substring($start, $end - $start) -replace "`r?`n", ' ')..."
} else {
    Pass "No U+FFFD (no mojibake)"
}

# ---- 4. Section 2 div balance ----
$lines = Get-Content $Path
$startIdx = -1; $endIdx = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match 'id="attention"') { $startIdx = $i }
    if ($lines[$i] -match 'id="trend60d"')  { $endIdx = $i; break }
}
if ($startIdx -ge 0 -and $endIdx -gt $startIdx) {
    $depth = 0
    for ($i = $startIdx; $i -le $endIdx; $i++) {
        if ($null -eq $lines[$i]) { continue }
        $depth += ([regex]::Matches($lines[$i], '<div\b')).Count
        $depth -= ([regex]::Matches($lines[$i], '</div>')).Count
    }
    if ($depth -ne 0) {
        Add-Fail "Section 2 (attention block) has unbalanced <div>s; net depth at end = $depth (expected 0). Likely cause: a callout is missing its closing </div>, which makes the next callout nest inside it."
    } else {
        Pass "Section 2 div balance OK (depth returns to 0)"
    }
} else {
    Add-Warn "Could not locate the attention block (#attention / #trend60d anchors). Skipping div-balance check."
}

# ---- 5. Informational: callout count + nested-callout sanity ----
$calloutOpens = ([regex]::Matches($content, '<div class="callout(?:\s|")')).Count
Write-Host ""
Write-Host "Info: $calloutOpens callout container(s) in the document."

# ---- 6. Sparkline / trend chart coverage ----
# The footer JS auto-renders any element with data-spark or data-trend. If the
# count is near-zero, the body was likely rebuilt without sparklines (v7
# regression — chartless report).
#
# Two checks:
#   6a. STRUCTURAL (HARD FAIL): if the report has KPI tiles but >half lack
#       data-spark, the rebuild dropped them — fail the build.
#   6b. OVERALL (WARN): total chart elements should be ~30+ (8 KPI sparks +
#       ~10 trend rows + ~12 WoW-table rows). Warn if under 15.
$sparkCount = ([regex]::Matches($content, 'data-spark=')).Count
$trendCount = ([regex]::Matches($content, 'data-trend=')).Count
$inlineSvg  = ([regex]::Matches($content, '<svg[^>]*class="?sparkline')).Count
$kpiTiles   = ([regex]::Matches($content, '<div class="kpi"')).Count
$totalCharts = $sparkCount + $trendCount + $inlineSvg
Write-Host ""
Write-Host "Info: $sparkCount data-spark, $trendCount data-trend, $inlineSvg inline sparkline svg(s), $kpiTiles KPI tile(s)."

if ($kpiTiles -ge 4 -and $sparkCount -lt [Math]::Ceiling($kpiTiles / 2)) {
    Add-Fail "Only $sparkCount data-spark element(s) for $kpiTiles KPI tile(s) — over half the KPI tiles are chartless. The body was likely rebuilt without sparklines. See template-readme.md \"Sparklines are MANDATORY\"."
} else {
    Pass "KPI tiles have data-spark coverage ($sparkCount/$kpiTiles)"
}
if ($totalCharts -lt 15) {
    Add-Warn "Only $totalCharts chart elements found. Expected ~30+ (KPI sparks + 60d-trend rows + WoW-table rows). Did you forget to add data-trend attributes to the WoW / trend tables?"
} else {
    Pass "Overall chart coverage looks reasonable ($totalCharts elements)"
}

# ---- 7. Traffic-attribution sub-block color diversity (tri-state convention) ----
# Per template-readme.md: each .attr-card's traffic sub-block should be green
# (ruled out), yellow (partly contributing), or red (primary driver). If every
# sub-block is the same color, the author defaulted to one and didn't actually
# classify per card (v7 second-pass regression: 10/10 yellow).
$taGreen  = ([regex]::Matches($content, '\u2713 Traffic attribution \u2014 ruled out')).Count
$taYellow = ([regex]::Matches($content, '\u26a0 Traffic attribution \u2014 partly contributing')).Count
$taRed    = ([regex]::Matches($content, '\ud83d\ude9a Traffic attribution \u2014 primary driver')).Count
$taTotal  = $taGreen + $taYellow + $taRed
if ($taTotal -ge 4) {
    $distinctColors = @($taGreen, $taYellow, $taRed | Where-Object { $_ -gt 0 }).Count
    if ($distinctColors -le 1) {
        Add-Warn "All $taTotal traffic-attribution sub-blocks share one color (g=$taGreen y=$taYellow r=$taRed). The tri-state convention exists so color carries meaning \u2014 verify each card's verdict and recolor accordingly. See template-readme.md \"Traffic-attribution sub-block on each attribution card (tri-state)\"."
    } else {
        Pass "Traffic-attribution color mix: $taGreen green / $taYellow yellow / $taRed red"
    }
}

# ---- 8. Code-attribution depth (8-field structure) ----
# SKILL.md \u00a74 mandates that each .attr-card's "Code attribution" block populates
# Originator + Top throw site + Wrapper + Caller hot-spots + Underlying cause +
# Top error_messages + Likely PRs + Next step. A pr-list-only block is the v7-third-
# pass regression. Heuristic: each `<div class="code-attr-title">Code attribution</div>`
# must be followed (within the same card) by an `origin-label` row.
$codeAttrBlocks = ([regex]::Matches($content, '<div class="code-attr-title">Code attribution</div>')).Count
$originLabels   = ([regex]::Matches($content, 'class="origin-label">Originator')).Count
if ($codeAttrBlocks -ge 1) {
    if ($originLabels -lt $codeAttrBlocks) {
        Add-Fail "$codeAttrBlocks Code-attribution block(s) but only $originLabels have an Originator row. Each card needs the full 8-field structure (Originator / Top throw site / Wrapper / Caller hot-spots / Underlying cause / Top error_messages / Likely PRs / Next step). See assets/code-attribution-template.md."
    } else {
        Pass "All $codeAttrBlocks code-attribution block(s) have full 8-field structure"
    }
}

# Cheap nested-callout heuristic: scan the attention block for any callout that
# opens before the previous callout closes. We approximate by tracking depth.
if ($startIdx -ge 0 -and $endIdx -gt $startIdx) {
    $depthOuter = 0; $nestedAt = @()
    for ($i = $startIdx; $i -le $endIdx; $i++) {
        if ($null -eq $lines[$i]) { continue }
        # Match the callout container itself, not callout-title. The class can be
        # `callout`, `callout urgent`, `callout watch`, `callout win`, etc. — but
        # never `callout-title`. Require a space or end-of-class-attr after.
        if ($lines[$i] -match '<div class="callout(?:\s|")' -and $depthOuter -gt 0) {
            $nestedAt += $i + 1
        }
        $depthOuter += ([regex]::Matches($lines[$i], '<div\b')).Count
        $depthOuter -= ([regex]::Matches($lines[$i], '</div>')).Count
    }
    if ($nestedAt.Count -gt 0) {
        Add-Fail "Nested callout detected at line(s): $($nestedAt -join ', '). Each callout in Section 2 must be a SIBLING, not nested inside another callout."
    } else {
        Pass "No nested callouts in Section 2"
    }
}

Write-Host ""
if ($failures.Count -eq 0) {
    Write-Host "All hard checks passed." -ForegroundColor Green
    if ($warnings.Count -gt 0) { Write-Host "$($warnings.Count) warning(s) — review above." -ForegroundColor Yellow }
    exit 0
} else {
    Write-Host "$($failures.Count) hard check(s) failed. Fix before publishing." -ForegroundColor Red
    exit 1
}
