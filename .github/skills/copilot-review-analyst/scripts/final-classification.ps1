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
    [string]$ReauditFlipsFile = "",
    # Authoritative per-CommentId verdict overrides (e.g. from a manual re-audit).
    # Format: { "commentId": "helpful"|"declined"|"not-helpful"|"unknown", ... }. Trumps all.
    [string]$OverridesFile = "",
    # Verified silent-adoption promotions (Tier 1.3). JSON array of CommentId strings that a
    # human/agent CONFIRMED were applied via an autofix/coauthor commit whose diff implements
    # the comment. native-apply commits auto-promote and need not be listed here.
    [string]$SignalPromotionsFile = ""
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

# Authoritative per-CommentId overrides (manual re-audit). Trumps every computed verdict.
$overrides = @{}
if ($OverridesFile -and (Test-Path $OverridesFile)) {
    $ovRaw = Get-Content $OverridesFile -Raw | ConvertFrom-Json
    foreach ($prop in $ovRaw.PSObject.Properties) { $overrides[$prop.Name] = $prop.Value }
    Write-Host "Loaded authoritative overrides: $($overrides.Count) entries" -ForegroundColor Cyan
}

# Verified silent-adoption promotions (Tier 1.3): CommentIds confirmed applied via
# autofix/coauthor commits. native-apply auto-promotes without needing this list.
$signalPromotions = @{}
if ($SignalPromotionsFile -and (Test-Path $SignalPromotionsFile)) {
    $spRaw = Get-Content $SignalPromotionsFile -Raw | ConvertFrom-Json
    foreach ($id in @($spRaw)) { $signalPromotions["$id"] = $true }
    Write-Host "Loaded verified signal promotions: $($signalPromotions.Count) entries" -ForegroundColor Cyan
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

    # ---- Tier 1.3 silent-adoption signal routing (strict fallback, upgrade-only) ----
    # Catches comments the engineer ADOPTED without replying: GitHub "Apply suggestion"
    # button, Copilot Autofix, or coding-agent commits. Only ever promotes an otherwise
    # unresolved/not-helpful no-reply comment -> helpful; NEVER demotes and NEVER touches a
    # replied comment (an explicit reply is a stronger, human signal). Confidence tiers:
    #   native-apply -> auto-promote (button applies the suggestion verbatim)
    #   autofix / coauthor -> promote only if the CommentId was human/agent-VERIFIED
    #                         (listed in SignalPromotionsFile); the file-touch alone is not proof.
    #   thumbs-up with no reply and no diff -> auto-promote (explicit approval reaction)
    $promotedBySignal = $false
    $signalTier = $null
    if (-not $replied -and $verdict -eq "not-helpful") {
        if ($item.ApplyTier -eq "native-apply") { $verdict = "helpful"; $promotedBySignal = $true; $signalTier = "native-apply" }
        elseif ($item.ApplyCandidate -eq $true -and $signalPromotions.ContainsKey("$commentId")) { $verdict = "helpful"; $promotedBySignal = $true; $signalTier = "$($item.ApplyTier)-verified" }
        elseif ([int]$item.ThumbsUp -gt 0) { $verdict = "helpful"; $promotedBySignal = $true; $signalTier = "reaction" }
    }

    # ---- Authoritative overrides (manual re-audit) trump everything ----
    if ($overrides.ContainsKey("$commentId")) {
        $verdict = $overrides["$commentId"]
    }

    # ---- Normalize to the canonical 5-verdict taxonomy ----
    # helpful | declined | incorrect | unresolved | unknown.
    # The legacy "not-helpful" verdict is intentionally coarse; resolve it honestly:
    #   replied + not-helpful  -> the engineer engaged and Copilot was proven wrong  => incorrect
    #   no-reply + not-helpful -> no evidence either way (never applied, never rebutted) => unresolved
    # NOTE: this legacy fallback CANNOT tell "declined" (correct-but-not-taken) apart from a
    # genuine error for replied comments — it will over-attribute to incorrect. To measure precision
    # honestly, supply an explicit "declined"/"incorrect" verdict via ReplyVerdictsFile or
    # OverridesFile; this mapping is only the conservative default when that signal is absent.
    if ($verdict -eq "not-helpful") {
        $verdict = if ($replied) { "incorrect" } else { "unresolved" }
    }

    $finalResults += [PSCustomObject]@{
        Engineer        = $engineer
        Repo            = $repo
        PRNumber        = $prNum
        PRAuthor        = $prAuthor
        CommentId       = $commentId
        FilePath        = $filePath
        Replied         = $replied
        Verdict         = $verdict
        PromotedBySignal = $promotedBySignal
        SignalTier      = $signalTier
    }
}

