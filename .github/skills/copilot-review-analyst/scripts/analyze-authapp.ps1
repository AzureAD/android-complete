# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

<#
.SYNOPSIS
    Collect Foundry reviews from AuthApp into the shared analyst schema.
.DESCRIPTION
    Prefer an InputFile exported using msazure-scoped ADO MCP tools. When those
    tools cannot reach msazure, use Azure CLI with an explicit organization.
    InputFile is an array of { pullRequest, threads, iterations } objects.
#>
param(
    [string]$OutputDir = "$env:TEMP\copilot-review-analysis",
    [string]$StartDate = (Get-Date).AddDays(-60).ToString("yyyy-MM-dd"),
    [string]$EndDate = (Get-Date).ToString("yyyy-MM-dd"),
    [string]$InputFile = "",
    [int[]]$PRNumbers = @(),
    [string]$ReviewerDisplayName = "One Build Service (msazure)",
    [string]$ReviewerMarker = "AuthApp PR Reviewer"
)

$ErrorActionPreference = "Stop"
$organization = "https://dev.azure.com/msazure"
$project = "One"
$repositoryName = "AD-MFA-phonefactor-phoneApp-android"
$start = [datetime]::ParseExact($StartDate, "yyyy-MM-dd", $null)
$endExclusive = [datetime]::ParseExact($EndDate, "yyyy-MM-dd", $null).AddDays(1)
if ($start -ge $endExclusive) { throw "StartDate must not be after EndDate." }
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

function Invoke-AuthAppApi {
    param([string]$Resource, [hashtable]$Route, [hashtable]$Query = @{})
    $arguments = @("devops", "invoke", "--organization", $organization,
        "--area", "git", "--resource", $Resource, "--api-version", "7.1",
        "--output", "json", "--only-show-errors", "--route-parameters", "project=$project")
    foreach ($key in $Route.Keys) { $arguments += "$key=$($Route[$key])" }
    if ($Query.Count -gt 0) {
        $arguments += "--query-parameters"
        foreach ($key in $Query.Keys) { $arguments += "$key=$($Query[$key])" }
    }
    $json = & az @arguments
    if ($LASTEXITCODE -ne 0) { throw "AuthApp API request failed: $Resource. Check msazure access and Azure CLI authentication." }
    if (-not $json) { throw "AuthApp API request returned no JSON: $Resource." }
    return ($json -join "`n" | ConvertFrom-Json)
}

function Test-ReviewComment {
    param($Comment)
    return ($Comment.author.displayName -eq $ReviewerDisplayName -and
        $Comment.content -match [regex]::Escape($ReviewerMarker))
}

function Test-TextComment {
    param($Comment)
    return (-not $Comment.isDeleted -and $Comment.commentType -in @("text", 1) -and
        -not [string]::IsNullOrWhiteSpace($Comment.content))
}

function Test-ServiceIdentity {
    param($Identity)
    return ($Identity.displayName -eq $ReviewerDisplayName -or
        $Identity.displayName -match '(?i)build service|Microsoft\.VisualStudio\.Services|GitOps|Enforcer|\[bot\]' -or
        $Identity.uniqueName -match '^(?i)(Build|Project Collection Build Service);')
}

