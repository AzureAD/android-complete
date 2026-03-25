<#
.SYNOPSIS
    Final classification of all Copilot review comments.
    Merges Phase 3 AI verdicts (for replied comments) with Phase 2 diff verdicts
    (for no-reply comments). Maps GitHub accounts to display names.
    Produces authoritative per-engineer and per-repo statistics.

.PARAMETER OutputDir
    Directory containing raw_results.json, precise.json, and reply-verdicts.json.

.PARAMETER AccountMapFile
    Path to JSON file mapping GitHub logins to display names.
    Format: { "github_login": "DisplayName", ... }
    If not provided, uses PR author login as-is.

.PARAMETER ReplyVerdictsFile
    Path to JSON file with AI verdicts for replied comments (Phase 3 output).
    Format: { "commentId": "helpful"|"not-helpful", ... }
    Keys are comment IDs (as strings), values are verdicts.
    If not provided, all replied comments default to "unknown".

.PARAMETER ReauditFlipsFile
    Path to JSON file with re-audit flips for no-reply comments (Phase 3 output).
    Format: { "reauditFlipKeys": ["repo/prNum/filePattern", ...] }
    If not provided, file-changed-elsewhere/no-line-info default to "not-helpful".
#>

param(
    [string]$OutputDir = "$env:TEMP\copilot-review-analysis",
    [string]$AccountMapFile = "",
    [string]$ReplyVerdictsFile = "",
    [string]$ReauditFlipsFile = ""
)

$rawData = Get-Content "$OutputDir\raw_results.json" | ConvertFrom-Json
$preciseData = Get-Content "$OutputDir\precise.json" | ConvertFrom-Json

# ========================================
# LOAD EXTERNAL CONFIGURATION
# ========================================

# Account mapping: GitHub login -> display name
$accountMap = @{}
if ($AccountMapFile -and (Test-Path $AccountMapFile)) {
    $mapRaw = Get-Content $AccountMapFile -Raw | ConvertFrom-Json
    foreach ($prop in $mapRaw.PSObject.Properties) {
        $accountMap[$prop.Name] = $prop.Value
    }
    Write-Host "Loaded account map: $($accountMap.Count) entries" -ForegroundColor Cyan
} else {
    Write-Host "No account map file provided — using raw GitHub logins" -ForegroundColor Yellow
}

# Phase 3 AI verdicts for replied comments: { "commentId": "helpful"|"not-helpful" }
$replyVerdicts = @{}
if ($ReplyVerdictsFile -and (Test-Path $ReplyVerdictsFile)) {
    $verdictsRaw = Get-Content $ReplyVerdictsFile -Raw | ConvertFrom-Json
    foreach ($prop in $verdictsRaw.PSObject.Properties) {
        $replyVerdicts[$prop.Name] = $prop.Value
    }
    Write-Host "Loaded reply verdicts: $($replyVerdicts.Count) entries" -ForegroundColor Cyan
} else {
    Write-Host "No reply verdicts file provided — replied comments will be 'unknown'" -ForegroundColor Yellow
}

# Phase 3 re-audit flips for no-reply comments
$reauditFlipKeys = @()
if ($ReauditFlipsFile -and (Test-Path $ReauditFlipsFile)) {
    $flipsRaw = Get-Content $ReauditFlipsFile -Raw | ConvertFrom-Json
    if ($flipsRaw.reauditFlipKeys) {
        $reauditFlipKeys = @($flipsRaw.reauditFlipKeys)
    }
    Write-Host "Loaded re-audit flips: $($reauditFlipKeys.Count) entries" -ForegroundColor Cyan
} else {
    Write-Host "No re-audit flips file provided — file-changed-elsewhere defaults to 'not-helpful'" -ForegroundColor Yellow
}

# ========================================
# RE-AUDIT FLIP FUNCTION
# ========================================
function Test-ReauditFlip($repo, $prNum, $filePath) {
    foreach ($key in $script:reauditFlipKeys) {
        $parts = $key -split "/"
        $keyRepo = $parts[0]
        $keyPR = $parts[1]
        $keyFile = $parts[2]
        if ($repo -eq $keyRepo -and "$prNum" -eq $keyPR -and $filePath -match [regex]::Escape($keyFile)) {
            return $true
        }
    }
    return $false
}

