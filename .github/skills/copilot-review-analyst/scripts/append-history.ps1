<#
.SYNOPSIS
    Appends a snapshot of the current analysis run to the persistent history file.

.DESCRIPTION
    Reads final_classification.json and precise.json from the intermediate data directory,
    computes aggregate statistics, and appends a timestamped snapshot to history.json.
    This enables trend tracking across multiple analysis runs.

.PARAMETER PeriodStart
    Start date of the analysis period (YYYY-MM-DD). This is the -StartDate that was passed
    to analyze.ps1, or the day after the previous run's PeriodEnd.

.PARAMETER PeriodEnd
    End date of the analysis period (YYYY-MM-DD). Defaults to today.

.PARAMETER InputDir
    Directory containing final_classification.json and precise.json.
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

    [string]$HistoryFile = "$env:USERPROFILE\.copilot-review-analysis\history.json"
)

$ErrorActionPreference = "Stop"

# --- Load data ---
$finalPath = Join-Path $InputDir "final_classification.json"
$precisePath = Join-Path $InputDir "precise.json"

if (-not (Test-Path $finalPath)) {
    Write-Error "final_classification.json not found at $finalPath. Run Phase 4 first."
    return
}

$data = Get-Content $finalPath -Raw | ConvertFrom-Json
$precise = @()
if (Test-Path $precisePath) {
    $precise = Get-Content $precisePath -Raw | ConvertFrom-Json
}

# --- Compute period ---
$startDate = [datetime]::ParseExact($PeriodStart, "yyyy-MM-dd", $null)
$endDate = [datetime]::ParseExact($PeriodEnd, "yyyy-MM-dd", $null)
$periodDays = ($endDate - $startDate).Days
$periodWeeks = [math]::Round($periodDays / 7, 1)

# --- Compute overall stats ---
$total = $data.Count
$replied = ($data | Where-Object { $_.Replied -eq $true }).Count
$helpful = ($data | Where-Object { $_.Verdict -eq "helpful" }).Count
$notHelpful = ($data | Where-Object { $_.Verdict -eq "not-helpful" }).Count

# Three-way breakdown: compute unresolved from no-reply comments without diff evidence
$preciseMap = @{}
foreach ($p in $precise) { $preciseMap["$($p.CommentId)"] = $p.Verdict }
$silentComments = $data | Where-Object { $_.Replied -eq $false }
$silentHelpful = 0
foreach ($s in $silentComments) {
    $dv = $preciseMap["$($s.CommentId)"]
    if ($dv -in @("suggestion-applied", "suggestion-likely-applied", "exact-lines-modified", "lines-modified-different-fix")) {
        $silentHelpful++
    }
}
$repliedHelpful = ($data | Where-Object { $_.Replied -eq $true -and $_.Verdict -eq "helpful" }).Count
$confirmedHelpful = $repliedHelpful + $silentHelpful
$confirmedNotHelpful = ($data | Where-Object { $_.Replied -eq $true -and $_.Verdict -eq "not-helpful" }).Count
$unresolved = $total - $confirmedHelpful - $confirmedNotHelpful

$responseRate = if ($total -gt 0) { [math]::Round(($replied / $total) * 100, 1) } else { 0 }
$helpfulPct = if ($total -gt 0) { [math]::Round(($confirmedHelpful / $total) * 100, 1) } else { 0 }
$notHelpfulPct = if ($total -gt 0) { [math]::Round(($confirmedNotHelpful / $total) * 100, 1) } else { 0 }
$unresolvedPct = if ($total -gt 0) { [math]::Round(($unresolved / $total) * 100, 1) } else { 0 }
$repliedHelpfulRate = if ($replied -gt 0) { [math]::Round(($repliedHelpful / $replied) * 100, 1) } else { 0 }
$commentsPerWeek = if ($periodWeeks -gt 0) { [math]::Round($total / $periodWeeks, 1) } else { $total }

# Count unique PRs
$humanPRs = ($data | Select-Object -Property Repo, PRNumber -Unique | Group-Object Repo | Measure-Object -Property Count -Sum).Sum
$reviewedPRs = $humanPRs  # All PRs in final_classification had Copilot comments
$avgCommentsPerPR = if ($reviewedPRs -gt 0) { [math]::Round($total / $reviewedPRs, 1) } else { 0 }

