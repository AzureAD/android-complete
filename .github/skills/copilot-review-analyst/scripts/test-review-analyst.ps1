# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

<#
.SYNOPSIS
    Offline regression checks for the unified review analyst; no account/API mutations.
#>
param(
    [string]$OutputDir = (Join-Path $env:TEMP ("review-analyst-test-" + [guid]::NewGuid().ToString("N")))
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "Assertion failed: $Message" }
}

function Write-Json {
    param($Value, [string]$Path)
    ConvertTo-Json -InputObject $Value -Depth 25 | Set-Content -LiteralPath $Path -Encoding utf8
}

function New-Comment {
    param([int]$Id, [string]$Author, [string]$Content, [string]$Type = "text", [bool]$Deleted = $false)
    return [pscustomobject]@{
        id = $Id; parentCommentId = 0; author = @{ id = "test-$Author"; displayName = $Author }
        content = $Content; commentType = $Type; isDeleted = $Deleted
    }
}

function New-Thread {
    param([int]$Id, [object[]]$Comments, [bool]$Inline = $true, [string]$Status = "fixed")
    return [pscustomobject]@{
        id = $Id; status = $Status; comments = $Comments
        threadContext = if ($Inline) {
            @{ filePath = "/Example.kt"; rightFileStart = @{ line = 10 }; rightFileEnd = @{ line = 10 } }
        } else { $null }
        pullRequestThreadContext = @{ iterationContext = @{ secondComparingIteration = 2 } }
    }
}

$reviewer = "One Build Service (msazure)"
$finding = "**Issue:** Example finding.`n_AuthApp PR Reviewer automated review_"
$threads = @(
    (New-Thread 101 @(
        (New-Comment 1 $reviewer $finding),
        (New-Comment 2 "Engineer" ("Fixed. " + ("Full reply preserved. " * 40))),
        (New-Comment 3 $reviewer "Automated follow-up"),
        (New-Comment 4 "Engineer" "Policy update" "system"),
        (New-Comment 5 "Engineer" "Deleted reply" "text" $true)
    )),
    (New-Thread 102 @((New-Comment 1 $reviewer $finding))),
    (New-Thread 103 @((New-Comment 1 $reviewer "AuthApp PR Reviewer summary")) $false "closed"),
    (New-Thread 104 @((New-Comment 1 $reviewer "Build passed"))),
    (New-Thread 105 @((New-Comment 1 "Human reviewer" "Human-only feedback"))),
    (New-Thread 106 @((New-Comment 1 $reviewer $finding "system"))),
    (New-Thread 107 @((New-Comment 1 $reviewer $finding "text" $true)))
)
$bundle = [pscustomobject]@{
    pullRequest = @{
        pullRequestId = 123; title = "Synthetic PR"; status = "completed"; closedDate = "2026-10-14T12:00:00Z"
        createdBy = @{ id = "test-author"; displayName = "Engineer" }
        repository = @{
            id = "test-repository"; name = "AD-MFA-phonefactor-phoneApp-android"
            url = "https://dev.azure.com/msazure/One/_apis/git/repositories/test-repository"
        }
        lastMergeSourceCommit = @{ commitId = ("b" * 40) }
    }
    threads = $threads
    iterations = @(@{ id = 2; sourceRefCommit = @{ commitId = ("a" * 40) } })
}
$inputPath = Join-Path $OutputDir "authapp-export.json"
Write-Json @($bundle) $inputPath
& "$PSScriptRoot\analyze-authapp.ps1" -InputFile $inputPath -OutputDir $OutputDir -StartDate "2026-10-14" -EndDate "2026-10-14"
$foundry = @(Get-Content "$OutputDir\authapp_raw_results.json" -Raw | ConvertFrom-Json)
$coverage = Get-Content "$OutputDir\authapp_coverage.json" -Raw | ConvertFrom-Json
Assert-True ($foundry.Count -eq 2) "Only marked inline roots count."
Assert-True ($foundry[0].HumanReplyCount -eq 1 -and $foundry[0].HumanReplyText.Length -gt 400) "Zero-parent human reply must be captured without truncation."
Assert-True (-not $foundry[1].HasReply) "Fixed status must not manufacture a reply."
Assert-True ($foundry[0].OriginalCommitId -eq ("a" * 40)) "Reviewed iteration must resolve to its immutable source SHA."
Assert-True ($foundry[0].CommentId -ne $foundry[1].CommentId) "Native comment ID 1 in different threads must not collide."
Assert-True ($coverage.reviewedByAI -eq 1 -and $coverage.withInlineFeedback -eq 1) "PR coverage counts reviews once, not once per finding."
Assert-True ($coverage.excludedUnattributedServiceThreads -eq 1) "Unmarked build-service automation must be excluded and surfaced."

