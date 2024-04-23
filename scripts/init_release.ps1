param(
    [Parameter(Mandatory=$true)][string]$msalVersion,
    [Parameter(Mandatory=$true)][string]$brokerVersion,
    [Parameter(Mandatory=$true)][string]$adalVersion,
    [Parameter(Mandatory=$true)][string]$CommonVersion,
    [Parameter(Mandatory=$true)][string]$Common4jVersion,
    [Parameter(Mandatory=$true)][string]$broker4jVersion,
    [Parameter(Mandatory=$true)][Bool]$isMajorMsalChange,
    [Parameter(Mandatory=$true)][Bool]$isMajorAdalChange,
    [Parameter(Mandatory=$true)][Bool]$isMajorBrokerChange
)

. ./constants.ps1
. ./helper_methods.ps1

$msalRCVersion = "$msalVersion-RC1"
$brokerRCVersion = "$brokerVersion-RC1"
$adalRCVersion = "$adalVersion-RC1"
$CommonRCVersion = "$CommonVersion-RC1"
$Common4jRCVersion = "$Common4jVersion-RC1"
$broker4jRCVersion = "$broker4jVersion-RC1"

# Move to root folder. (android complete)
Set-Location ..

# Update COMMON and COMMON4J version
Write-Host "New common version: '$commonRCVersion' and common4j version: '$common4jRCVersion' ." -ForegroundColor Green
Update-ChangelogHeader -changelogFile  $COMMON_CHANGELOG_FILE    -newVersion $commonRCVersion  -changelogConstants $changelogConstants

Update-VersionNumber -versioningFile $COMMON4J_VERSIONING_FILE -newVersion $common4jRCVersion
Update-VersionNumber -versioningFile $COMMON_VERSIONING_FILE -newVersion $commonRCVersion   

Update-GradeFile -gradleFile $COMMON_BUILD_GRADLE_FILE -variableToUpdate $GRADLE_COMMON4J_VAR -newVersion $common4jRCVersion 

# Update MSAL version
Write-Host "New msal version: '$msalRCVersion'." -ForegroundColor Green
Update-ChangelogHeader -changelogFile  $MSAL_CHANGELOG_FILE -newVersion $msalRCVersion -changelogConstants $changelogConstants -newCommonVersion $commonRCVersion

Update-VersionNumber -versioningFile $MSAL_VERSIONING_FILE -newVersion $msalRCVersion    

Update-GradeFile -gradleFile $MSAL_BUILD_GRADLE_FILE -variableToUpdate $GRADLE_COMMON4J_VAR -newVersion $common4jRCVersion   
Update-GradeFile -gradleFile $MSAL_BUILD_GRADLE_FILE -variableToUpdate $GRADLE_COMMON_VAR -newVersion $commonRCVersion    

# Update BROKER and BROKER4J version
Write-Host "New broker version: '$brokerRCVersion' and broker4j version: '$broker4jRCVersion' ." -ForegroundColor Green
Update-ChangelogHeader  -changelogFile $BROKER_CHANGELOG_FILE -newVersion $brokerRCVersion -changelogConstants $changelogConstants -newCommonVersion $commonRCVersion   

Update-VersionNumber -versioningFile $BROKER_VERSIONING_FILE -newVersion $brokerRCVersion   
Update-VersionNumber -versioningFile $BROKER4j_VERSIONING_FILE -newVersion $broker4jRCVersion 

Update-GradeFile -gradleFile $BROKER4J_BUILD_GRADLE_FILE -variableToUpdate $GRADLE_COMMON4J_VAR  -newVersion $common4JRCVersion   

Update-GradeFile -gradleFile $BROKER_BUILD_GRADLE_FILE -variableToUpdate $GRADLE_COMMON_VAR -newVersion $commonRCVersion    
Update-GradeFile -gradleFile $BROKER_BUILD_GRADLE_FILE -variableToUpdate $GRADLE_BROKER4J_VAR -newVersion $broker4jRCVersion  
Update-GradeFile -gradleFile $BROKER_BUILD_GRADLE_FILE -variableToUpdate $GRADLE_COMMON4J_VAR -newVersion $common4JRCVersion    

# Update ADAL version
Write-Host "New adal version: '$adalRCVersion'." -ForegroundColor Green
Update-ChangelogHeader -changelogFile $ADAL_CHANGELOG_FILE -newVersion $adalRCVersion -changelogConstants $changelogConstants -newCommonVersion $commonRCVersion 

Update-VersionNumber -versioningFile $ADAL_VERSIONING_FILE -newVersion $adalRCVersion     

Update-GradeFile -gradleFile $ADAL_BUILD_GRADLE_FILE -variableToUpdate $GRADLE_COMMON_VAR -newVersion $commonRCVersion   


if ($isMajorMsalChange)
{
    Write-Host "Major MSAL change, update msalautomationapp" -ForegroundColor Yellow
    # Upadte msal automation app
    $majorMsalVersion = [regex]::Match($msalVersion, '\d+').Value
    Update-GradeFile -gradleFile $MSALAUTOMATIONAPP_BUILD_GRADLE_FILE   -variableToUpdate $GRADLE_MSAL_VAR   -newVersion "$majorMsalVersion.+" 
}
if ($isMajorBrokerChange) {
    #Update broker automation app
    Write-Host "Major BROKER change, update brokerautomationapp" -ForegroundColor Yellow

    $majorCommonVersion = [regex]::Match($CommonVersion, '\d+').Value
    Update-GradeFile -gradleFile $BROKERAUTOMATIONAPP_BUILD_GRADLE_FILE -variableToUpdate $GRADLE_COMMON_VAR -newVersion "$majorCommonVersion.+" 
}
if ($isMajorAdalChange)
{
    Write-Host "Major ADAL change, update brokerautomationapp" -ForegroundColor Yellow
    $majorAdalVersion = [regex]::Match($adalVersion, '\d+').Value
    Update-GradeFile -gradleFile $BROKERAUTOMATIONAPP_BUILD_GRADLE_FILE -variableToUpdate $GRADLE_ADAL_VAR   -newVersion "$majorAdalVersion.+"

}

# Return to scripts folder
Set-Location .\scripts