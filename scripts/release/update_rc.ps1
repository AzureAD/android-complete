param(
    [Parameter(Mandatory=$true)][UInt32]$rc,
    [Parameter(Mandatory=$false)][switch]$skipCommon,
    [Parameter(Mandatory=$false)][switch]$skipMsal,
    [Parameter(Mandatory=$false)][switch]$skipBroker
)

# Get the path of the current script
$scriptPath = $PSScriptRoot

# Get all PS1 files in a specific folder
$filesToInclude = Get-ChildItem -Path "$scriptPath/libs" -Filter "*.ps1" -Recurse 

# Dot-source each file
foreach ($file in $filesToInclude) {
    . $file.FullName
}

if (-not $skipCommon) {
    Write-Host "Update common and common4j." -ForegroundColor Green
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON4J_VERSIONING_FILE
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON_VERSIONING_FILE
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON_BUILD_GRADLE_FILE
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON_CHANGELOG_FILE
} else {
    Write-Host "Skipping common and common4j RC update." -ForegroundColor Yellow
}

if (-not $skipMsal) {
    Write-Host "Update msal." -ForegroundColor Green
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $MSAL_VERSIONING_FILE
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $MSAL_BUILD_GRADLE_FILE
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $MSAL_CHANGELOG_FILE 
} else {
    Write-Host "Skipping msal RC update." -ForegroundColor Yellow
}

if (-not $skipBroker) {
    Write-Host "Update broker and broker4j." -ForegroundColor Green
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER_VERSIONING_FILE
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER4j_VERSIONING_FILE
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER_BUILD_GRADLE_FILE  
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER4J_BUILD_GRADLE_FILE  
    Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER_CHANGELOG_FILE 
} else {
    Write-Host "Skipping broker and broker4j RC update." -ForegroundColor Yellow
} 