& "$PSScriptRoot\analyze-authapp.ps1" -InputFile $inputPath -OutputDir $OutputDir -StartDate "2026-10-13" -EndDate "2026-10-13"
Assert-True (@(Get-Content "$OutputDir\authapp_raw_results.json" -Raw | ConvertFrom-Json).Count -eq 0) "PRs completed outside the window must be excluded."
$bundle.pullRequest.createdBy.displayName = $reviewer
Write-Json @($bundle) $inputPath
& "$PSScriptRoot\analyze-authapp.ps1" -InputFile $inputPath -OutputDir $OutputDir -StartDate "2026-10-14" -EndDate "2026-10-14"
Assert-True ((Get-Content "$OutputDir\authapp_coverage.json" -Raw | ConvertFrom-Json).mergedHumanPRs -eq 0) "Service-authored PRs must not enter human denominators."
$bundle.pullRequest.createdBy.displayName = "Engineer"
Write-Json @($bundle) $inputPath

# Azure CLI lists PRs at project scope unless the repository is a search filter.
$global:ReviewAnalystMockBundle = $bundle
function az {
    $global:LASTEXITCODE = 0
    $resource = $args[[array]::IndexOf($args, "--resource") + 1]
    switch ($resource) {
        "repositories" { return ($global:ReviewAnalystMockBundle.pullRequest.repository | ConvertTo-Json -Depth 25) }
        "pullRequests" {
            Assert-True ($args -contains "searchCriteria.repositoryId=test-repository") "CLI PR lists must use the repository search filter."
            return (ConvertTo-Json -InputObject @{ value = @($global:ReviewAnalystMockBundle.pullRequest) } -Depth 25)
        }
        "pullRequestThreads" { return (ConvertTo-Json -InputObject @{ value = $global:ReviewAnalystMockBundle.threads } -Depth 25) }
        "pullRequestIterations" { return (ConvertTo-Json -InputObject @{ value = $global:ReviewAnalystMockBundle.iterations } -Depth 25) }
        default { throw "Unexpected mocked az resource: $resource" }
    }
}
& "$PSScriptRoot\analyze-authapp.ps1" -OutputDir $OutputDir -StartDate "2026-10-14" -EndDate "2026-10-14"
Assert-True (@(Get-Content "$OutputDir\authapp_raw_results.json" -Raw | ConvertFrom-Json).Count -eq 2) "CLI collection must preserve the same findings as a full export."
Remove-Item Function:\az
Remove-Variable ReviewAnalystMockBundle -Scope Global

# Exercise combined collection with deterministic GitHub responses.
function gh {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq "auth") { return "Logged in" }
    if ($args[0] -eq "pr") {
        return '[{"number":7,"title":"Synthetic GitHub PR","author":{"login":"github-engineer"},"state":"MERGED"}]'
    }
    if ($args[0] -eq "api") {
        $url = [string]$args[1]
        if ($url -match '/reviews$') { return '[{"id":9,"user":{"login":"Copilot"},"body":"Summary","state":"COMMENTED"}]' }
        if ($url -match '/comments$' -and $url -match 'common-for-android') {
            return '[{"id":1001,"user":{"login":"Copilot"},"body":"Finding","path":"Example.kt","line":10},{"id":1002,"in_reply_to_id":1001,"user":{"login":"github-engineer"},"body":"Fixed"}]'
        }
        return "[]"
    }
    throw "Unexpected mocked gh call: $args"
}
& "$PSScriptRoot\analyze.ps1" -AuthAppInputFile $inputPath -OutputDir $OutputDir -StartDate "2026-10-14" -EndDate "2026-10-14"
$raw = @(Get-Content "$OutputDir\raw_results.json" -Raw | ConvertFrom-Json)
$combinedCoverage = Get-Content "$OutputDir\coverage.json" -Raw | ConvertFrom-Json
Assert-True ($raw.Count -eq 3) "Combined collection must contain both sources."
Assert-True ($combinedCoverage.perSource.Count -eq 2 -and $combinedCoverage.overall.reviewedByAI -eq 4) "Combined coverage must retain distinct sources, including summary-only reviews."

