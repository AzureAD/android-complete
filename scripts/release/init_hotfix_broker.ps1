param(
    [Parameter(Mandatory=$true)][string]$brokerVersion,
    [Parameter(Mandatory=$true)][string]$CommonVersion
)

# Get the path of the current script
$scriptPath = $PSScriptRoot

# Get all PS1 files in a specific folder
$filesToInclude = Get-ChildItem -Path "$scriptPath/libs" -Filter "*.ps1" -Recurse 

# Dot-source each file
foreach ($file in $filesToInclude) {
    . $file.FullName
}

$brokerRCVersion = "$brokerVersion-RC1"
$broker4jRCVersion = "$brokerVersion-RC1"  # Same as brokerVersion

# Move to root folder. (android complete)

# Update BROKER and BROKER4J version
Write-Host "New broker version: '$brokerRCVersion' and broker4j version: '$broker4jRCVersion' ." -ForegroundColor Green
Update-ChangelogHeaderForHotfix -changelogFile $BROKER_CHANGELOG_FILE -newVersion $brokerRCVersion -changelogConstants $changelogConstants -newCommonVersion $CommonVersion

Update-VersionNumber -versioningFile $BROKER_VERSIONING_FILE -newVersion $brokerRCVersion   
Update-VersionNumber -versioningFile $BROKER4j_VERSIONING_FILE -newVersion $broker4jRCVersion 

Update-GradeFile -gradleFile $BROKER_BUILD_GRADLE_FILE -variableToUpdate $GRADLE_BROKER4J_VAR -newVersion $broker4jRCVersion  
