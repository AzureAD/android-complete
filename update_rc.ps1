param(
    [Parameter(Mandatory=$true)][UInt32]$rc
)

. ./constants.ps1
. ./helper_methods.ps1

Write-Host "New common version."
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON4J_VERSIONING_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON_VERSIONING_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON_BUILD_GRADLE_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $COMMON_CHANGELOG_FILE

Write-Host "New msal version."
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $MSAL_VERSIONING_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $MSAL_BUILD_GRADLE_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $MSAL_CHANGELOG_FILE 

Write-Host "New broker version: '$brokerRCVersion' and broker4j version: '$broker4jRCVersion' ."
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER_VERSIONING_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER4j_VERSIONING_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER_BUILD_GRADLE_FILE  
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER4J_BUILD_GRADLE_FILE  
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $BROKER_CHANGELOG_FILE 

Write-Host "New adal version: '$msalRCVersion'."
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $ADAL_VERSIONING_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $ADAL_BUILD_GRADLE_FILE
Update-AllRCVersionsInFile -newRCVersion $rc -filePath $ADAL_CHANGELOG_FILE
