# Copyright (c) Microsoft Corporation. All rights reserved. Licensed under the MIT License.

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$tasks = @(
    ':adal:assembleLocalDebug'
    ':common:assembleLocalDebug'
    ':msal:assembleLocalDebug'
    ':AADAuthenticator:assembleLocalDebug'
    ':adal:testLocalDebugUnitTest'
    ':common:testLocalDebugUnitTest'
    ':msal:testLocalDebugUnitTest'
    ':AADAuthenticator:testLocalDebugUnitTest'
    ':adal:lintLocalDebug'
    ':common:lintLocalDebug'
    ':msal:lintLocalDebug'
    ':AADAuthenticator:lintLocalDebug'
)

Push-Location -LiteralPath $repositoryRoot
try {
    & (Join-Path $repositoryRoot 'gradlew.bat') @tasks
    if ($LASTEXITCODE -ne 0) {
        throw "Gradle verification failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
