<#
.SYNOPSIS
    Appends a snapshot of the current analysis run to the persistent history file.

.DESCRIPTION
    Reads final_classification.json from the intermediate data directory,
    computes aggregate statistics, and appends a timestamped snapshot to history.json.
    This enables trend tracking across multiple analysis runs.

.PARAMETER PeriodStart
    Start date of the analysis period (YYYY-MM-DD). This is the -StartDate that was passed
    to analyze.ps1, or the day after the previous run's PeriodEnd.

.PARAMETER PeriodEnd
    End date of the analysis period (YYYY-MM-DD). Defaults to today.

.PARAMETER InputDir
    Directory containing final_classification.json.
    Default: $env:TEMP\copilot-review-analysis

.PARAMETER HistoryFile
    Path to the persistent history JSON file.
    Default: ~/.copilot-review-analysis/history.json

.EXAMPLE
    .\append-history.ps1 -PeriodStart "2026-01-24" -PeriodEnd "2026-03-25"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$PeriodStart,

    [string]$PeriodEnd = (Get-Date -Format "yyyy-MM-dd"),

    [string]$InputDir = "$env:TEMP\copilot-review-analysis",

    [string]$CoverageFile = "",

    [string]$HistoryFile = "$env:USERPROFILE\.copilot-review-analysis\history.json"
)

$ErrorActionPreference = "Stop"

# --- Load data ---
$finalPath = Join-Path $InputDir "final_classification.json"

if (-not (Test-Path $finalPath)) {
    Write-Error "final_classification.json not found at $finalPath. Run Phase 4 first."
    return
}

$data = @(Get-Content $finalPath -Raw | ConvertFrom-Json)
foreach ($item in $data) {
    if (-not $item.ReviewSource) {
        $source = if ($item.Repo -eq "authapp") { "Foundry" } else { "GitHub Copilot" }
        $item | Add-Member -NotePropertyName ReviewSource -NotePropertyValue $source -Force
    }
}

# --- Load coverage (Tier 2.4), if present ---
# coverage.json is emitted by analyze.ps1 and records, per repo, how many merged human PRs
# actually received a Copilot review. Optional so older runs still work.
if (-not $CoverageFile) { $CoverageFile = Join-Path $InputDir "coverage.json" }
$coverage = $null
if (Test-Path $CoverageFile) {
    $coverage = Get-Content $CoverageFile -Raw | ConvertFrom-Json
}

# --- Compute period ---
$startDate = [datetime]::ParseExact($PeriodStart, "yyyy-MM-dd", $null)
$endDate = [datetime]::ParseExact($PeriodEnd, "yyyy-MM-dd", $null)
if ($endDate -lt $startDate) { throw "PeriodEnd must not be before PeriodStart." }
$periodDays = ($endDate - $startDate).Days + 1
$periodWeeks = $periodDays / 7

# --- Compute overall stats (canonical taxonomy) ---
# final_classification.json now carries a fully-resolved canonical Verdict per comment
# (helpful | declined | incorrect | unresolved | unknown) with silent-adoption promotions
# ALREADY applied by final-classification.ps1. We count those verdicts directly rather than
# re-deriving silent-helpful from precise.json (which double-counted before).
$total = $data.Count
$replied = ($data | Where-Object { $_.Replied -eq $true }).Count

$confirmedHelpful             = ($data | Where-Object { $_.Verdict -eq "helpful" }).Count
$confirmedNotHelpfulIncorrect = ($data | Where-Object { $_.Verdict -eq "incorrect" }).Count
$declinedCount                = ($data | Where-Object { $_.Verdict -eq "declined" }).Count
$unresolved                   = ($data | Where-Object { $_.Verdict -eq "unresolved" }).Count
$unknownCount                 = ($data | Where-Object { $_.Verdict -eq "unknown" }).Count
$repliedHelpful               = ($data | Where-Object { $_.Replied -eq $true -and $_.Verdict -eq "helpful" }).Count
$silentHelpful                = ($data | Where-Object { $_.Replied -eq $false -and $_.Verdict -eq "helpful" }).Count
$promotedBySignal             = ($data | Where-Object { $_.PromotedBySignal }).Count
# Combined dismissals — kept for trend continuity with prior snapshots (which pre-date the declined split)
$confirmedNotHelpful = $confirmedNotHelpfulIncorrect + $declinedCount