# ========================================
# CLASSIFY EVERY COMMENT
# ========================================
$finalResults = @()

foreach ($item in $rawData) {
    $prAuthor = $item.PRAuthor
    $engineer = if ($accountMap.ContainsKey($prAuthor)) { $accountMap[$prAuthor] } else { $prAuthor }
    $commentId = $item.CommentId
    $repo = $item.Repo
    $prNum = $item.PRNumber
    $filePath = $item.FilePath
    $replied = $item.HasReply -eq $true
    $verdict = "unknown"

    if ($replied) {
        # Use Phase 3 AI verdict
        $commentIdStr = "$commentId"
        if ($replyVerdicts.ContainsKey($commentIdStr)) {
            $verdict = $replyVerdicts[$commentIdStr]
        }
        # else stays "unknown"
    }
    else {
        # No reply — use Phase 2 diff verification results
        $precise = $preciseData | Where-Object { $_.CommentId -eq $commentId }
        if ($precise) {
            $pv = $precise.Verdict
            if ($pv -in @("suggestion-applied", "suggestion-likely-applied", "exact-lines-modified")) {
                $verdict = "helpful"
            }
            elseif ($pv -eq "lines-modified-different-fix") {
                $verdict = "helpful"
            }
            elseif ($pv -in @("file-changed-elsewhere", "file-changed-no-line-info")) {
                if (Test-ReauditFlip $repo $prNum $filePath) {
                    $verdict = "helpful"
                } else {
                    $verdict = "not-helpful"
                }
            }
            elseif ($pv -in @("file-not-changed", "no-subsequent-commits", "not-applied")) {
                $verdict = "not-helpful"
            }
            else {
                $verdict = "not-helpful"
            }
        }
        else {
            $verdict = "not-helpful"
        }
    }

    $finalResults += [PSCustomObject]@{
        Engineer    = $engineer
        Repo        = $repo
        PRNumber    = $prNum
        PRAuthor    = $prAuthor
        CommentId   = $commentId
        FilePath    = $filePath
        Replied     = $replied
        Verdict     = $verdict
    }
}

# Save final results
$finalResults | ConvertTo-Json -Depth 5 | Out-File "$OutputDir\final_classification.json" -Encoding utf8

# ========================================
# VALIDATE TOTALS
# ========================================
$totalHelp = ($finalResults | Where-Object { $_.Verdict -eq "helpful" }).Count
$totalNot = ($finalResults | Where-Object { $_.Verdict -eq "not-helpful" }).Count
$totalUnknown = ($finalResults | Where-Object { $_.Verdict -eq "unknown" }).Count
$totalReplied = ($finalResults | Where-Object { $_.Replied -eq $true }).Count
$totalIgnored = ($finalResults | Where-Object { $_.Replied -eq $false }).Count

Write-Host "================================================================"
Write-Host "FINAL CLASSIFICATION VALIDATION"
Write-Host "================================================================"
Write-Host "Total comments: $($finalResults.Count)"
Write-Host "Helpful: $totalHelp"
Write-Host "Not helpful: $totalNot"
Write-Host "Unknown: $totalUnknown"
Write-Host "Replied: $totalReplied"
Write-Host "Ignored: $totalIgnored"
Write-Host "Sum check: $($totalHelp + $totalNot + $totalUnknown) (should be $($finalResults.Count))"
Write-Host ""

# ========================================
# PER-ENGINEER STATS
# ========================================
Write-Host "================================================================"
Write-Host "PER-ENGINEER FINAL STATS"
Write-Host "================================================================"

