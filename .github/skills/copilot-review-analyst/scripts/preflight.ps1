<#
.SYNOPSIS
    Phase 0 preflight: verify the authenticated token can actually SEE Copilot reviews in
    every target repo BEFORE the (expensive) Phase 1 collection runs.

.DESCRIPTION
    The single most dangerous failure mode of this analysis is a *silent-zero repo*: the token
    (typically an EMU account) lacks visibility into one repo — most often the private broker —
    so Phase 1 returns zero Copilot comments for it, and the final report silently under-counts
    or omits that repo without any error. A 60% helpfulness number computed over 2 of 3 repos is
    worse than no number at all.

    This preflight, borrowed in spirit from the peer teams' "coverage audit" step, hits each repo
    with three cheap probes and writes coverage_audit.json:
      (1) repo readable          — GET repos/{slug}
      (2) PR list non-empty      — can we enumerate merged PRs in-window
      (3) Copilot reviews visible — do recent merged human PRs actually surface Copilot reviews

    Probe (3) is the crucial one. A repo that is readable and has merged PRs but surfaces ZERO
    Copilot reviews across a healthy sample is flagged SILENT-ZERO RISK and preflight exits
    non-zero so the operator stops and fixes access instead of shipping a skewed report.

.PARAMETER OutputDir
    Where to write coverage_audit.json. Default: $env:TEMP\copilot-review-analysis
.PARAMETER StartDate / .PARAMETER EndDate
    The analysis window (YYYY-MM-DD). Mirrors analyze.ps1 so the probe uses the same corpus.
.PARAMETER SampleSize
    How many recent merged human PRs to probe per repo for Copilot-review visibility. Default 8.

.EXAMPLE
    .\preflight.ps1 -StartDate "2026-06-10" -EndDate "2026-07-15"
#>

param(
    [string]$OutputDir = "$env:TEMP\copilot-review-analysis",
    [string]$StartDate = (Get-Date).AddDays(-60).ToString("yyyy-MM-dd"),
    [string]$EndDate = (Get-Date).ToString("yyyy-MM-dd"),
    [int]$SampleSize = 8
)

$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

# ========================================
# AUTH: common/msal live on github.com; broker lives on msft.ghe.com.
# Identical logic to analyze.ps1 so preflight validates the SAME identities the run will use.
# ========================================
$originalAccount = $null

if (-not (gh auth status --hostname github.com 2>&1 | Select-String 'Logged in')) {
    Write-Host "ERROR: not logged in to github.com." -ForegroundColor Red
    Write-Host "  Run: gh auth login --hostname github.com" -ForegroundColor Yellow
    exit 1
}
if (-not (gh auth status --hostname msft.ghe.com 2>&1 | Select-String 'Logged in')) {
    Write-Host "ERROR: not logged in to msft.ghe.com (required for the broker repo)." -ForegroundColor Red
    Write-Host "  Run: gh auth login --hostname msft.ghe.com" -ForegroundColor Yellow
    exit 1
}

$COPILOT_USERS = @("Copilot", "copilot-pull-request-reviewer[bot]")
$BOT_AUTHORS = @("app/copilot-swe-agent", "Copilot", "dependabot[bot]", "github-actions[bot]")

$repos = @(
    @{ Label = "common"; Slug = "AzureAD/microsoft-authentication-library-common-for-android"; PrRepo = "AzureAD/microsoft-authentication-library-common-for-android"; ApiRepo = "https://api.github.com/repos/AzureAD/microsoft-authentication-library-common-for-android" },
    @{ Label = "msal";   Slug = "AzureAD/microsoft-authentication-library-for-android";        PrRepo = "AzureAD/microsoft-authentication-library-for-android";        ApiRepo = "https://api.github.com/repos/AzureAD/microsoft-authentication-library-for-android" },
    @{ Label = "broker"; Slug = "security/ad-accounts-for-android";                            PrRepo = "msft.ghe.com/security/ad-accounts-for-android";                ApiRepo = "https://msft.ghe.com/api/v3/repos/security/ad-accounts-for-android" }
)

Write-Host ""
Write-Host "================================================================"
Write-Host " PHASE 0 PREFLIGHT  (account: $emuAccount)"
Write-Host " Window: $StartDate .. $EndDate   Sample: $SampleSize PRs/repo"
Write-Host "================================================================"

$audit = @()
$hardFail = $false
$silentZeroRisk = $false