$responseRate = if ($total -gt 0) { [math]::Round(($replied / $total) * 100, 1) } else { 0 }
$helpfulPct = if ($total -gt 0) { [math]::Round(($confirmedHelpful / $total) * 100, 1) } else { 0 }
$notHelpfulPct = if ($total -gt 0) { [math]::Round(($confirmedNotHelpful / $total) * 100, 1) } else { 0 }
$notHelpfulIncorrectPct = if ($total -gt 0) { [math]::Round(($confirmedNotHelpfulIncorrect / $total) * 100, 1) } else { 0 }
$declinedPct = if ($total -gt 0) { [math]::Round(($declinedCount / $total) * 100, 1) } else { 0 }
$unresolvedPct = if ($total -gt 0) { [math]::Round(($unresolved / $total) * 100, 1) } else { 0 }
$unknownPct = if ($total -gt 0) { [math]::Round(($unknownCount / $total) * 100, 1) } else { 0 }
$repliedHelpfulRate = if ($replied -gt 0) { [math]::Round(($repliedHelpful / $replied) * 100, 1) } else { 0 }
# Copilot precision: when Copilot was evaluable for correctness (helpful + genuinely incorrect), how often it was correct
$precisionDenom = $confirmedHelpful + $confirmedNotHelpfulIncorrect
$precision = if ($precisionDenom -gt 0) { [math]::Round(($confirmedHelpful / $precisionDenom) * 100, 1) } else { $null }
$commentsPerWeek = if ($periodWeeks -gt 0) { [math]::Round($total / $periodWeeks, 1) } else { $total }

# Count unique PRs
$humanPRs = ($data | Select-Object -Property Repo, PRNumber -Unique | Group-Object Repo | Measure-Object -Property Count -Sum).Sum
$prsWithInlineFeedback = $humanPRs
$reviewedPRs = if ($coverage -and $coverage.overall) {
    if ($null -ne $coverage.overall.reviewedByAI) { $coverage.overall.reviewedByAI }
    else { $coverage.overall.reviewedByCopilot }
} else { $prsWithInlineFeedback }
$avgCommentsPerPR = if ($reviewedPRs -gt 0) { [math]::Round($total / $reviewedPRs, 1) } else { 0 }

# --- Per-repo stats (canonical taxonomy) ---
$repoStats = @{}
foreach ($repoGroup in ($data | Group-Object Repo)) {
    $repoName = $repoGroup.Name
    $rTotal = $repoGroup.Count
    $rReplied = ($repoGroup.Group | Where-Object { $_.Replied -eq $true }).Count
    $rConfH = ($repoGroup.Group | Where-Object { $_.Verdict -eq "helpful" }).Count
    $rRepliedNH = ($repoGroup.Group | Where-Object { $_.Verdict -eq "incorrect" }).Count
    $rDeclined = ($repoGroup.Group | Where-Object { $_.Verdict -eq "declined" }).Count
    $rUnresolved = ($repoGroup.Group | Where-Object { $_.Verdict -eq "unresolved" }).Count
    $rConfNH = $rRepliedNH + $rDeclined   # combined dismissals for trend continuity

    $repoStats[$repoName] = @{
        reviewSource         = $repoGroup.Group[0].ReviewSource
        comments             = $rTotal
        responseRate         = if ($rTotal -gt 0) { [math]::Round(($rReplied / $rTotal) * 100, 1) } else { 0 }
        helpfulPct           = if ($rTotal -gt 0) { [math]::Round(($rConfH / $rTotal) * 100, 1) } else { 0 }
        notHelpfulPct        = if ($rTotal -gt 0) { [math]::Round(($rConfNH / $rTotal) * 100, 1) } else { 0 }
        notHelpfulIncorrectPct = if ($rTotal -gt 0) { [math]::Round(($rRepliedNH / $rTotal) * 100, 1) } else { 0 }
        declinedPct          = if ($rTotal -gt 0) { [math]::Round(($rDeclined / $rTotal) * 100, 1) } else { 0 }
        unresolvedPct        = if ($rTotal -gt 0) { [math]::Round(($rUnresolved / $rTotal) * 100, 1) } else { 0 }
    }
}

