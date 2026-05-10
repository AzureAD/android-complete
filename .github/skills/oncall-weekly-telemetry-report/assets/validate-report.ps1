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
