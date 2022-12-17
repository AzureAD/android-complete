Param (
    [Parameter(Mandatory = $true)][String]$OrganizationUrl,
    [Parameter(Mandatory = $true)][String]$Project,
    [Parameter(Mandatory = $true)][String]$PipelinePAT,
    [Parameter(Mandatory = $true)][String]$BuildDefinitionId,
    [Parameter(Mandatory = $false)][String]$PipelineVariablesJson,
    [Parameter(Mandatory = $false)][String]$Branch
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

# Wait for build completion
$getBuildUri="$($baseUri)_apis/build/builds/$($Result.id)?api-version=7.0"
$BuildStartTime= Get-Date
do{
   try {
       $QueuedBuild = Invoke-RestMethod -Uri $getBuildUri -Method Get -ContentType "application/json" -Headers $authHeader;
       Write-Host $($QueuedBuild.status)
       $BuildNotCompleted = ($($QueuedBuild.status) -eq "inProgress") -Or ($($QueuedBuild.status) -eq "notStarted")
       if($BuildNotCompleted){
           Start-Sleep -Seconds 300
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
} while($BuildNotCompleted -and $BuildStartTime.AddMinutes(60) -gt (Get-Date))

if ($BuildNotCompleted) {
    Write-Error "Timed out waiting for Build $($baseUri)_build/results?buildId=$($QueuedBuild.id) to complete,"
} else {
    Write-Host "Build $($baseUri)_build/results?buildId=$($QueuedBuild.id) completed with result $($QueuedBuild.result)"
}