$bundles = @()
if ($InputFile) {
    $bundles = @(Get-Content -LiteralPath $InputFile -Raw | ConvertFrom-Json)
} else {
    $repository = Invoke-AuthAppApi -Resource "repositories" -Route @{ repositoryId = $repositoryName }
    if (-not $repository.id) { throw "AuthApp repository was not resolved in msazure/One." }
    $prs = @()
    if ($PRNumbers.Count -gt 0) {
        foreach ($number in $PRNumbers) {
            $prs += Invoke-AuthAppApi -Resource "pullRequests" -Route @{
                repositoryId = $repository.id; pullRequestId = $number
            }
        }
    } else {
        $skip = 0
        do {
            $page = Invoke-AuthAppApi -Resource "pullRequests" -Route @{ repositoryId = $repository.id } -Query @{
                'searchCriteria.repositoryId' = $repository.id
                'searchCriteria.status' = 'completed'
                'searchCriteria.queryTimeRangeType' = 'closed'
                'searchCriteria.minTime' = $start.ToString("yyyy-MM-ddT00:00:00Z")
                'searchCriteria.maxTime' = $endExclusive.AddTicks(-1).ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ")
                '$top' = 100; '$skip' = $skip
            }
            if ($null -eq $page.value) { throw "AuthApp PR list lacks the expected value array." }
            $prs += @($page.value)
            $skip += 100
        } while (@($page.value).Count -eq 100)
    }
    foreach ($pr in $prs) {
        if ($pr.repository.id -ne $repository.id) {
            throw "AuthApp PR list returned PR $($pr.pullRequestId) from another repository; refusing a mixed corpus."
        }
        if ($pr.status -ne "completed" -or -not $pr.closedDate -or
            [datetime]$pr.closedDate -lt $start -or [datetime]$pr.closedDate -ge $endExclusive -or
            (Test-ServiceIdentity $pr.createdBy)) { continue }
        $route = @{ repositoryId = $repository.id; pullRequestId = $pr.pullRequestId }
        $threads = Invoke-AuthAppApi -Resource "pullRequestThreads" -Route $route
        $iterations = Invoke-AuthAppApi -Resource "pullRequestIterations" -Route $route
        if ($null -eq $threads.value -or $null -eq $iterations.value) {
            throw "AuthApp PR $($pr.pullRequestId) is missing thread or iteration data."
        }
        $bundles += [pscustomobject]@{
            pullRequest = $pr; threads = @($threads.value); iterations = @($iterations.value)
        }
    }
}

$results = @()
$summaries = @()
$humanPRs = @()
$reviewed = @{}
$seenPRs = @{}
$excludedServiceComments = 0
foreach ($bundle in $bundles) {
    $pr = $bundle.pullRequest
    if (-not $pr.pullRequestId -or -not $pr.repository.id -or
        $pr.repository.name -ne $repositoryName -or -not $pr.createdBy.id -or
        -not $pr.closedDate -or $null -eq $bundle.threads -or $null -eq $bundle.iterations) {
        throw "Each AuthApp input must contain a full PR, threads array, and iterations array."
    }
    if ($pr.repository.url -notmatch '^https://(?:dev\.azure\.com/msazure/|msazure\.visualstudio\.com/)') {
        throw "AuthApp input repository is not in msazure."
    }
    if ($pr.status -ne "completed" -or [datetime]$pr.closedDate -lt $start -or
        [datetime]$pr.closedDate -ge $endExclusive -or (Test-ServiceIdentity $pr.createdBy)) { continue }
    if ($seenPRs.ContainsKey("$($pr.pullRequestId)")) { throw "Duplicate AuthApp PR $($pr.pullRequestId) in input." }
    $seenPRs["$($pr.pullRequestId)"] = $true
    $authorKey = "ado:msazure:$($pr.createdBy.id)"
    $humanPRs += [pscustomobject]@{
        number = $pr.pullRequestId; title = $pr.title
        author = @{ login = $authorKey; name = $pr.createdBy.displayName }
        mergedAt = $pr.closedDate; state = "MERGED"
    }
    foreach ($thread in $bundle.threads) {
        if ($thread.isDeleted) { continue }
        $comments = @($thread.comments | Where-Object { Test-TextComment $_ } | Sort-Object id)
        $root = $comments | Select-Object -First 1
        if (-not $root) { continue }
        if (-not (Test-ReviewComment $root)) {
            if ($root.author.displayName -eq $ReviewerDisplayName) { $excludedServiceComments++ }
            continue
        }
        $reviewed["$($pr.pullRequestId)"] = $true
        $url = "https://msazure.visualstudio.com/One/_git/$repositoryName/pullrequest/$($pr.pullRequestId)?discussionId=$($thread.id)"
        $key = "ado:msazure:$($pr.repository.id):$($pr.pullRequestId):$($thread.id):$($root.id)"
        if (-not $thread.threadContext.filePath) {
            $summaries += [pscustomobject]@{
                Repo = "authapp"; ReviewSource = "Foundry"; Platform = "Azure DevOps"
                PRNumber = $pr.pullRequestId; PRAuthor = $authorKey; PRTitle = $pr.title
                ReviewId = $key; ThreadId = $thread.id; Body = $root.content; CommentUrl = $url
            }
            continue
        }
        # ADO follow-ups can have parentCommentId=0; thread membership and order matter.
        $replies = @($comments | Where-Object {
            $_.id -gt $root.id -and -not (Test-ServiceIdentity $_.author) -and
            -not (Test-ReviewComment $_)
        })
        $iterationId = $thread.pullRequestThreadContext.iterationContext.secondComparingIteration
        $iteration = @($bundle.iterations | Where-Object { $_.id -eq $iterationId }) | Select-Object -First 1
        $originalCommit = $iteration.sourceRefCommit.commitId
        $context = $thread.threadContext
        $side = if ($context.rightFileStart) { "RIGHT" } else { "LEFT" }
        $lineStart = if ($side -eq "RIGHT") { $context.rightFileStart.line } else { $context.leftFileStart.line }
        $lineEnd = if ($side -eq "RIGHT") { $context.rightFileEnd.line } else { $context.leftFileEnd.line }
        $results += [pscustomobject]@{
            Repo = "authapp"; ReviewSource = "Foundry"; Platform = "Azure DevOps"
            PRNumber = $pr.pullRequestId; PRAuthor = $authorKey; PRAuthorName = $pr.createdBy.displayName
            PRTitle = $pr.title; PRState = "MERGED"; CommentId = $key
            NativeCommentId = $root.id; ThreadId = $thread.id; RepositoryId = $pr.repository.id
            CommentUrl = $url; ReviewerName = $root.author.displayName; ReviewerId = $root.author.id
            FilePath = $context.filePath; Line = $lineEnd; StartLine = $lineStart; Side = $side
            CommentBody = $root.content; CommentExcerpt = $root.content
            HumanReplyCount = $replies.Count; HumanReplyText = ($replies.content -join "`n---`n")
            HumanReplies = @($replies | ForEach-Object {
                [pscustomobject]@{ Id = $_.id; AuthorId = $_.author.id; Body = $_.content; CreatedAt = $_.publishedDate }
            })
            HasReply = ($replies.Count -gt 0); CommentType = "inline"
            ThreadStatus = $thread.status; ReviewedIteration = $iterationId
            OriginalCommitId = $originalCommit; FinalCommitId = $pr.lastMergeSourceCommit.commitId
            DiffHunk = $null; ReviewedCommitRewritten = $null
            SnapshotNeedsValidation = $true
            ThumbsUp = 0; ThumbsDown = 0; ApplyCandidate = $false; ApplyTier = $null
        }
    }
}