foreach ($r in $repos) {
    $label = $r.Label; $slug = $r.Slug; $apiRepo = $r.ApiRepo
    $repoReadable = $false
    $prListable = $false
    $mergedInWindow = 0
    $sampledPRs = 0
    $prsWithCopilotReview = 0
    $notes = @()

    # (1) repo readable?
    $repoJson = gh api "$apiRepo" 2>$null
    if ($LASTEXITCODE -eq 0 -and $repoJson) {
        $repoReadable = $true
    } else {
        $notes += "repo NOT readable (404/403) — token lacks access"
    }

    # (2) merged human PRs in-window?
    $prs = @()
    if ($repoReadable) {
        $prJson = gh pr list --repo $r.PrRepo --state merged --search "merged:$StartDate..$EndDate" `
            --limit 200 --json number,author,mergedAt 2>$null
        if ($LASTEXITCODE -eq 0 -and $prJson) {
            try { $prs = @($prJson | ConvertFrom-Json) } catch { $prs = @() }
            $prListable = $true
            $humanPRs = @($prs | Where-Object { $_.author.login -notin $BOT_AUTHORS })
            $mergedInWindow = $humanPRs.Count
        } else {
            $notes += "PR list failed or empty"
        }
    }

    # (3) can we actually SEE Copilot reviews on recent merged human PRs? (silent-zero guard)
    if ($prListable -and $mergedInWindow -gt 0) {
        $sample = @($prs | Where-Object { $_.author.login -notin $BOT_AUTHORS } |
            Sort-Object { $_.mergedAt } -Descending | Select-Object -First $SampleSize)
        $sampledPRs = $sample.Count
        foreach ($pr in $sample) {
            $reviews = gh api "$apiRepo/pulls/$($pr.number)/reviews" --paginate 2>$null | ConvertFrom-Json
            if ($reviews) {
                $hasCopilot = $reviews | Where-Object { $_.user.login -in $COPILOT_USERS }
                if ($hasCopilot) { $prsWithCopilotReview++ }
            }
        }
        if ($prsWithCopilotReview -eq 0) {
            $notes += "SILENT-ZERO RISK: $sampledPRs recent merged PRs sampled, ZERO surfaced a Copilot review"
            $silentZeroRisk = $true
        }
    } elseif ($repoReadable -and $mergedInWindow -eq 0) {
        $notes += "no merged human PRs in window (repo may simply be quiet — verify manually)"
    }

    if (-not $repoReadable) { $hardFail = $true }

    $status = if (-not $repoReadable) { "FAIL" }
              elseif ($prsWithCopilotReview -eq 0 -and $sampledPRs -gt 0) { "SILENT-ZERO-RISK" }
              elseif ($mergedInWindow -eq 0) { "EMPTY-WINDOW" }
              else { "OK" }

    $sampleVisibilityPct = if ($sampledPRs -gt 0) { [math]::Round($prsWithCopilotReview / $sampledPRs * 100, 1) } else { 0 }

    $audit += [PSCustomObject]@{
        repo                 = $label
        slug                 = $slug
        status               = $status
        repoReadable         = $repoReadable
        prListable           = $prListable
        mergedHumanPRsInWindow = $mergedInWindow
        sampledPRs           = $sampledPRs
        sampledWithCopilotReview = $prsWithCopilotReview
        sampleVisibilityPct  = $sampleVisibilityPct
        notes                = $notes
    }

    $color = switch ($status) { "OK" {"Green"} "EMPTY-WINDOW" {"Yellow"} default {"Red"} }
    Write-Host ("  [{0,-16}] {1,-7} readable={2} merged={3} sample={4}/{5} copilot-visible" -f `
        $status, $label, $repoReadable, $mergedInWindow, $prsWithCopilotReview, $sampledPRs) -ForegroundColor $color
    foreach ($n in $notes) { Write-Host "        - $n" -ForegroundColor $color }
}

$auditObj = [PSCustomObject]@{
    generatedAt = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    window      = @{ start = $StartDate; end = $EndDate }
    sampleSize  = $SampleSize
    repos       = $audit
    hardFail    = $hardFail
    silentZeroRisk = $silentZeroRisk
}
$auditPath = Join-Path $OutputDir "coverage_audit.json"
$auditObj | ConvertTo-Json -Depth 6 | Out-File $auditPath -Encoding utf8

Write-Host ""
Write-Host "  coverage_audit.json -> $auditPath"
Write-Host "================================================================"

if ($hardFail) {
    Write-Host " PREFLIGHT FAILED: at least one repo is unreadable. Fix token/repo access before" -ForegroundColor Red
    Write-Host " running analyze.ps1 — otherwise the report silently omits that repo." -ForegroundColor Red
    exit 2
}
if ($silentZeroRisk) {
    Write-Host " PREFLIGHT WARNING: a readable repo surfaced ZERO Copilot reviews across the sample." -ForegroundColor Red
    Write-Host " This is the classic silent-zero-repo trap. Confirm Copilot is enabled AND the token" -ForegroundColor Red
    Write-Host " can see its reviews there before trusting any aggregate number. Exiting non-zero." -ForegroundColor Red
    exit 3
}
Write-Host " PREFLIGHT PASSED: all repos readable and surfacing Copilot reviews. Safe to run Phase 1." -ForegroundColor Green
exit 0
