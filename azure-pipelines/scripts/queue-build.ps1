Param (
    [Parameter(Mandatory = $true)][String]$OrganizationUrl,
    [Parameter(Mandatory = $true)][String]$Project,
    [Parameter(Mandatory = $true)][String]$PipelinePAT,
    [Parameter(Mandatory = $true)][String]$BuildDefinitionId,
    [Parameter(Mandatory = $false)][String]$PipelineVariablesJson,
    [Parameter(Mandatory = $false)][String]$Branch,
    [Parameter(Mandatory = $false)][int]$WaitTimeoutInMinutes = 100,
    [Parameter(Mandatory = $false)][int]$PollingIntervalInSeconds = 5 * 60
)

#request uri
$baseUri = "$($OrganizationUrl)/$($Project)/";
$queueBuild = "_apis/build/builds?api-version=7.0"
$queueBuildUri = "$($baseUri)$($queueBuild)"

# Auth header
$base64AuthInfo = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes(("token:{0}" -f $PipelinePAT)))
$authHeader = @{Authorization = ("Basic {0}" -f $base64AuthInfo)};

# Request Body
$Build = New-Object PSObject -Property @{
        definition = New-Object PSObject -Property @{
            id = $BuildDefinitionId
        }
        sourceBranch = $Branch
        reason = "userCreated"
        parameters = $PipelineVariablesJson
    }

$requestBody = $Build | ConvertTo-Json

# Call ADO Rest API
try {
    $Result = Invoke-RestMethod -Uri $queueBuildUri -Method Post -ContentType "application/json" -Headers $authHeader -Body $requestBody;
} catch {
    if($_.ErrorDetails.Message){
        $errorObject = $_.ErrorDetails.Message | ConvertFrom-Json
        foreach($result in $errorObject.customProperties.ValidationResults){
            Write-Warning $result.message
        }
        Write-Error $errorObject.message
    }
    throw $_.Exception
}

Write-Host "Build is queued: $($baseUri)_build/results?buildId=$($Result.id)"

Write-Host "##vso[task.setvariable variable=BrokeBuildId;isoutput=true]$($Result.id)"

# Wait for build completion
$getBuildUri="$($baseUri)_apis/build/builds/$($Result.id)?api-version=7.0"
$BuildStartTime= Get-Date
do{
   try {
       $QueuedBuild = Invoke-RestMethod -Uri $getBuildUri -Method Get -ContentType "application/json" -Headers $authHeader;
       Write-Host $($QueuedBuild.status)
       $BuildNotCompleted = ($($QueuedBuild.status) -eq "inProgress") -Or ($($QueuedBuild.status) -eq "notStarted")
       if($BuildNotCompleted){
           Start-Sleep -Seconds $PollingIntervalInSeconds
       }
   } catch {
       if($_.ErrorDetails.Message){
           $errorObject = $_.ErrorDetails.Message | ConvertFrom-Json
           foreach($result in $errorObject.customProperties.ValidationResults){
               Write-Warning $result.message
           }
           Write-Error $errorObject.message
       }
       throw $_.Exception
   }
} while($BuildNotCompleted -and $BuildStartTime.AddMinutes($WaitTimeoutInMinutes) -gt (Get-Date))

if ($BuildNotCompleted) {
    Write-Error "Timed out waiting for Build $($baseUri)_build/results?buildId=$($QueuedBuild.id) to complete,"
} elseif ($($QueuedBuild.result) -eq "succeeded"){
    Write-Host "Build $($baseUri)_build/results?buildId=$($QueuedBuild.id) completed successfully."
} else {
    Write-Error "Build $($baseUri)_build/results?buildId=$($QueuedBuild.id) did not complete successfully, BuildResult: $($QueuedBuild.result)."
}
