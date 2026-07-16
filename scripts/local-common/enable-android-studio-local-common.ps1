<#
.SYNOPSIS
    Enables (or disables) the Android Studio "Run" button to build/install OneAuthTestApp
    against your locally-published Common — no per-run Gradle flags needed.

.DESCRIPTION
    The normal command-line workflow passes two things at invocation time:
        --init-script oneauth-mavenlocal.init.gradle   (adds mavenLocal())
        -PandroidCommonVersion=<ver>                   (which Common version to use)
    The Android Studio Run button runs a plain Gradle build and passes NEITHER, so by
    default it silently resolves the FEED Common (msIdentityCommon = 24.3.0), not your
    local changes.

    This script makes both persistent for your machine so the IDE picks them up:
      1. Copies oneauth-mavenlocal.init.gradle -> ~/.gradle/init.d/zz-oneauth-local-common.gradle
         (Gradle auto-applies every *.gradle in init.d to ALL builds, including the IDE.)
      2. Sets  androidCommonVersion=<Version>  in your GLOBAL ~/.gradle/gradle.properties.

    It uses a STABLE -SNAPSHOT version by default (0.0.0-local-dev-SNAPSHOT). Because the
    OneAuth SDK + app set cacheChangingModulesFor(0), a -SNAPSHOT is re-read from ~/.m2 on
    every build — so you can re-publish the same version and just hit Run again, without
    editing anything.

    TRADE-OFF: these hooks are GLOBAL (per-machine, all Gradle builds). That's why this is
    opt-in and fully reversible. Run with -Disable to remove both hooks. Prefer the
    command-line scripts if you don't want global changes.

.PARAMETER Version
    The local Common version to wire in. Default: 0.0.0-local-dev-SNAPSHOT.
    Use a -SNAPSHOT suffix so repeated re-publishes are picked up without edits.

.PARAMETER Publish
    Also publish local Common at -Version first (calls publish-common-to-maven-local.ps1).

.PARAMETER Disable
    Remove both persistent hooks (uninstall). Restores default IDE behavior.

.PARAMETER JavaHome
    JDK 17 home (only used with -Publish). Defaults to the Microsoft OpenJDK 17 install.

.EXAMPLE
    # One-time enable, publishing the SNAPSHOT in the same step:
    .\enable-android-studio-local-common.ps1 -Publish
    # -> then in Android Studio: Sync Gradle, pick OneAuthTestApp :app, hit Run.

.EXAMPLE
    # After you change Common again, just re-publish the same version and hit Run:
    .\publish-common-to-maven-local.ps1 -Version 0.0.0-local-dev-SNAPSHOT
    # (no need to re-run this script; hooks already point at that version)

.EXAMPLE
    # Turn it all off (back to normal feed-based builds):
    .\enable-android-studio-local-common.ps1 -Disable
#>
[CmdletBinding()]
param(
    [string]$Version = "0.0.0-local-dev-SNAPSHOT",
    [switch]$Publish,
    [switch]$Disable,
    [string]$JavaHome = "C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot"
)

$ErrorActionPreference = "Stop"

$gradleUserHome = if ($env:GRADLE_USER_HOME) { $env:GRADLE_USER_HOME } else { Join-Path $env:USERPROFILE ".gradle" }
$initDir   = Join-Path $gradleUserHome "init.d"
$initDst   = Join-Path $initDir "zz-oneauth-local-common.gradle"
$gpFile    = Join-Path $gradleUserHome "gradle.properties"
$initSrc   = Join-Path $PSScriptRoot "oneauth-mavenlocal.init.gradle"
$propName  = "androidCommonVersion"
$marker    = "# added by enable-android-studio-local-common.ps1"

function Remove-GlobalProperty {
    if (Test-Path $gpFile) {
        $kept = Get-Content $gpFile | Where-Object { $_ -notmatch "^\s*$propName\s*=" -and $_ -ne $marker }
        Set-Content -Path $gpFile -Value $kept -Encoding ascii
    }
}

if ($Disable) {
    Write-Host "Disabling Android Studio local-Common hooks ..." -ForegroundColor Yellow
    if (Test-Path $initDst) { Remove-Item $initDst -Force; Write-Host "  removed $initDst" -ForegroundColor Green }
    else { Write-Host "  (init.d hook not present)" }
    Remove-GlobalProperty
    Write-Host "  removed '$propName' from $gpFile" -ForegroundColor Green
    Write-Host "`nDone. In Android Studio: Sync Gradle so the change takes effect." -ForegroundColor Green
    Write-Host "The Run button now builds against the FEED Common again." -ForegroundColor Green
    return
}

if (-not (Test-Path $initSrc)) { throw "Init script not found next to this script: $initSrc" }

if ($Publish) {
    Write-Host "Publishing local Common ($Version) before enabling ..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "publish-common-to-maven-local.ps1") -Version $Version -JavaHome $JavaHome
    if ($LASTEXITCODE -ne 0) { throw "publish step failed (exit $LASTEXITCODE)" }
}

# Warn if the artifact isn't in Maven Local yet.
$pom = Join-Path $env:USERPROFILE ".m2\repository\com\microsoft\identity\common\$Version\common-$Version.pom"
if (-not (Test-Path $pom)) {
    Write-Warning "common:$Version is not in Maven Local yet ($pom)."
    Write-Warning "Publish it first:  .\publish-common-to-maven-local.ps1 -Version $Version   (or re-run this with -Publish)"
}

# Hook 1: init.d mavenLocal
New-Item -ItemType Directory -Force -Path $initDir | Out-Null
Copy-Item $initSrc $initDst -Force
Write-Host "Installed init.d hook  -> $initDst" -ForegroundColor Green

# Hook 2: global gradle.properties androidCommonVersion (idempotent)
Remove-GlobalProperty
Add-Content -Path $gpFile -Value $marker
Add-Content -Path $gpFile -Value "$propName=$Version"
Write-Host "Set $propName=$Version in $gpFile" -ForegroundColor Green

Write-Host "`n=====================================================================" -ForegroundColor Green
Write-Host " Android Studio Run button is now wired to local Common $Version." -ForegroundColor Green
Write-Host " Next:" -ForegroundColor Green
Write-Host "   1. In Android Studio: File > Sync Project with Gradle Files"
Write-Host "   2. Select the OneAuthTestApp ':app' run configuration and hit Run."
Write-Host "   After changing Common again: re-run publish with the SAME version, then just hit Run:"
Write-Host "     .\publish-common-to-maven-local.ps1 -Version $Version"
Write-Host "`n Turn it off with:  .\enable-android-studio-local-common.ps1 -Disable" -ForegroundColor Green
Write-Host "=====================================================================" -ForegroundColor Green
