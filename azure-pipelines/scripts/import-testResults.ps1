Param (
    [Parameter(Mandatory = $true)][String]$AdoPAT,
    [Parameter(Mandatory = $false)][String]$Organization = "identitydivision",
    [Parameter(Mandatory = $false)][String]$Project = "Engineering",
    [Parameter(Mandatory = $true)][String]$SourceBuildId,
    [Parameter(Mandatory = $true)][String]$TargetBuildId
)


#request Uris
$apiVersion = "7.1-preview.3"
$baseUri = "https://dev.azure.com/$Organization/$Project";
$testRunUri = "$baseUri/_apis/test/runs?api-version=$apiVersion"

$minLastUpdatedDate = Get-Date (Get-Date).AddDays(-1) -Format "MM/dd/yyyy"
$maxLastUpdatedDate = Get-Date (Get-Date).AddDays(1) -Format "MM/dd/yyyy"
$queryRunsUri = "$baseUri/_apis/test/runs?minLastUpdatedDate=$minLastUpdatedDate&maxLastUpdatedDate=$maxLastUpdatedDate&buildIds=$SourceBuildId&api-version=$apiVersion"
Write-Host $queryRunsUri

# Auth header
$base64AuthInfo = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes(("token:{0}" -f $AdoPAT)))
$authHeader = @{Authorization = ("Basic {0}" -f $base64AuthInfo)};


# Call ADO Rest APIs
try {
    Write-Host "Get TestResults from $SourceBuildId"
    $testRuns = Invoke-RestMethod -Uri $queryRunsUri -Method Get -Headers $authHeader
    foreach ($testRun in $testRuns.value) {
        $testRunId = $testRun.id
        $testResultsApi = "_apis/test/runs/$testRunId/results?api-version=$apiVersion"
        $testResultsUri = "$baseUri/$testResultsApi"
        $testResults  = Invoke-RestMethod -Uri $testResultsUri -Method Get -Headers $authHeader;

        Write-Host "Create a new Test Run on the $TargetBuildId"
        $createRunParam = New-Object PSObject -Property @{
                            name = $testRun.name
                            build = @{ id = "$TargetBuildId" }
                            isAutomated = $testRun.isAutomated
                       }

        $createRunRequest = $createRunParam | ConvertTo-Json
        $createRunResult = Invoke-RestMethod -Uri $testRunUri -ContentType "application/json" -Headers $authHeader -Method Post -Body $createRunRequest

        $targetRunId = $createRunResult.id
        Write-Host "Import the testResults $testRunId in new test run $targetRunId" 
        
        foreach ($testResult in $testResults.value) {
            $testResult.testRun.id = $targetRunId
        }

        $targetRunUri = "$baseUri/_apis/test/runs/$($targetRunId)?api-version=$apiVersion"
        $updateResultsUri = "$baseUri/_apis/test/runs/$targetRunId/results?api-version=$apiVersion"
        $updateResultRequest = $testResults.value | ConvertTo-Json
        Invoke-RestMethod -Uri $updateResultsUri -ContentType "application/json" -Headers $authHeader -Method Post -Body $updateResultRequest

        Write-Host "Mark the new test run $targetRunId as completed"
        $testRunCompleteRequest = @{state = "completed"} | ConvertTo-Json        
        Invoke-RestMethod -Uri $targetRunUri -ContentType "application/json" -Headers $authHeader -Method Patch -Body $testRunCompleteRequest
    }
    
} catch {
    if($_.ErrorDetails.Message){
        Write-Error $_.ErrorDetails.Message
    }
    throw $_.Exception
}
