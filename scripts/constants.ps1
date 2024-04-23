
New-Variable -Name GRADLE_BROKER4J_VAR -Value "broker4jVersion" -Option Constant
New-Variable -Name GRADLE_COMMON4J_VAR -Value "common4jVersion" -Option Constant
New-Variable -Name GRADLE_COMMON_VAR -Value "commonVersion" -Option Constant
New-Variable -Name GRADLE_MSAL_VAR -Value "msalVersion" -Option Constant
New-Variable -Name GRADLE_ADAL_VAR -Value "adalVersion" -Option Constant

New-Variable -Name COMMON4J_VERSIONING_FILE -Value "common/common4j/versioning/version.properties" -Option Constant
New-Variable -Name COMMON_VERSIONING_FILE -Value "common/versioning/version.properties" -Option Constant
New-Variable -Name BROKER4J_VERSIONING_FILE -Value "broker/broker4j/versioning/version.properties" -Option Constant
New-Variable -Name BROKER_VERSIONING_FILE -Value "broker/AADAuthenticator/versioning/version.properties" -Option Constant
New-Variable -Name MSAL_VERSIONING_FILE -Value "msal/msal/versioning/version.properties" -Option Constant
New-Variable -Name ADAL_VERSIONING_FILE -Value "adal/adal/versioning/version.properties" -Option Constant

New-Variable -Name COMMON_BUILD_GRADLE_FILE -Value "common/common/build.gradle" -Option Constant
New-Variable -Name MSAL_BUILD_GRADLE_FILE -Value "msal/msal/build.gradle" -Option Constant
New-Variable -Name ADAL_BUILD_GRADLE_FILE -Value "adal/adal/build.gradle" -Option Constant
New-Variable -Name BROKER_BUILD_GRADLE_FILE -Value "broker/AADAuthenticator/build.gradle" -Option Constant
New-Variable -Name BROKER4J_BUILD_GRADLE_FILE -Value "broker/broker4j/build.gradle" -Option Constant
New-Variable -Name MSALAUTOMATIONAPP_BUILD_GRADLE_FILE -Value "msal/msalautomationapp/build.gradle" -Option Constant
New-Variable -Name BROKERAUTOMATIONAPP_BUILD_GRADLE_FILE -Value "broker/brokerautomationapp/build.gradle" -Option Constant

New-Variable -Name COMMON_CHANGELOG_FILE -Value "common/changelog.txt" -Option Constant
New-Variable -Name BROKER_CHANGELOG_FILE -Value "broker/changes.txt" -Option Constant
New-Variable -Name MSAL_CHANGELOG_FILE -Value "msal/changelog" -Option Constant
New-Variable -Name ADAL_CHANGELOG_FILE -Value "adal/changelog.txt" -Option Constant


$changelogConstants = @{
    "VnextFormat" = "V.Next"
    "separator" = "---------"
    "versionFormat" = "Version "
}

