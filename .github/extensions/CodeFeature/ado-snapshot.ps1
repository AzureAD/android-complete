# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

$ErrorActionPreference = 'Stop'
$inputData = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($snapshotInputBase64)) | ConvertFrom-Json
$token = az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv
if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'Azure DevOps authentication failed for PR observation.' }
$headers = @{ Authorization = "Bearer $token" }
$projectBase = "$($inputData.orgUrl)/$([uri]::EscapeDataString($inputData.project))"
$prBase = "$projectBase/_apis/git/repositories/$([uri]::EscapeDataString($inputData.repoName))/pullRequests/$($inputData.prNumber)"
$prUri = $prBase + '?api-version=7.0'
$pr = Invoke-RestMethod -Uri $prUri -Headers $headers -Method Get
$sourceHead = [string]$pr.lastMergeSourceCommit.commitId
$mergeHead = [string]$pr.lastMergeCommit.commitId
$iterations = (Invoke-RestMethod -Uri ($prBase + '/iterations?api-version=7.0') -Headers $headers -Method Get).value
$iteration = ($iterations | Measure-Object -Property id -Maximum).Maximum
$threads = (Invoke-RestMethod -Uri ($prBase + '/threads?api-version=7.0') -Headers $headers -Method Get).value
$statuses = (Invoke-RestMethod -Uri ($prBase + '/statuses?api-version=7.0') -Headers $headers -Method Get).value
$artifact = [uri]::EscapeDataString("vstfs:///CodeReview/CodeReviewId/$($pr.repository.project.id)/$($pr.pullRequestId)")
$policyRows = (Invoke-RestMethod -Uri ("$projectBase/_apis/policy/evaluations?artifactId=$artifact&api-version=7.1-preview.1") -Headers $headers -Method Get).value
$buildRows = (Invoke-RestMethod -Uri ("$projectBase/_apis/build/builds?branchName=refs/pull/$($inputData.prNumber)/merge&`$top=50&queryOrder=queueTimeDescending&api-version=7.0") -Headers $headers -Method Get).value
$diagnosticErrors = @()
$buildsById = @{}
foreach ($build in @($buildRows)) { if ($build.id) { $buildsById[[string]$build.id] = $build } }
foreach ($policy in @($policyRows)) {
    $buildId = [string]$policy.context.buildId
    if ($buildId -and -not $buildsById.ContainsKey($buildId)) {
        try {
            $buildsById[$buildId] = Invoke-RestMethod -Uri ("$projectBase/_apis/build/builds/$buildId`?api-version=7.0") -Headers $headers -Method Get
        } catch { $diagnosticErrors += "Build $buildId metadata unavailable: $($_.Exception.Message)" }
    }
}

function Get-BuildSourceHead($Build) {
    if ($Build.parameters) {
        try {
            $parameters = $Build.parameters | ConvertFrom-Json
            $parameterHead = [string]$parameters.'system.pullRequest.sourceCommitId'
            if ($parameterHead) { return $parameterHead }
        } catch {
            $script:diagnosticErrors += "Build $($Build.id) has invalid PR parameters: $($_.Exception.Message)"
            return ''
        }
    }
    if ($mergeHead -and $Build.sourceVersion -eq $mergeHead) { return $sourceHead }
    return ''
}

function Get-LogText($Url) {
    $textHeaders = @{ Authorization = $headers.Authorization; Accept = 'text/plain' }
    $response = Invoke-RestMethod -Uri ($Url + '?api-version=7.0') -Headers $textHeaders -Method Get
    if ($response -is [string]) { return $response }
    if ($null -ne $response.value) { return $response.value -join "`n" }
    throw 'Build log endpoint did not return text or log lines.'
}