# --- Per-repo stats ---
$repoStats = @{}
foreach ($repoGroup in ($data | Group-Object Repo)) {
    $repoName = $repoGroup.Name
    $rTotal = $repoGroup.Count
    $rReplied = ($repoGroup.Group | Where-Object { $_.Replied -eq $true }).Count
    $rRepliedH = ($repoGroup.Group | Where-Object { $_.Replied -eq $true -and $_.Verdict -eq "helpful" }).Count
    $rRepliedNH = ($repoGroup.Group | Where-Object { $_.Replied -eq $true -and $_.Verdict -eq "not-helpful" }).Count

    # Silent helpful for this repo
    $rSilentH = 0
    $rSilent = $repoGroup.Group | Where-Object { $_.Replied -eq $false }
    foreach ($s in $rSilent) {
        $dv = $preciseMap["$($s.CommentId)"]
        if ($dv -in @("suggestion-applied", "suggestion-likely-applied", "exact-lines-modified", "lines-modified-different-fix")) {
            $rSilentH++
        }
    }

    $rConfH = $rRepliedH + $rSilentH
    $rConfNH = $rRepliedNH
    $rUnresolved = $rTotal - $rConfH - $rConfNH

    $repoStats[$repoName] = @{
        comments      = $rTotal
        responseRate  = if ($rTotal -gt 0) { [math]::Round(($rReplied / $rTotal) * 100, 1) } else { 0 }
        helpfulPct    = if ($rTotal -gt 0) { [math]::Round(($rConfH / $rTotal) * 100, 1) } else { 0 }
        notHelpfulPct = if ($rTotal -gt 0) { [math]::Round(($rConfNH / $rTotal) * 100, 1) } else { 0 }
        unresolvedPct = if ($rTotal -gt 0) { [math]::Round(($rUnresolved / $rTotal) * 100, 1) } else { 0 }
    }
}

# --- Per-engineer stats ---
$engineerStats = @{}
foreach ($engGroup in ($data | Group-Object Engineer)) {
    $eName = $engGroup.Name
    $eTotal = $engGroup.Count
    $eReplied = ($engGroup.Group | Where-Object { $_.Replied -eq $true }).Count
    $eRepliedH = ($engGroup.Group | Where-Object { $_.Replied -eq $true -and $_.Verdict -eq "helpful" }).Count

    # Silent helpful for this engineer
    $eSilentH = 0
    $eSilent = $engGroup.Group | Where-Object { $_.Replied -eq $false }
    foreach ($s in $eSilent) {
        $dv = $preciseMap["$($s.CommentId)"]
        if ($dv -in @("suggestion-applied", "suggestion-likely-applied", "exact-lines-modified", "lines-modified-different-fix")) {
            $eSilentH++
        }
    }

    $eConfH = $eRepliedH + $eSilentH

    $engineerStats[$eName] = @{
        comments     = $eTotal
        responseRate = if ($eTotal -gt 0) { [math]::Round(($eReplied / $eTotal) * 100, 1) } else { 0 }
        helpfulPct   = if ($eTotal -gt 0) { [math]::Round(($eConfH / $eTotal) * 100, 1) } else { 0 }
    }
}

# --- Build snapshot ---
$snapshot = [ordered]@{
    runDate            = (Get-Date -Format "yyyy-MM-dd")
    periodStart        = $PeriodStart
    periodEnd          = $PeriodEnd
    periodDays         = $periodDays
    total              = $total
    commentsPerWeek    = $commentsPerWeek
    reviewedPRs        = $reviewedPRs
    avgCommentsPerPR   = $avgCommentsPerPR
    responseRate       = $responseRate
    helpful            = [ordered]@{ count = $confirmedHelpful; pct = $helpfulPct }
    notHelpful         = [ordered]@{ count = $confirmedNotHelpful; pct = $notHelpfulPct }
    unresolved         = [ordered]@{ count = $unresolved; pct = $unresolvedPct }
    repliedHelpfulRate = $repliedHelpfulRate
    repos              = $repoStats
    engineers          = $engineerStats
}

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
$sorted | ConvertTo-Json -Depth 5 | Set-Content $HistoryFile -Encoding UTF8

Write-Host ""
Write-Host "================================================================"
Write-Host "     HISTORY SNAPSHOT APPENDED"
Write-Host "================================================================"
Write-Host "  Period: $PeriodStart to $PeriodEnd ($periodDays days)"
Write-Host "  Comments: $total ($commentsPerWeek/week)"
Write-Host "  Response Rate: $responseRate%"
Write-Host "  Helpful: $helpfulPct% ($confirmedHelpful)"
Write-Host "  Not Helpful: $notHelpfulPct% ($confirmedNotHelpful)"
Write-Host "  Unresolved: $unresolvedPct% ($unresolved)"
Write-Host "  History entries: $($sorted.Count)"
Write-Host "  Saved to: $HistoryFile"
Write-Host "================================================================"
