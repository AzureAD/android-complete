<#
.SYNOPSIS
    Final classification of all Copilot review comments.
    Applies keyword rules, loads manual audit decisions from external JSON,
    merges GitHub accounts to real engineer names via external config.
    Produces authoritative per-engineer and per-repo statistics.

.PARAMETER OutputDir
    Directory containing raw_results.json and precise.json from prior phases.

.PARAMETER AccountMapFile
    Path to JSON file mapping GitHub logins to display names.
    Format: { "github_login": "DisplayName", ... }
    If not provided, uses PR author login as-is.

.PARAMETER ManualAuditFile
    Path to JSON file with manual audit decisions from Phase 3.
    Format: {
        "genuineUnclearHelpful": ["reply pattern 1", ...],
        "genuineUnclearHelpfulExtra": ["commit sha or pattern", ...],
        "reauditFlipKeys": ["repo/prNum/filePattern", ...],
        "mixedResponseVerdict": "not-helpful"
    }
    If not provided, genuinely unclear comments default to "not-helpful"
    and file-changed-elsewhere/no-line-info default to "not-helpful".
#>

param(
    [string]$OutputDir = "$env:TEMP\copilot-review-analysis",
    [string]$AccountMapFile = "",
    [string]$ManualAuditFile = ""
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

# Manual audit decisions from Phase 3
$genuineUnclearHelpful = @()
$genuineUnclearHelpfulExtra = @()
$reauditFlipKeys = @()
$mixedResponseVerdict = "not-helpful"

if ($ManualAuditFile -and (Test-Path $ManualAuditFile)) {
    $auditRaw = Get-Content $ManualAuditFile -Raw | ConvertFrom-Json
    if ($auditRaw.genuineUnclearHelpful) {
        $genuineUnclearHelpful = @($auditRaw.genuineUnclearHelpful)
    }
    if ($auditRaw.genuineUnclearHelpfulExtra) {
        $genuineUnclearHelpfulExtra = @($auditRaw.genuineUnclearHelpfulExtra)
    }
    if ($auditRaw.reauditFlipKeys) {
        $reauditFlipKeys = @($auditRaw.reauditFlipKeys)
    }
    if ($auditRaw.mixedResponseVerdict) {
        $mixedResponseVerdict = $auditRaw.mixedResponseVerdict
    }
    Write-Host "Loaded manual audit: $($genuineUnclearHelpful.Count) unclear-helpful patterns, $($reauditFlipKeys.Count) re-audit flips" -ForegroundColor Cyan
} else {
    Write-Host "No manual audit file provided — genuinely unclear and ambiguous comments will default to 'not-helpful'" -ForegroundColor Yellow
}

# ========================================
# STEP 1: Keyword patterns for classifying replied comments
# ========================================

# Positive reply patterns
$positivePatterns = @(
    "good catch", "fixed", "done", "addressed", "will fix", "will address",
    "thanks", "thank you", "agreed", "makes sense", "updated", "nice catch",
    "you're right", "you are right", "correct", "valid point", "great catch",
    "resolved", "will do", "good point", "fair point", "acknowledged",
    "applied", "changed", "modified", "yep", "absolutely",
    "i'll update", "i will update", "i'll fix", "i will fix",
    "good suggestion", "great suggestion", "nice suggestion",
    "will change", "will update", "pushed a fix", "committed",
    "good find", "great find", "indeed",
    "making the change", "i've updated", "i've fixed"
)

# Negative reply patterns
$negativePatterns = @(
    "not applicable", "n/a", "won't fix", "wontfix", "by design",
    "intentional", "false positive", "not relevant", "ignore",
    "doesn't apply", "not needed", "unnecessary", "nah", "no need",
    "disagree", "incorrect", "wrong", "not accurate", "hallucin",
    "not a real issue", "not an issue", "this is fine", "it's fine",
    "already handled", "already done", "not applicable here",
    "copilot is wrong", "bot is wrong", "misunderstanding",
    "out of scope", "does not apply", "not a concern", "not a problem",
    "doesn't matter", "won't happen", "can't happen", "impossible"
)

# Delegated to copilot pattern
$delegatedPattern = '@copilot'

# Acknowledged action patterns (from unclear reclassification)
$acknowledgedPatterns = @(
    "added tests?", "refactored", "removed", "reverted", "renamed",
    "implemented", "reworked", "update signature", "log warning",
    "move check", "add test", "add unit test", "nice job", "good bot"
)

# Explained-away patterns (from unclear reclassification)
$explainedPatterns = @(
    "this is", "we don't", "we do not", "we aren't", "nope", "has been",
    "it's a", "they're meant", "this has", "only used", "never been",
    "was consciously", "just telemetry", "is just", "original behavior",
    "overdo", "legacy", "can only", "can never", "doesn't need",
    "suffix was", "timing is not", "skip", "most of the", "empty is fine",
    "no longer", "will stick", "keep the current", "consciously"
)

# Outdated/dismissed patterns
$outdatedPatterns = @("outdated", "dismissed")

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
    $replied = $item.Classification -ne "no-response"
    $verdict = "unknown"

    if ($item.Classification -eq "helpful-acknowledged") {
        $verdict = "helpful"
    }
    elseif ($item.Classification -eq "unhelpful-dismissed") {
        $verdict = "not-helpful"
    }
    elseif ($item.Classification -eq "mixed-response") {
        $verdict = $mixedResponseVerdict
    }
    elseif ($item.Classification -eq "replied-unclear") {
        # Apply the reclassification cascade
        $replyLower = $item.HumanReplyText.ToLower()

        # Check delegated to copilot
        if ($replyLower -match $delegatedPattern) {
            $verdict = "helpful"
        }
        # Check acknowledged action
        elseif ($false) { # placeholder, check below
        }
        else {
            # Check acknowledged patterns
            $isAcknowledged = $false
            foreach ($p in $acknowledgedPatterns) {
                if ($replyLower -match "\b$p\b") { $isAcknowledged = $true; break }
            }

            if ($isAcknowledged) {
                $verdict = "helpful"
            }
            else {
                # Check explained-away patterns
                $isExplained = $false
                foreach ($p in $explainedPatterns) {
                    if ($replyLower -match [regex]::Escape($p)) { $isExplained = $true; break }
                }

                # Check outdated patterns
                $isOutdated = $false
                foreach ($p in $outdatedPatterns) {
                    if ($replyLower -match "\b$p\b") { $isOutdated = $true; break }
                }

                if ($isExplained) {
                    $verdict = "not-helpful"
                }
                elseif ($isOutdated) {
                    $verdict = "not-helpful"
                }
                else {
                    # Genuinely unclear - check manual audit helpful list
                    $isManualHelpful = $false
                    foreach ($pattern in $genuineUnclearHelpful) {
                        if ($replyLower.Contains($pattern)) { $isManualHelpful = $true; break }
                    }
                    foreach ($pattern in $genuineUnclearHelpfulExtra) {
                        if ($replyLower.Contains($pattern)) { $isManualHelpful = $true; break }
                    }

                    if ($isManualHelpful) {
                        $verdict = "helpful"
                    }
                    else {
                        # Everything else in genuinely unclear was not-helpful
                        $verdict = "not-helpful"
                    }
                }
            }
        }
    }
    elseif ($item.Classification -eq "no-response") {
        # Use diff verification results
        $precise = $preciseData | Where-Object { $_.CommentId -eq $commentId }
        if ($precise) {
            $pv = $precise.Verdict
            if ($pv -in @("suggestion-applied", "suggestion-likely-applied", "exact-lines-modified")) {
                $verdict = "helpful"
            }
            elseif ($pv -eq "lines-modified-different-fix") {
                # Nearby lines modified with a different approach — treat as helpful
                # (the engineer addressed the concern differently than suggested)
                $verdict = "helpful"
            }
            elseif ($pv -in @("file-changed-elsewhere", "file-changed-no-line-info")) {
                # Check if this specific comment was flipped in re-audit
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
        OrigClass   = $item.Classification
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