& "$PSScriptRoot\precise.ps1" -OutputDir $OutputDir -AuthAppRepoPath (Join-Path $OutputDir "absent-checkout")
$precise = @(Get-Content "$OutputDir\precise.json" -Raw | ConvertFrom-Json)
Assert-True ($precise.Count -eq 1 -and $precise[0].Verdict -eq "unknown") "Unavailable AuthApp snapshots remain unknown, not file-not-changed."
Assert-True ($precise[0].ReviewSource -eq "Foundry") "Verification must preserve source."

# Verify the local-object path without fetching or creating repository state.
function git {
    $global:LASTEXITCODE = 0
    if ($args -contains "cat-file") { return }
    if ($args -contains "merge-base") {
        if ($global:ReviewAnalystMockRewritten) { $global:LASTEXITCODE = 1 }
        return
    }
    if ($args -contains "diff") {
        return @("@@ -10,1 +10,1 @@", "-oldValue", "+newValue")
    }
    throw "Unexpected mocked git call: $args"
}
$global:ReviewAnalystMockRewritten = $false
& "$PSScriptRoot\precise.ps1" -OutputDir $OutputDir -AuthAppRepoPath $OutputDir
$verified = @(Get-Content "$OutputDir\precise.json" -Raw | ConvertFrom-Json)
Assert-True ($verified[0].Verdict -eq "exact-lines-modified" -and -not $verified[0].SnapshotNeedsValidation) "Available immutable source snapshots must support RIGHT-side diff evidence."
& "$PSScriptRoot\final-classification.ps1" -OutputDir $OutputDir
$verifiedFinal = @(Get-Content "$OutputDir\final_classification.json" -Raw | ConvertFrom-Json)
$verifiedFinding = $verifiedFinal | Where-Object CommentId -eq $foundry[1].CommentId
Assert-True ($verifiedFinding.Verdict -eq "helpful" -and -not $verifiedFinding.SnapshotNeedsValidation) "Final classification must carry verified snapshot metadata, not stale collection flags."

$global:ReviewAnalystMockRewritten = $true
& "$PSScriptRoot\precise.ps1" -OutputDir $OutputDir -AuthAppRepoPath $OutputDir
$rewritten = @(Get-Content "$OutputDir\precise.json" -Raw | ConvertFrom-Json)
Assert-True ($rewritten[0].Verdict -eq "unknown" -and $rewritten[0].ReviewedCommitRewritten) "Rewritten AuthApp snapshots require manual validation, not automatic credit."

$global:ReviewAnalystMockRewritten = $false
foreach ($item in $raw) { if ($item.Repo -eq "authapp") { $item.Side = "LEFT" } }
Write-Json @($raw) "$OutputDir\raw_results.json"
& "$PSScriptRoot\precise.ps1" -OutputDir $OutputDir -AuthAppRepoPath $OutputDir
$left = @(Get-Content "$OutputDir\precise.json" -Raw | ConvertFrom-Json)
Assert-True ($left[0].Verdict -eq "file-changed-no-line-info") "LEFT target coordinates must not receive source-line overlap credit."
foreach ($item in $raw) { if ($item.Repo -eq "authapp") { $item.Side = "RIGHT" } }
Write-Json @($raw) "$OutputDir\raw_results.json"
& "$PSScriptRoot\precise.ps1" -OutputDir $OutputDir -AuthAppRepoPath (Join-Path $OutputDir "absent-checkout")