# Save final results
$finalResults | ConvertTo-Json -Depth 5 | Out-File "$OutputDir\final_classification.json" -Encoding utf8

# ========================================
# VALIDATE TOTALS  (canonical taxonomy: helpful | declined | incorrect | unresolved | unknown)
# ========================================
$totalHelp      = ($finalResults | Where-Object { $_.Verdict -eq "helpful" }).Count
$totalDeclined  = ($finalResults | Where-Object { $_.Verdict -eq "declined" }).Count
$totalIncorrect = ($finalResults | Where-Object { $_.Verdict -eq "incorrect" }).Count
$unresolved     = ($finalResults | Where-Object { $_.Verdict -eq "unresolved" }).Count
$totalUnknown   = ($finalResults | Where-Object { $_.Verdict -eq "unknown" }).Count
$totalReplied   = ($finalResults | Where-Object { $_.Replied -eq $true }).Count
$totalIgnored   = ($finalResults | Where-Object { $_.Replied -eq $false }).Count
$promotedCount  = ($finalResults | Where-Object { $_.PromotedBySignal }).Count

# Copilot precision: of the comments where Copilot's correctness was actually evaluable
# (Helpful + genuinely Incorrect), how often it was correct. "declined" (correct/reasonable
# but the engineer chose not to take it) and "unresolved" (no evidence either way) are
# EXCLUDED from the denominator — neither is a Copilot error.
$precisionDenom = $totalHelp + $totalIncorrect
$precision = if ($precisionDenom -gt 0) { [math]::Round($totalHelp / $precisionDenom * 100, 1) } else { 0 }

Write-Host "================================================================"
Write-Host "FINAL CLASSIFICATION VALIDATION"
Write-Host "================================================================"
Write-Host "Total comments: $($finalResults.Count)"
Write-Host "Helpful:    $totalHelp  (replied-helpful + silently-applied)"
Write-Host "Declined:   $totalDeclined  (correct/reasonable, engineer declined)"
Write-Host "Incorrect:  $totalIncorrect  (Copilot proven factually wrong)"
Write-Host "Unresolved: $unresolved  (no reply, no diff evidence)"
Write-Host "Unknown:    $totalUnknown"
Write-Host "Replied: $totalReplied   Silent/ignored: $totalIgnored   Signal-promoted: $promotedCount"
Write-Host "Copilot precision (Helpful / (Helpful + Incorrect)): $precision%"
Write-Host "Sum check: $($totalHelp + $totalDeclined + $totalIncorrect + $unresolved + $totalUnknown) (should be $($finalResults.Count))"
Write-Host ""

# ========================================
# SELF-CONSISTENCY GATE (Tier 2.5, borrowed from JS)
# Refuse to emit numbers that don't reconcile. Any failure here means a bucketing bug
# and MUST stop the pipeline before a bad report is generated.
# ========================================
$grand = $finalResults.Count
$gateErrors = @()
$canon = @("helpful","declined","incorrect","unresolved","unknown")

# (a) the five canonical buckets sum to the grand total
if (($totalHelp + $totalDeclined + $totalIncorrect + $unresolved + $totalUnknown) -ne $grand) {
    $gateErrors += "Overall buckets ($($totalHelp+$totalDeclined+$totalIncorrect+$unresolved+$totalUnknown)) != total ($grand)"
}
# (b) no stray verdict values outside the canonical set (e.g. an un-normalized 'not-helpful')
$stray = ($finalResults | Where-Object { $canon -notcontains $_.Verdict })
if ($stray.Count -gt 0) {
    $gateErrors += "Found $($stray.Count) comment(s) with non-canonical verdict(s): $((($stray.Verdict | Select-Object -Unique) -join ', '))"
}
# (c) sum of per-repo totals == grand total, and each repo's buckets sum to its total
$repoSum = 0
foreach ($rl in @("common","msal","broker")) {
    $rc = $finalResults | Where-Object { $_.Repo -eq $rl }
    $rt = $rc.Count; $repoSum += $rt
    $rb = ($rc | Where-Object { $canon -contains $_.Verdict }).Count
    if ($rb -ne $rt) { $gateErrors += "Repo $rl buckets ($rb) != repo total ($rt)" }
}
if ($repoSum -ne $grand) { $gateErrors += "Sum of per-repo totals ($repoSum) != grand total ($grand)" }
# (d) sum of per-engineer totals == grand total, and each engineer's buckets sum to its total
$engSum = 0
foreach ($eg in ($finalResults | Group-Object Engineer)) {
    $et = $eg.Group.Count; $engSum += $et
    $eb = ($eg.Group | Where-Object { $canon -contains $_.Verdict }).Count
    if ($eb -ne $et) { $gateErrors += "Engineer $($eg.Name) buckets ($eb) != engineer total ($et)" }
}
if ($engSum -ne $grand) { $gateErrors += "Sum of per-engineer totals ($engSum) != grand total ($grand)" }

