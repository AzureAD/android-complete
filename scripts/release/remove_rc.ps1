# Get the path of the current script
$scriptPath = $PSScriptRoot

# Get all PS1 files in a specific folder
$filesToInclude = Get-ChildItem -Path "$scriptPath/libs" -Filter "*.ps1" -Recurse 

# Dot-source each file
foreach ($file in $filesToInclude) {
    . $file.FullName
}

Write-Host "New common version." -ForegroundColor Green
Remove-AllRCVersionsInFile -filePath $COMMON4J_VERSIONING_FILE
Remove-AllRCVersionsInFile -filePath $COMMON_VERSIONING_FILE
Remove-AllRCVersionsInFile -filePath $COMMON_BUILD_GRADLE_FILE
Remove-AllRCVersionsInFile -filePath $COMMON_CHANGELOG_FILE

Write-Host "New msal version." -ForegroundColor Green
Remove-AllRCVersionsInFile -filePath $MSAL_VERSIONING_FILE
Remove-AllRCVersionsInFile -filePath $MSAL_BUILD_GRADLE_FILE
Remove-AllRCVersionsInFile -filePath $MSAL_CHANGELOG_FILE 

Write-Host "New broker and broker4j version." -ForegroundColor Green
Remove-AllRCVersionsInFile -filePath $BROKER_VERSIONING_FILE
Remove-AllRCVersionsInFile -filePath $BROKER4j_VERSIONING_FILE
Remove-AllRCVersionsInFile -filePath $BROKER4j_BUILD_GRADLE_FILE  
Remove-AllRCVersionsInFile -filePath $BROKER_BUILD_GRADLE_FILE  
Remove-AllRCVersionsInFile -filePath $BROKER_CHANGELOG_FILE 
