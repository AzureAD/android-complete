<#
.SYNOPSIS
    Publishes the locally-modified Common (common4j + common) to Maven Local (~/.m2)
    so it can be consumed by OneAuthTestApp (or any local build) without touching a feed.

.DESCRIPTION
    Runs from the android-complete SUPERPROJECT (the main checkout that actually has the
    `common` sub-repo cloned). Publishes in the required order:
        1. :common4j  -> com.microsoft.identity:common4j:<Version>
        2. :common    -> com.microsoft.identity:common:<Version>   (dist release publication)
    The `common` dist POM is pinned to the SAME <Version> of common4j via -PdistCommon4jVersion.

    The chosen version is written to `.last-published-version` next to this script so the
    companion build script can pick it up automatically.

.PARAMETER Version
    The Maven version to publish under. Defaults to a timestamped local-only version
    (0.0.0-local-<yyyyMMddHHmmss>) so it can never collide with a feed artifact and is
    obvious in dependency reports. Use a distinct version each run to avoid Gradle cache staleness.

.PARAMETER SuperprojectRoot
    Path to the android-complete main checkout (that contains `common\` and `gradlew.bat`).
    Auto-detected from this script's location (worktree -> main checkout) when omitted.

.PARAMETER JavaHome
    JDK 17 home. Defaults to the Microsoft OpenJDK 17 install used to validate this workflow.

.EXAMPLE
    .\publish-common-to-maven-local.ps1
    # publishes a fresh timestamped version and records it for the build script

.EXAMPLE
    .\publish-common-to-maven-local.ps1 -Version 0.0.0-local-secure-redirect-test1
#>
[CmdletBinding()]
param(
    [string]$Version,
    [string]$SuperprojectRoot,
    [string]$JavaHome = "C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot"
)

$ErrorActionPreference = "Stop"

function Resolve-SuperprojectRoot {
    param([string]$Explicit)
    if ($Explicit) { return (Resolve-Path $Explicit).Path }

    # This script lives at <repo>\scripts\local-common\ ; <repo> may be a git worktree
    # named "...android-complete.worktrees\<name>" whose sub-repos are NOT cloned. The real
    # buildable checkout is the sibling "...android-complete".
    $repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $candidates = @()
    if ($repoRoot -match '(?i)^(.*android-complete)\.worktrees\\[^\\]+$') {
        $candidates += $matches[1]      # main checkout
    }
    $candidates += $repoRoot            # in case scripts are run from the main checkout

    foreach ($c in $candidates) {
        if ((Test-Path (Join-Path $c 'gradlew.bat')) -and (Test-Path (Join-Path $c 'common\common\build.gradle'))) {
            return (Resolve-Path $c).Path
        }
    }
    throw "Could not locate an android-complete checkout with the 'common' sub-repo. Pass -SuperprojectRoot explicitly. Tried: $($candidates -join '; ')"
}

if (-not $Version) {
    $Version = "0.0.0-local-{0}" -f (Get-Date -Format "yyyyMMddHHmmss")
}

$root = Resolve-SuperprojectRoot -Explicit $SuperprojectRoot
if (-not (Test-Path (Join-Path $JavaHome 'bin\java.exe'))) {
    throw "JDK 17 not found at '$JavaHome'. Pass -JavaHome pointing at a JDK 17 install."
}

Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host " Publishing local Common to Maven Local" -ForegroundColor Cyan
Write-Host "   Superproject : $root"
Write-Host "   Version      : $Version"
Write-Host "   JAVA_HOME    : $JavaHome"
Write-Host "=====================================================================" -ForegroundColor Cyan

$env:JAVA_HOME = $JavaHome
Push-Location $root
try {
    # Common's Android build requires the Authenticator opt-out because gradle.properties in the
    # main checkout sets includeAuthenticatorApp=true but the authenticator/ repo isn't cloned.
    $commonFlags = @('-PincludeAuthenticatorApp=false')

    Write-Host "`n[1/2] Publishing :common4j ($Version) ..." -ForegroundColor Yellow
    & .\gradlew.bat :common4j:publishToMavenLocal "-PprojVersion=$Version" @commonFlags --console=plain
    if ($LASTEXITCODE -ne 0) { throw ":common4j publish failed (exit $LASTEXITCODE)" }

    Write-Host "`n[2/2] Publishing :common dist release ($Version) ..." -ForegroundColor Yellow
    & .\gradlew.bat :common:publishDistReleasePublicationToMavenLocal `
        "-PprojVersion=$Version" "-PdistCommon4jVersion=$Version" @commonFlags --console=plain
    if ($LASTEXITCODE -ne 0) { throw ":common publish failed (exit $LASTEXITCODE)" }
}
finally {
    Pop-Location
}

# Record the version so the companion build script can default to it.
$versionFile = Join-Path $PSScriptRoot '.last-published-version'
Set-Content -Path $versionFile -Value $Version -NoNewline -Encoding ascii

$m2 = Join-Path $env:USERPROFILE ".m2\repository\com\microsoft\identity"
Write-Host "`n=====================================================================" -ForegroundColor Green
Write-Host " Published to Maven Local:" -ForegroundColor Green
Write-Host "   com.microsoft.identity:common4j:$Version"
Write-Host "   com.microsoft.identity:common:$Version"
Write-Host "   (under $m2)"
Write-Host " Recorded version -> $versionFile" -ForegroundColor Green
Write-Host "`n Next: build OneAuthTestApp against it:" -ForegroundColor Green
Write-Host "   .\build-oneauthtestapp-with-local-common.ps1 -VerifyOnly      # fast dependency check"
Write-Host "   .\build-oneauthtestapp-with-local-common.ps1                  # full :app:assembleDebug"
Write-Host "=====================================================================" -ForegroundColor Green