$engineers = $finalResults | Group-Object Engineer | Sort-Object { $_.Group.Count } -Descending
foreach ($eg in $engineers) {
    $name = $eg.Name
    $comments = $eg.Group
    $total = $comments.Count
    $helped = ($comments | Where-Object { $_.Verdict -eq "helpful" }).Count
    $notHelped = ($comments | Where-Object { $_.Verdict -eq "not-helpful" }).Count
    $unknown = ($comments | Where-Object { $_.Verdict -eq "unknown" }).Count
    $replied = ($comments | Where-Object { $_.Replied -eq $true }).Count
    $ignored = ($comments | Where-Object { $_.Replied -eq $false }).Count
    $responseRate = [math]::Round(($replied / $total) * 100, 1)
    $helpfulness = [math]::Round(($helped / $total) * 100, 1)
    $prs = ($comments | Select-Object -Property PRNumber,Repo -Unique).Count

    Write-Host "$name | $total comments | $prs PRs | Replied=$replied Ignored=$ignored RR=$responseRate% | Helpful=$helped Not=$notHelped Unknown=$unknown | H=$helpfulness%"
}

# ========================================
# PER-REPO STATS
# ========================================
Write-Host ""
Write-Host "================================================================"
Write-Host "PER-REPO FINAL STATS"
Write-Host "================================================================"

foreach ($repoLabel in @("common", "msal", "broker")) {
    $rc = $finalResults | Where-Object { $_.Repo -eq $repoLabel }
    $total = $rc.Count
    $helped = ($rc | Where-Object { $_.Verdict -eq "helpful" }).Count
    $notHelped = ($rc | Where-Object { $_.Verdict -eq "not-helpful" }).Count
    $replied = ($rc | Where-Object { $_.Replied -eq $true }).Count
    $prsWithComments = ($rc | Select-Object -Property PRNumber -Unique).Count

    $prsFile = "$env:TEMP\${repoLabel}_prs.json"
    $allPRs = Get-Content $prsFile | ConvertFrom-Json
    $humanPRs = ($allPRs | Where-Object { $_.author.login -notin @("app/copilot-swe-agent", "dependabot[bot]", "github-actions[bot]") }).Count

    Write-Host "$($repoLabel.ToUpper()) | $total comments | $prsWithComments/$humanPRs PRs reviewed | Helpful=$helped ($([math]::Round($helped/$total*100,1))%) Not=$notHelped ($([math]::Round($notHelped/$total*100,1))%) | RR=$([math]::Round($replied/$total*100,1))%"
}

# ========================================
# RESPONSE BEHAVIOR STATS
# ========================================
Write-Host ""
Write-Host "================================================================"
Write-Host "OVERALL RESPONSE BEHAVIOR"
Write-Host "================================================================"
Write-Host "Total: $($finalResults.Count)"
Write-Host "Replied: $totalReplied ($([math]::Round($totalReplied/$finalResults.Count*100,1))%)"
Write-Host "Ignored: $totalIgnored ($([math]::Round($totalIgnored/$finalResults.Count*100,1))%)"
Write-Host ""
Write-Host "Of REPLIED ($totalReplied):"
$repliedHelp = ($finalResults | Where-Object { $_.Replied -and $_.Verdict -eq "helpful" }).Count
$repliedNot = ($finalResults | Where-Object { $_.Replied -and $_.Verdict -eq "not-helpful" }).Count
Write-Host "  Helpful: $repliedHelp ($([math]::Round($repliedHelp/$totalReplied*100,1))%)"
Write-Host "  Not helpful: $repliedNot ($([math]::Round($repliedNot/$totalReplied*100,1))%)"
Write-Host ""
Write-Host "Of IGNORED ($totalIgnored):"
$ignoredHelp = ($finalResults | Where-Object { -not $_.Replied -and $_.Verdict -eq "helpful" }).Count
$ignoredNot = ($finalResults | Where-Object { -not $_.Replied -and $_.Verdict -eq "not-helpful" }).Count
Write-Host "  Helpful (silently applied): $ignoredHelp ($([math]::Round($ignoredHelp/$totalIgnored*100,1))%)"
Write-Host "  Not helpful: $ignoredNot ($([math]::Round($ignoredNot/$totalIgnored*100,1))%)"

Write-Host ""
Write-Host "================================================================"
Write-Host "Data saved to: $OutputDir\final_classification.json"
Write-Host "================================================================"
