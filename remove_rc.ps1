
. ./constants.ps1
. ./helper_methods.ps1

Write-Host "New common version."
Remove-AllRCVersionsInFile -filePath $COMMON4J_VERSIONING_FILE
Remove-AllRCVersionsInFile -filePath $COMMON_VERSIONING_FILE
Remove-AllRCVersionsInFile -filePath $COMMON_BUILD_GRADLE_FILE
Remove-AllRCVersionsInFile -filePath $COMMON_CHANGELOG_FILE

Write-Host "New msal version."
Remove-AllRCVersionsInFile -filePath $MSAL_VERSIONING_FILE
Remove-AllRCVersionsInFile -filePath $MSAL_BUILD_GRADLE_FILE
Remove-AllRCVersionsInFile -filePath $MSAL_CHANGELOG_FILE 

Write-Host "New broker version: '$brokerRCVersion' and broker4j version: '$broker4jRCVersion' ."
Remove-AllRCVersionsInFile -filePath $BROKER_VERSIONING_FILE
Remove-AllRCVersionsInFile -filePath $BROKER4j_VERSIONING_FILE
Remove-AllRCVersionsInFile -filePath $BROKER4j_BUILD_GRADLE_FILE  
Remove-AllRCVersionsInFile -filePath $BROKER_BUILD_GRADLE_FILE  
Remove-AllRCVersionsInFile -filePath $BROKER_CHANGELOG_FILE 

Write-Host "New adal version: '$msalRCVersion'."
Remove-AllRCVersionsInFile -filePath $ADAL_VERSIONING_FILE
Remove-AllRCVersionsInFile -filePath $ADAL_BUILD_GRADLE_FILE
Remove-AllRCVersionsInFile -filePath $ADAL_CHANGELOG_FILE
