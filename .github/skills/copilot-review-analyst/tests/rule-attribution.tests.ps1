# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

$ErrorActionPreference = "Stop"
$scripts = Join-Path $PSScriptRoot "..\scripts"
. (Join-Path $scripts "rule-attribution.ps1")

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$tag = "Review rule: memory-storage/v1"
$cases = @(
    @{ Body = $null; Tagged = $false },
    @{ Body = ""; Tagged = $false },
    @{ Body = "Issue`n$tag"; Tagged = $true },
    @{ Body = "Issue`r`n$tag `t`r`n"; Tagged = $true },
    @{ Body = "$tag`n$tag"; Tagged = $true },
    @{ Body = "Review rule: memory-storage/v10"; Tagged = $false },
    @{ Body = "Review rule: memory-storage/v1 extra"; Tagged = $false },
    @{ Body = "> $tag"; Tagged = $false },
    @{ Body = "    $tag"; Tagged = $false },
    @{ Body = ('```text' + "`n$tag`n" + '```'); Tagged = $false },
    @{ Body = ("~~~~text`n$tag`n~~~~"); Tagged = $false },
    @{ Body = ('````text' + "`n$tag`n" + '```' + "`n$tag`n" + '````'); Tagged = $false },
    @{ Body = ('```text' + "`n$tag`n" + '```' + "`n$tag"); Tagged = $true },
    @{ Body = "Review rule: MEMORY-STORAGE/V1"; Tagged = $false }
)
foreach ($case in $cases) {
    $metadata = Get-MemoryStorageReviewMetadata -Body $case.Body
    Assert-True (($metadata.RuleId -eq "memory-storage/v1") -eq $case.Tagged) "Incorrect marker parsing: $($case.Body)"
}
$sha = "a" * 40
$metadata = Get-MemoryStorageReviewMetadata -Body "Summary`nReview screen: memory-storage/v1`nGuidance revision: $sha"
Assert-True $metadata.MemoryStorageScreened "Screen marker was not recognized"
Assert-True ($metadata.GuidanceRevision -eq $sha) "Guidance SHA was lost"
$metadata = Get-MemoryStorageReviewMetadata -Body "Review screen: memory-storage/v1`nGuidance revision: local-uncommitted"
Assert-True ($metadata.GuidanceRevision -eq "local-uncommitted") "Local guidance revision was lost"
$metadata = Get-MemoryStorageReviewMetadata -Body "Review screen: memory-storage/v1`nGuidance revision: guessed"
Assert-True ($metadata.GuidanceRevision -eq "unknown") "Invalid revision was accepted"
$metadata = Get-MemoryStorageReviewMetadata -Body ('```text' + "`nReview screen: memory-storage/v1`n" + '```')
Assert-True (-not $metadata.MemoryStorageScreened) "Quoted screen was counted as coverage"

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("review-rule-tests-" + [guid]::NewGuid())
$originalTemp = $env:TEMP
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    $env:TEMP = $testRoot
    foreach ($repo in @("common", "msal", "broker")) {
        '[{"number":1,"author":{"login":"fixture-author"}}]' |
            Set-Content (Join-Path $testRoot "${repo}_prs.json") -Encoding utf8
    }
    $raw = @(
        @{ Repo = "common"; PRNumber = 1; PRAuthor = "fixture-author"; CommentId = 11; ReviewId = 101; FilePath = "Cache.kt"; HasReply = $true; CommentBody = "Issue`n$tag" },
        @{ Repo = "common"; PRNumber = 1; PRAuthor = "fixture-author"; CommentId = 12; ReviewId = 101; FilePath = "Store.kt"; HasReply = $false; CommentBody = "Issue`n$tag`n$tag" },
        @{ Repo = "common"; PRNumber = 1; PRAuthor = "fixture-author"; CommentId = 13; ReviewId = 101; FilePath = "Other.kt"; HasReply = $false; CommentBody = "> $tag" },
        @{ Repo = "msal"; PRNumber = 1; PRAuthor = "fixture-author"; CommentId = 21; ReviewId = 201; FilePath = "Client.kt"; HasReply = $true; CommentBody = "Issue`n$tag" },
        @{ Repo = "broker"; PRNumber = 1; PRAuthor = "fixture-author"; CommentId = 31; ReviewId = 301; FilePath = "Broker.kt"; HasReply = $true; CommentBody = "Issue`n$tag" },
        @{ Repo = "broker"; PRNumber = 1; PRAuthor = "fixture-author"; CommentId = 32; ReviewId = 301; FilePath = "Broker.kt"; HasReply = $true; CommentBody = "Issue`n$tag" },
        @{ Repo = "broker"; PRNumber = 1; PRAuthor = "fixture-author"; CommentId = 33; ReviewId = 301; FilePath = "Broker.kt"; HasReply = $true; CommentBody = "Issue`n$tag" }
    )
    ConvertTo-Json -InputObject $raw | Set-Content (Join-Path $testRoot "raw_results.json") -Encoding utf8
    '[]' | Set-Content (Join-Path $testRoot "precise.json") -Encoding utf8
    '{"11":"helpful","21":"declined","31":"incorrect","32":"unknown","33":"helpful"}' |
        Set-Content (Join-Path $testRoot "reply-verdicts.json") -Encoding utf8
    $summaries = @(
        @{ Repo = "common"; PRNumber = 1; ReviewId = 101; MemoryStorageScreened = $true; GuidanceRevision = $sha },
        @{ Repo = "common"; PRNumber = 2; ReviewId = 102; MemoryStorageScreened = $true; GuidanceRevision = "local-uncommitted" },
        @{ Repo = "broker"; PRNumber = 1; ReviewId = 301; BodyExcerpt = "Legacy summary without coverage metadata" }
    )
    ConvertTo-Json -InputObject $summaries | Set-Content (Join-Path $testRoot "review_summaries.json") -Encoding utf8
    $classifier = Join-Path $scripts "final-classification.ps1"
    $verdictsFile = Join-Path $testRoot "reply-verdicts.json"
    & $classifier -OutputDir $testRoot -ReplyVerdictsFile $verdictsFile *> $null

    $results = @(Get-Content (Join-Path $testRoot "final_classification.json") -Raw | ConvertFrom-Json)
    Assert-True ($results.Count -eq 7) "Existing classification count changed"
    Assert-True (@($results | Where-Object { $_.RuleId -eq "memory-storage/v1" }).Count -eq 6) "Tags were not retained"
    Assert-True (($results | Where-Object CommentId -eq 11).ReviewId -eq 101) "Review ID was lost"
    $attribution = @(Get-Content (Join-Path $testRoot "rule_attribution.json") -Raw | ConvertFrom-Json)
    Assert-True ($attribution.Count -eq 4) "Cross-repo PRs or zero-finding screen was lost"
    $common = $attribution | Where-Object { $_.Repo -eq "common" -and $_.PRNumber -eq 1 }
    Assert-True ($common.TaggedComments -eq 2 -and $common.Helpful -eq 1 -and $common.Unresolved -eq 1) "Common outcomes are incorrect"
    Assert-True ($common.ScreenConfirmed -and $common.GuidanceRevisions[0] -eq $sha) "Screen revision was lost"
    $zero = $attribution | Where-Object { $_.Repo -eq "common" -and $_.PRNumber -eq 2 }
    Assert-True ($zero.ScreenConfirmed -and $zero.TaggedComments -eq 0) "Confirmed zero was not retained"
    $msal = $attribution | Where-Object Repo -eq "msal"
    Assert-True (-not $msal.ScreenConfirmed -and $msal.Declined -eq 1) "Missing coverage was inferred or declined was lost"
    $broker = $attribution | Where-Object Repo -eq "broker"
    Assert-True (-not $broker.ScreenConfirmed -and $broker.Incorrect -eq 1 -and $broker.Unknown -eq 1 -and $broker.Helpful -eq 1) "Broker outcomes are incorrect"

    # A repeated input row must not inflate rule-associated comment counts.
    ConvertTo-Json -InputObject @($raw + $raw[0]) | Set-Content (Join-Path $testRoot "raw_results.json") -Encoding utf8
    & $classifier -OutputDir $testRoot -ReplyVerdictsFile $verdictsFile *> $null
    $attribution = @(Get-Content (Join-Path $testRoot "rule_attribution.json") -Raw | ConvertFrom-Json)
    $common = $attribution | Where-Object { $_.Repo -eq "common" -and $_.PRNumber -eq 1 }
    Assert-True ($common.TaggedComments -eq 2) "Duplicate comment ID inflated attribution"

    # A fully quiet analysis still records confirmed screens and valid JSON arrays.
    '[]' | Set-Content (Join-Path $testRoot "raw_results.json") -Encoding utf8
    & $classifier -OutputDir $testRoot *> $null
    Assert-True ((Get-Content (Join-Path $testRoot "final_classification.json") -Raw).Trim() -eq "[]") "Empty classification is not a JSON array"
    $attribution = @(Get-Content (Join-Path $testRoot "rule_attribution.json") -Raw | ConvertFrom-Json)
    Assert-True ($attribution.Count -eq 2 -and @($attribution | Where-Object TaggedComments -ne 0).Count -eq 0) "Zero-finding coverage failed"
    Remove-Item -LiteralPath (Join-Path $testRoot "review_summaries.json")
    & $classifier -OutputDir $testRoot *> $null
    Assert-True ((Get-Content (Join-Path $testRoot "rule_attribution.json") -Raw).Trim() -eq "[]") "Legacy/no-tag input is not an empty array"

    # Mock only GitHub transport; exercise the collector without credentials/network access.
    $emptyCollectorComments = $false
    $collectorComments = @(
        @{ id = 500; body = "Issue`n$tag"; path = "Cache.kt"; line = 10; pull_request_review_id = 1001; user = @{ login = "Copilot" } },
        @{ id = 501; body = ('```suggestion' + "`n$tag`n" + '```'); path = "Other.kt"; line = 20; pull_request_review_id = 1001; user = @{ login = "copilot-pull-request-reviewer[bot]" } },
        @{ id = 502; body = $tag; in_reply_to_id = 500; user = @{ login = "fixture-author" } }
    )
    $collectorFirstReviewPage = @(
        foreach ($id in 1..30) {
            @{ id = $id; state = "COMMENTED"; user = @{ login = "fixture-author" }; body = "Earlier human review" }
        }
    )
    $collectorReviews = @(
        @{ id = 1001; state = "COMMENTED"; commit_id = $sha; user = @{ login = "Copilot" };
            body = ("Summary " * 50) + "`nReview screen: memory-storage/v1`nGuidance revision: $sha" }
    )
    function gh {
        $command = $args -join " "
        if ($command -match '^auth status ') { return "Logged in" }
        if ($command -match '^api .*/pulls/1/comments(?: |$)') {
            if ($emptyCollectorComments) { return "[]" }
            return (ConvertTo-Json -InputObject $collectorComments -Depth 5)
        }
        if ($command -match '^api .*/pulls/1/commits(?: |$)') { return ("a" * 40) }
        if ($command -match '^api .*/pulls/1/reviews(?: |$)') {
            if ($args -notcontains "--paginate") {
                return (ConvertTo-Json -InputObject $collectorFirstReviewPage -Depth 5)
            }
            if ($args -contains "--slurp") {
                return (ConvertTo-Json -InputObject @($collectorFirstReviewPage, $collectorReviews) -Depth 6)
            }
            ConvertTo-Json -InputObject $collectorFirstReviewPage -Depth 5
            ConvertTo-Json -InputObject $collectorReviews -Depth 5
            return
        }
        throw "Unexpected GitHub invocation in attribution fixture: $command"
    }
    $collector = Join-Path $scripts "analyze.ps1"
    $collectorOutput = & $collector -OutputDir $testRoot *>&1
    $collected = @(Get-Content (Join-Path $testRoot "raw_results.json") -Raw | ConvertFrom-Json)
    Assert-True ($collected.Count -eq 6) "Collector included human replies or missed top-level comments ($($collected.Count)): $($collectorOutput -join [Environment]::NewLine)"
    Assert-True (@($collected | Where-Object RuleId -eq "memory-storage/v1").Count -eq 3) "Collector misattributed quoted tags"
    Assert-True (@($collected | Where-Object ReviewId -eq 1001).Count -eq 6) "Collector lost review IDs"
    $collectedSummaries = @(Get-Content (Join-Path $testRoot "review_summaries.json") -Raw | ConvertFrom-Json)
    Assert-True (@($collectedSummaries | Where-Object MemoryStorageScreened -eq $true).Count -eq 3) "Collector lost screen metadata from the second review page"
    Assert-True ($collectedSummaries[0].GuidanceRevision -eq $sha) "Collector lost guidance revision beyond excerpt"
    Assert-True ($collectedSummaries[0].BodyExcerpt -notmatch 'Review screen:') "Fixture did not exercise excerpt truncation"
    $emptyCollectorComments = $true
    & $collector -OutputDir $testRoot *> $null
    Assert-True ((Get-Content (Join-Path $testRoot "raw_results.json") -Raw).Trim() -eq "[]") "Quiet collection is not a valid empty array"
    & $classifier -OutputDir $testRoot *> $null
    $attribution = @(Get-Content (Join-Path $testRoot "rule_attribution.json") -Raw | ConvertFrom-Json)
    Assert-True ($attribution.Count -eq 3 -and @($attribution | Where-Object TaggedComments -ne 0).Count -eq 0) "Quiet collector-to-classifier attribution failed"

    Write-Host "Rule attribution tests passed."
}
finally {
    $env:TEMP = $originalTemp
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