$feedbackPRs = @($results | Select-Object -ExpandProperty PRNumber -Unique).Count
$coverage = [pscustomobject]@{
    repo = "authapp"; reviewSource = "Foundry"; platform = "Azure DevOps"
    mergedHumanPRs = $humanPRs.Count; reviewedByAI = $reviewed.Count
    withInlineFeedback = $feedbackPRs; noFeedback = $reviewed.Count - $feedbackPRs
    reviewCoveragePct = if ($humanPRs.Count -gt 0) { [math]::Round($reviewed.Count / $humanPRs.Count * 100, 1) } else { 0 }
    excludedUnattributedServiceThreads = $excludedServiceComments
}
ConvertTo-Json -InputObject @($results) -Depth 15 | Set-Content "$OutputDir\authapp_raw_results.json" -Encoding utf8
ConvertTo-Json -InputObject @($summaries) -Depth 8 | Set-Content "$OutputDir\authapp_review_summaries.json" -Encoding utf8
ConvertTo-Json -InputObject @($humanPRs) -Depth 5 | Set-Content "$OutputDir\authapp_prs.json" -Encoding utf8
$coverage | ConvertTo-Json -Depth 5 | Set-Content "$OutputDir\authapp_coverage.json" -Encoding utf8
Write-Host "AuthApp / Foundry: $($results.Count) inline findings, $($summaries.Count) summaries, $($humanPRs.Count) merged human PRs."
if ($excludedServiceComments -gt 0) {
    Write-Warning "$excludedServiceComments build-service threads lacked the reviewer marker and were excluded; inspect for reviewer format changes."
}