# --- Per-source stats: never attribute Foundry outcomes to GitHub Copilot ---
$sourceStats = @{}
$sourceNames = @($data.ReviewSource)
if ($coverage -and $coverage.perRepo) { $sourceNames += @($coverage.perRepo.reviewSource) }
foreach ($source in @($sourceNames | Where-Object { $_ } | Select-Object -Unique)) {
    $comments = @($data | Where-Object { $_.ReviewSource -eq $source })
    $count = $comments.Count
    $sourceHelpful = @($comments | Where-Object Verdict -eq "helpful").Count
    $sourceIncorrect = @($comments | Where-Object Verdict -eq "incorrect").Count
    $sourceDeclined = @($comments | Where-Object Verdict -eq "declined").Count
    $sourceUnresolved = @($comments | Where-Object Verdict -eq "unresolved").Count
    $sourceUnknown = @($comments | Where-Object Verdict -eq "unknown").Count
    $sourceReplied = @($comments | Where-Object Replied -eq $true).Count
    $sourceStats[$source] = [ordered]@{
        comments = $count
        commentsPerWeek = [math]::Round($count / $periodWeeks, 1)
        responseRate = if ($count -gt 0) { [math]::Round($sourceReplied / $count * 100, 1) } else { 0 }
        helpfulPct = if ($count -gt 0) { [math]::Round($sourceHelpful / $count * 100, 1) } else { 0 }
        declinedPct = if ($count -gt 0) { [math]::Round($sourceDeclined / $count * 100, 1) } else { 0 }
        incorrectPct = if ($count -gt 0) { [math]::Round($sourceIncorrect / $count * 100, 1) } else { 0 }
        unresolvedPct = if ($count -gt 0) { [math]::Round($sourceUnresolved / $count * 100, 1) } else { 0 }
        unknownPct = if ($count -gt 0) { [math]::Round($sourceUnknown / $count * 100, 1) } else { 0 }
        helpful = $sourceHelpful; incorrect = $sourceIncorrect; declined = $sourceDeclined
        unresolved = $sourceUnresolved; unknown = $sourceUnknown
        precision = if ($sourceHelpful + $sourceIncorrect -gt 0) {
            [math]::Round($sourceHelpful / ($sourceHelpful + $sourceIncorrect) * 100, 1)
        } else { $null }
        precisionDenominator = $sourceHelpful + $sourceIncorrect
        repos = if ($coverage -and $coverage.perRepo) {
            @($coverage.perRepo | Where-Object { $_.reviewSource -eq $source } | Select-Object -ExpandProperty repo -Unique)
        } else { @($comments.Repo | Select-Object -Unique) }
        coverage = @($coverage.perRepo | Where-Object { $_.reviewSource -eq $source })
    }
}

# --- Per-engineer stats (canonical taxonomy) ---
$engineerStats = @{}
foreach ($engGroup in ($data | Group-Object Engineer)) {
    $eName = $engGroup.Name
    $eTotal = $engGroup.Count
    $eReplied = ($engGroup.Group | Where-Object { $_.Replied -eq $true }).Count
    $eConfH = ($engGroup.Group | Where-Object { $_.Verdict -eq "helpful" }).Count
    $eDeclined = ($engGroup.Group | Where-Object { $_.Verdict -eq "declined" }).Count

    $engineerStats[$eName] = @{
        comments     = $eTotal
        responseRate = if ($eTotal -gt 0) { [math]::Round(($eReplied / $eTotal) * 100, 1) } else { 0 }
        helpfulPct   = if ($eTotal -gt 0) { [math]::Round(($eConfH / $eTotal) * 100, 1) } else { 0 }
        declinedPct  = if ($eTotal -gt 0) { [math]::Round(($eDeclined / $eTotal) * 100, 1) } else { 0 }
    }
}

# --- Coverage block for the snapshot (Tier 2.4) ---
# coverage.json schema (from analyze.ps1): { perRepo:[{repo,mergedHumanPRs,reviewedByCopilot,
# withInlineFeedback,noFeedback,reviewCoveragePct}], overall:{...same fields...} }
$coverageBlock = $null
if ($coverage -and $coverage.overall) {
    $coverageBlock = [ordered]@{
        overallPct     = $coverage.overall.reviewCoveragePct
        reviewedPRs    = if ($null -ne $coverage.overall.reviewedByAI) {
            $coverage.overall.reviewedByAI
        } else { $coverage.overall.reviewedByCopilot }
        mergedHumanPRs = $coverage.overall.mergedHumanPRs
        withFeedback   = $coverage.overall.withInlineFeedback
        noFeedback     = $coverage.overall.noFeedback
        repos          = $coverage.perRepo
        sources        = $coverage.perSource
    }
}