$verdictPath = Join-Path $OutputDir "reply-verdicts.json"
$verdicts = @{ "1001" = "helpful"; $foundry[0].CommentId = "declined" }
Write-Json $verdicts $verdictPath
& "$PSScriptRoot\final-classification.ps1" -OutputDir $OutputDir -ReplyVerdictsFile $verdictPath
$final = @(Get-Content "$OutputDir\final_classification.json" -Raw | ConvertFrom-Json)
Assert-True ($final.Count -eq 3) "Final totals must reconcile across repositories."
Assert-True (@($final | Where-Object ReviewSource -eq "Foundry").Count -eq 2) "Foundry source must survive classification."
Assert-True (($final | Where-Object CommentId -eq $foundry[1].CommentId).Verdict -eq "unresolved") "Fixed thread without evidence must remain unresolved."

$duplicates = @($raw) + @($raw[0])
Write-Json $duplicates "$OutputDir\raw_results.json"
$duplicateRejected = $false
try {
    & "$PSScriptRoot\final-classification.ps1" -OutputDir $OutputDir -ReplyVerdictsFile $verdictPath
} catch {
    if ($_ -notmatch "Duplicate CommentId") { throw }
    $duplicateRejected = $true
}
Assert-True $duplicateRejected "Duplicate composite keys must abort instead of inflating metrics."
Write-Json @($raw) "$OutputDir\raw_results.json"

$historyPath = Join-Path $OutputDir "history.json"
Write-Json @(@{ periodStart = "2026-09-01"; periodEnd = "2026-09-07"; total = 10; helpful = @{ pct = 50 } }) $historyPath
& "$PSScriptRoot\append-history.ps1" -InputDir $OutputDir -HistoryFile $historyPath -PeriodStart "2026-10-14" -PeriodEnd "2026-10-14"
$history = @(Get-Content $historyPath -Raw | ConvertFrom-Json)
Assert-True ($history.Count -eq 2 -and $history[0].periodDays -eq 1) "History must support one inclusive day and retain the legacy snapshot."
Assert-True ($history[0].sources.Foundry.comments -eq 2) "History must carry source-specific denominators."
Assert-True ($null -eq $history[0].sources.Foundry.precision -and $history[0].sources.Foundry.precisionDenominator -eq 0) "Unevaluable precision must be n/a, not zero percent."
Assert-True ($history[0].sources.'GitHub Copilot'.precision -eq 100) "Copilot precision must exclude Foundry declined/unresolved findings."
Assert-True ($history[0].coverage.reviewedPRs -eq 4) "History must use reviewedByAI for combined coverage."
Assert-True ($history[0].reviewedPRs -eq 4 -and $history[0].prsWithInlineFeedback -eq 2) "History must distinguish all reviewed PRs from PRs with findings."
& "$PSScriptRoot\append-history.ps1" -InputDir $OutputDir -HistoryFile $historyPath -PeriodStart "2026-10-14" -PeriodEnd "2026-10-14"
Assert-True (@(Get-Content $historyPath -Raw | ConvertFrom-Json).Count -eq 2) "Repeating a period replaces its snapshot."

# Legacy GitHub-only rows retain their numeric verdict keys and source inference.
Write-Json @(@{ Repo = "common"; PRNumber = 7; PRAuthor = "github-engineer"; CommentId = 1001; FilePath = "Example.kt"; HasReply = $true }) "$OutputDir\raw_results.json"
Write-Json @() "$OutputDir\precise.json"
& "$PSScriptRoot\final-classification.ps1" -OutputDir $OutputDir -ReplyVerdictsFile $verdictPath
$legacy = @(Get-Content "$OutputDir\final_classification.json" -Raw | ConvertFrom-Json)
Assert-True ($legacy[0].ReviewSource -eq "GitHub Copilot" -and $legacy[0].Verdict -eq "helpful") "Legacy numeric comment IDs and source inference must remain compatible."

# Empty windows must produce JSON arrays and no division-by-zero errors.
Write-Json @() "$OutputDir\raw_results.json"
& "$PSScriptRoot\precise.ps1" -OutputDir $OutputDir
& "$PSScriptRoot\final-classification.ps1" -OutputDir $OutputDir
Assert-True ((Get-Content "$OutputDir\final_classification.json" -Raw).Trim() -eq "[]") "An empty window must persist an empty JSON array."
Write-Host "Unified review analyst regression checks passed. Artifacts: $OutputDir" -ForegroundColor Green
Remove-Variable ReviewAnalystMockRewritten -Scope Global
