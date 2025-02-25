param(
    [Parameter(Mandatory=$true)][UInt32]$rc
)

# Get the path of the current script
$scriptPath = $PSScriptRoot

# Get all PS1 files in a specific folder
$filesToInclude = Get-ChildItem -Path "$scriptPath/libs" -Filter "*.ps1" -Recurse 

# Dot-source each file
foreach ($file in $filesToInclude) {
    . $file.FullName
}

Write-Host "Update common and common4j." -ForegroundColor Green
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON4J_VERSIONING_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON_VERSIONING_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON_BUILD_GRADLE_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON_CHANGELOG_FILE

Write-Host "Update msal." -ForegroundColor Green
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $MSAL_VERSIONING_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $MSAL_BUILD_GRADLE_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $MSAL_CHANGELOG_FILE 

Write-Host "Update broker and broker4j." -ForegroundColor Green
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER_VERSIONING_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER4j_VERSIONING_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER_BUILD_GRADLE_FILE  
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER4J_BUILD_GRADLE_FILE  
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER_CHANGELOG_FILE 