# --- Build snapshot ---
$snapshot = [ordered]@{
    schemaVersion      = 2
    reviewSources      = @($sourceStats.Keys | Sort-Object)
    repositories       = if ($coverage -and $coverage.perRepo) {
        @($coverage.perRepo.repo | Sort-Object -Unique)
    } else { @($data.Repo | Sort-Object -Unique) }
    runDate            = (Get-Date -Format "yyyy-MM-dd")
    periodStart        = $PeriodStart
    periodEnd          = $PeriodEnd
    periodDays         = $periodDays
    total              = $total
    commentsPerWeek    = $commentsPerWeek
    reviewedPRs        = $reviewedPRs
    prsWithInlineFeedback = $prsWithInlineFeedback
    avgCommentsPerPR   = $avgCommentsPerPR
    responseRate       = $responseRate
    helpful            = [ordered]@{ count = $confirmedHelpful; pct = $helpfulPct }
    notHelpful         = [ordered]@{ count = $confirmedNotHelpful; pct = $notHelpfulPct }
    notHelpfulIncorrect = [ordered]@{ count = $confirmedNotHelpfulIncorrect; pct = $notHelpfulIncorrectPct }
    declined           = [ordered]@{ count = $declinedCount; pct = $declinedPct }
    unresolved         = [ordered]@{ count = $unresolved; pct = $unresolvedPct }
    unknown            = [ordered]@{ count = $unknownCount; pct = $unknownPct }
    precision          = $precision
    precisionDenominator = $precisionDenom
    repliedHelpfulRate = $repliedHelpfulRate
    silentAdoptions    = [ordered]@{ count = $silentHelpful; promotedBySignal = $promotedBySignal }
    coverage           = $coverageBlock
    repos              = $repoStats
    sources            = $sourceStats
    engineers          = $engineerStats
}

# --- Self-consistency: canonical additive buckets must reconcile to total ---
# (helpful + incorrect + declined + unresolved + unknown). notHelpful is a derived
# combined bucket (incorrect + declined), so it is intentionally excluded here.
# Mirrors the gate in final-classification.ps1 so history stays consistent with the
# classification output — a nonzero unknown that was dropped would silently under-sum.
$bucketSum = $confirmedHelpful + $confirmedNotHelpfulIncorrect + $declinedCount + $unresolved + $unknownCount
if ($bucketSum -ne $total) {
    throw "Snapshot buckets ($bucketSum) do not reconcile to total ($total). Check for verdicts outside the canonical taxonomy."
}
$sourceTotal = ($sourceStats.Values | ForEach-Object { $_.comments } | Measure-Object -Sum).Sum
if ($total -gt 0 -and $sourceTotal -ne $total) { throw "Per-source snapshot totals do not reconcile to $total." }

# --- Load or create history ---
$historyDir = Split-Path $HistoryFile -Parent
if (-not (Test-Path $historyDir)) {
    New-Item -ItemType Directory -Path $historyDir -Force | Out-Null
}

$history = @()
if (Test-Path $HistoryFile) {
    $existing = Get-Content $HistoryFile -Raw | ConvertFrom-Json
    if ($existing -is [array]) {
        $history = [System.Collections.ArrayList]@($existing)
    }
    else {
        $history = [System.Collections.ArrayList]@($existing)
    }
}
else {
    $history = [System.Collections.ArrayList]::new()
}

# Check for duplicate run (same periodStart + periodEnd)
$duplicate = $history | Where-Object { $_.periodStart -eq $PeriodStart -and $_.periodEnd -eq $PeriodEnd }
if ($duplicate) {
    Write-Host "Replacing existing entry for period $PeriodStart to $PeriodEnd"
    $history = [System.Collections.ArrayList]@($history | Where-Object { -not ($_.periodStart -eq $PeriodStart -and $_.periodEnd -eq $PeriodEnd) })
}

# Append
$history.Add($snapshot) | Out-Null

# Sort by periodStart descending (newest first)
$sorted = $history | Sort-Object { [datetime]::ParseExact($_.periodStart, "yyyy-MM-dd", $null) } -Descending

# Save
ConvertTo-Json -InputObject @($sorted) -Depth 12 | Set-Content $HistoryFile -Encoding UTF8

Write-Host ""
Write-Host "================================================================"
Write-Host "     HISTORY SNAPSHOT APPENDED"
Write-Host "================================================================"
Write-Host "  Period: $PeriodStart to $PeriodEnd ($periodDays days)"
Write-Host "  Comments: $total ($commentsPerWeek/week)"
Write-Host "  Response Rate: $responseRate%"
Write-Host "  Helpful: $helpfulPct% ($confirmedHelpful)  [silent adoptions: $silentHelpful, signal-promoted: $promotedBySignal]"
Write-Host "  Declined: $declinedPct% ($declinedCount)"
Write-Host "  Incorrect: $notHelpfulIncorrectPct% ($confirmedNotHelpfulIncorrect)"
Write-Host "  Unresolved: $unresolvedPct% ($unresolved)"
Write-Host "  Unknown: $unknownPct% ($unknownCount)"
Write-Host ("  Precision: " + $(if ($null -ne $precision) { "$precision%" } else { "n/a" }))
if ($coverageBlock) { Write-Host "  Coverage: $($coverageBlock.overallPct)% ($($coverageBlock.reviewedPRs)/$($coverageBlock.mergedHumanPRs) merged human PRs reviewed)" }
Write-Host "  History entries: $($sorted.Count)"
Write-Host "  Saved to: $HistoryFile"
Write-Host "================================================================"