if ($gateErrors.Count -gt 0) {
    Write-Host "SELF-CONSISTENCY GATE FAILED:" -ForegroundColor Red
    $gateErrors | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    throw "Self-consistency gate failed with $($gateErrors.Count) error(s). Report generation aborted."
}
Write-Host "Self-consistency gate PASSED (overall = per-repo = per-engineer; all buckets reconcile)." -ForegroundColor Green

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
    $notHelped = ($comments | Where-Object { $_.Verdict -eq "incorrect" }).Count
    $declined = ($comments | Where-Object { $_.Verdict -eq "declined" }).Count
    $unresolvedE = ($comments | Where-Object { $_.Verdict -eq "unresolved" }).Count
    $unknown = ($comments | Where-Object { $_.Verdict -eq "unknown" }).Count
    $replied = ($comments | Where-Object { $_.Replied -eq $true }).Count
    $ignored = ($comments | Where-Object { $_.Replied -eq $false }).Count
    $responseRate = [math]::Round(($replied / $total) * 100, 1)
    $helpfulness = [math]::Round(($helped / $total) * 100, 1)
    $prs = ($comments | Select-Object -Property PRNumber,Repo -Unique).Count

    Write-Host "$name | $total comments | $prs PRs | Replied=$replied Ignored=$ignored RR=$responseRate% | Helpful=$helped Incorrect=$notHelped Declined=$declined Unresolved=$unresolvedE Unknown=$unknown | H=$helpfulness%"
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
    $notHelped = ($rc | Where-Object { $_.Verdict -eq "incorrect" }).Count
    $declined = ($rc | Where-Object { $_.Verdict -eq "declined" }).Count
    $unresolvedR = ($rc | Where-Object { $_.Verdict -eq "unresolved" }).Count
    $replied = ($rc | Where-Object { $_.Replied -eq $true }).Count
    $prsWithComments = ($rc | Select-Object -Property PRNumber -Unique).Count

    $prsFile = "$env:TEMP\${repoLabel}_prs.json"
    $allPRs = Get-Content $prsFile | ConvertFrom-Json
    $humanPRs = ($allPRs | Where-Object { $_.author.login -notin @("app/copilot-swe-agent", "dependabot[bot]", "github-actions[bot]") }).Count

    Write-Host "$($repoLabel.ToUpper()) | $total comments | $prsWithComments/$humanPRs PRs reviewed | Helpful=$helped ($([math]::Round($helped/$total*100,1))%) Incorrect=$notHelped Declined=$declined Unresolved=$unresolvedR | RR=$([math]::Round($replied/$total*100,1))%"
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
$repliedNot = ($finalResults | Where-Object { $_.Replied -and $_.Verdict -eq "incorrect" }).Count
$repliedDeclined = ($finalResults | Where-Object { $_.Replied -and $_.Verdict -eq "declined" }).Count
Write-Host "  Helpful: $repliedHelp ($([math]::Round($repliedHelp/$totalReplied*100,1))%)"
Write-Host "  Incorrect (Copilot proven wrong): $repliedNot ($([math]::Round($repliedNot/$totalReplied*100,1))%)"
Write-Host "  Declined (correct/reasonable, engineer declined): $repliedDeclined ($([math]::Round($repliedDeclined/$totalReplied*100,1))%)"
Write-Host ""
Write-Host "Of IGNORED ($totalIgnored):"
$ignoredHelp = ($finalResults | Where-Object { -not $_.Replied -and $_.Verdict -eq "helpful" }).Count
$ignoredUnres = ($finalResults | Where-Object { -not $_.Replied -and $_.Verdict -eq "unresolved" }).Count
Write-Host "  Helpful (silently applied): $ignoredHelp ($([math]::Round($ignoredHelp/$totalIgnored*100,1))%)"
Write-Host "  Unresolved (no diff evidence): $ignoredUnres ($([math]::Round($ignoredUnres/$totalIgnored*100,1))%)"

Write-Host ""
Write-Host "================================================================"
Write-Host "Data saved to: $OutputDir\final_classification.json"
Write-Host "================================================================"
