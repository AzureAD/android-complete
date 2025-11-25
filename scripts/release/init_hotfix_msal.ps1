param(
    [Parameter(Mandatory=$true)][string]$msalVersion,
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

$msalRCVersion = "$msalVersion-RC1"

# Move to root folder. (android complete)

# Update MSAL version
Write-Host "New msal version: '$msalRCVersion'." -ForegroundColor Green
Update-ChangelogHeaderForHotfix -changelogFile $MSAL_CHANGELOG_FILE -newVersion $msalRCVersion -changelogConstants $changelogConstants -newCommonVersion $CommonVersion

Update-VersionNumber -versioningFile $MSAL_VERSIONING_FILE -newVersion $msalRCVersion