$policies = @($policyRows | ForEach-Object {
    $build = $buildsById[[string]$_.context.buildId]
    @{
        evaluationId = $_.evaluationId; name = $_.configuration.type.displayName
        status = $_.status; blocking = $_.configuration.isBlocking
        buildIsNotCurrent = $_.context.buildIsNotCurrent; buildId = $_.context.buildId
        buildSourceHeadSha = if ($build) { Get-BuildSourceHead $build } else { '' }
        buildDefinitionId = if ($build) { $build.definition.id } else { $null }
    }
})
$currentBuilds = @($buildsById.Values | Where-Object { (Get-BuildSourceHead $_) -eq $sourceHead -and $sourceHead })
$latestBuilds = @($currentBuilds | Group-Object { $_.definition.id } | ForEach-Object {
    $build = $_.Group | Sort-Object id -Descending | Select-Object -First 1
    $failedTasks = @()
    $infrastructureIssues = @()
    $readErrors = @()
    if ($build.result -in @('failed', 'partiallySucceeded', 'canceled', 'cancelled') -or $build.status -in @('inProgress', 'cancelling')) {
        try {
            $timeline = Invoke-RestMethod -Uri ("$projectBase/_apis/build/builds/$($build.id)/timeline?api-version=7.0") -Headers $headers -Method Get
            $infrastructureIssues = @($timeline.records | ForEach-Object { $_.issues } | ForEach-Object { $_.message } |
                Where-Object { $_ -match 'brownout|provider.*unable.*process|hosted.*image.*unavailable|image.*retired' } |
                Select-Object -Unique -First 8)
            $failedRecords = @($timeline.records | Where-Object { $_.type -eq 'Task' -and $_.result -eq 'failed' })
            if (@($failedRecords | Where-Object { $_.name -notmatch 'Publish .* report' }).Count) {
                $failedRecords = @($failedRecords | Where-Object { $_.name -notmatch 'Publish .* report' })
            }
            foreach ($record in $failedRecords) {
                $logText = ''
                $issues = @($record.issues | ForEach-Object { $_.message })
                $failedTests = @()
                if ($record.log.url) {
                    try { $logText = Get-LogText $record.log.url }
                    catch { $readErrors += "Task $($record.name) log unavailable: $($_.Exception.Message)" }
                }
                # Include plugin/feed configuration errors as well as compiler and test failures.
                $excerpt = @($logText -split "`r?`n" | Where-Object {
                    $_ -notmatch '\tat\s|\sw:\s' -and
                    $_ -match 'FAILED|What went wrong|\.kt:\d+|\.java:\d+|Execution failed|weighted issues|Caused by:|Exception|UnknownHostException|error:|fetch-pack:|Plugin \[|could not resolve|could not be resolved|could not find|was not found|401|403|unauthorized|forbidden|HTTP|maven|https://.*(?:pkgs|packaging)'
                } | Select-Object -Last 25)
                $runIds = @([regex]::Matches($logText, '(?i)(?:run id:\s*|test run[^0-9]{0,20}|[?&]runId=)(\d+)') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
                foreach ($runId in $runIds) {
                    try {
                        $results = (Invoke-RestMethod -Uri ("$projectBase/_apis/test/Runs/$runId/results?outcomes=Failed&api-version=7.0") -Headers $headers -Method Get).value
                        foreach ($result in @($results | Select-Object -First 20)) {
                            $failedTests += @{
                                testClass = $result.automatedTestStorage; testName = $result.automatedTestName
                                errorMessage = $result.errorMessage; stackTrace = @(($result.stackTrace -split "`r?`n") | Select-Object -First 8)
                            }
                        }
                    } catch { $readErrors += "Test run $runId diagnostics unavailable: $($_.Exception.Message)" }
                }
                $failedTasks += @{ name = $record.name; issues = $issues; excerpt = $excerpt; failedTests = $failedTests }
            }
            if (-not $failedTasks.Count -and -not $infrastructureIssues.Count -and $build.result -ne 'succeeded') {
                $readErrors += @($timeline.records | Where-Object { $_.result -in @('failed', 'canceled', 'cancelled') } |
                    ForEach-Object { $_.issues } | ForEach-Object { $_.message } | Select-Object -Unique -First 8)
            }
        } catch { $readErrors += "Build $($build.id) timeline unavailable: $($_.Exception.Message)" }
    }
    @{
        id = $build.id; definitionId = $build.definition.id; definition = $build.definition.name
        buildNumber = $build.buildNumber; status = $build.status; result = $build.result
        sourceHeadSha = Get-BuildSourceHead $build; sourceVersion = $build.sourceVersion
        failedTasks = $failedTasks; infrastructureIssues = $infrastructureIssues; diagnosticErrors = $readErrors
    }
})
$latestStatuses = @($statuses | Where-Object { -not $_.iterationId -or $_.iterationId -eq $iteration } |
    Group-Object { "$($_.context.genre)/$($_.context.name)" } |
    ForEach-Object { $_.Group | Sort-Object id -Descending | Select-Object -First 1 | Select-Object state, description, context, iterationId })
$changedFiles = @()
if ($iteration) {
    try {
        $changes = (Invoke-RestMethod -Uri ($prBase + "/iterations/$iteration/changes?api-version=7.0") -Headers $headers -Method Get).changeEntries
        $changedFiles = @($changes | ForEach-Object { ([string]$_.item.path) -replace '^/+', '' } | Select-Object -Unique -First 50)
    } catch { $diagnosticErrors += "Changed-file metadata unavailable: $($_.Exception.Message)" }
}
$latestPr = Invoke-RestMethod -Uri $prUri -Headers $headers -Method Get
$headChanged = $latestPr.lastMergeSourceCommit.commitId -ne $sourceHead -or $latestPr.lastMergeCommit.commitId -ne $mergeHead
$out = @{
    provider = 'ado'; number = $pr.pullRequestId; title = $pr.title; description = $pr.description
    state = $pr.status; mergeStateStatus = $pr.mergeStatus
    headRefName = ($pr.sourceRefName -replace '^refs/heads/', ''); headRefOid = $sourceHead; mergeHeadSha = $mergeHead
    baseRefName = ($pr.targetRefName -replace '^refs/heads/', ''); iterationId = $iteration
    snapshotHeadChanged = $headChanged; diagnosticErrors = $diagnosticErrors
    statusCheckRollup = $latestStatuses; prBuilds = $latestBuilds; policyEvaluations = $policies; changedFiles = $changedFiles
    activeReviewThreads = @($threads | Where-Object { -not $_.isDeleted -and $_.status -in @('active', 1) } |
        ForEach-Object { @{ id = $_.id; status = $_.status; filePath = $_.threadContext.filePath; comments = @($_.comments |
            ForEach-Object { @{ id = $_.id; author = $_.author.displayName; content = $_.content } }) } })
    url = $inputData.prUrl
}
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Console]::Out.Write(($out | ConvertTo-Json -Depth 12 -Compress))
