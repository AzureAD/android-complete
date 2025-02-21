param(
    [Parameter(Mandatory=$true)][string]$msalVersion,
    [Parameter(Mandatory=$true)][string]$brokerVersion,
    [Parameter(Mandatory=$true)][string]$CommonVersion,
    [Parameter(Mandatory=$true)][string]$Common4jVersion,
    [Parameter(Mandatory=$true)][string]$broker4jVersion
)


# Get the path of the current script
$scriptPath = $PSScriptRoot

# Get all PS1 files in a specific folder
$filesToInclude = Get-ChildItem -Path "$scriptPath/libs" -Filter "*.ps1" -Recurse 

# Dot-source each file
foreach ($file in $filesToInclude) {
    . $file.FullName
}


$msalRCVersion = "$msalVersion-RC1"
$brokerRCVersion = "$brokerVersion-RC1"
$CommonRCVersion = "$CommonVersion-RC1"
$Common4jRCVersion = "$Common4jVersion-RC1"
$broker4jRCVersion = "$broker4jVersion-RC1"

# Move to root folder. (android complete)

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
