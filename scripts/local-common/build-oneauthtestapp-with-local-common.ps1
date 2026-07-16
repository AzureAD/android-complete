<#
.SYNOPSIS
    Builds / verifies OneAuthTestApp against a locally-published Common (from Maven Local),
    without modifying the OneAuth repo (mavenLocal is injected via a Gradle init script).

.DESCRIPTION
    Wires OneAuthTestApp to consume com.microsoft.identity:common:<Version> from ~/.m2 by:
      * --init-script oneauth-mavenlocal.init.gradle   (adds mavenLocal() to every project)
      * -PandroidCommonVersion=<Version>               (OneAuth SDK's built-in override hook)

    Run publish-common-to-maven-local.ps1 FIRST so the artifact exists in Maven Local.

.PARAMETER Version
    The locally-published Common version to consume. Defaults to the value recorded by
    publish-common-to-maven-local.ps1 in `.last-published-version`.

.PARAMETER Task
    Gradle task(s) to run in the OneAuthTestApp build. Default: :app:assembleDebug.

.PARAMETER VerifyOnly
    Fast path: instead of building the APK, run a dependency check proving `common` resolves
    from Maven Local. Implies -SkipNativeBuild (no NDK/CMake needed).

.PARAMETER SkipNativeBuild
    Pass -PskipNativeBuild=true to skip the OneAuth CMake/NDK native build. Useful for a quick
    compile/resolve check. NOTE: a full functional run of the app needs the native build, so
    omit this for a real end-to-end test.

.PARAMETER SuperprojectRoot
    Path to the android-complete main checkout (contains oneauth\testapps\...). Auto-detected
    from this script's location (worktree -> main checkout) when omitted.

.PARAMETER JavaHome
    JDK 17 home. Defaults to the Microsoft OpenJDK 17 install used to validate this workflow.

.PARAMETER GradleArgs
    Extra args passed through to gradlew (e.g. --refresh-dependencies, -Pxyz).

.EXAMPLE
    .\build-oneauthtestapp-with-local-common.ps1 -VerifyOnly
    # confirms common/common4j resolve from ~/.m2 (fast, no native build)

.EXAMPLE
    .\build-oneauthtestapp-with-local-common.ps1
    # full :app:assembleDebug against the local Common

.EXAMPLE
    .\build-oneauthtestapp-with-local-common.ps1 -Task ':app:installDebug' -GradleArgs '--refresh-dependencies'
#>
[CmdletBinding()]
param(
    [string]$Version,
    [string]$Task = ":app:assembleDebug",
    [switch]$VerifyOnly,
    [switch]$SkipNativeBuild,
    [string]$SuperprojectRoot,
    [string]$JavaHome = "C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot",
    [string[]]$GradleArgs = @()
)

$ErrorActionPreference = "Stop"

function Resolve-SuperprojectRoot {
    param([string]$Explicit)
    if ($Explicit) { return (Resolve-Path $Explicit).Path }
    $repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $candidates = @()
    if ($repoRoot -match '(?i)^(.*android-complete)\.worktrees\\[^\\]+$') { $candidates += $matches[1] }
    $candidates += $repoRoot
    foreach ($c in $candidates) {
        if (Test-Path (Join-Path $c 'oneauth\testapps\android\OneAuthTestApp\gradlew.bat')) {
            return (Resolve-Path $c).Path
        }
    }
    throw "Could not locate OneAuthTestApp under an android-complete checkout. Pass -SuperprojectRoot. Tried: $($candidates -join '; ')"
}

# Resolve version (from param or the file the publish script wrote).
$versionFile = Join-Path $PSScriptRoot '.last-published-version'
if (-not $Version) {
    if (Test-Path $versionFile) {
        $Version = (Get-Content $versionFile -Raw).Trim()
    } else {
        throw "No -Version given and no '.last-published-version' found. Run publish-common-to-maven-local.ps1 first, or pass -Version."
    }
}

$root    = Resolve-SuperprojectRoot -Explicit $SuperprojectRoot
$appDir  = Join-Path $root 'oneauth\testapps\android\OneAuthTestApp'
$initScript = Join-Path $PSScriptRoot 'oneauth-mavenlocal.init.gradle'
if (-not (Test-Path $initScript)) { throw "Init script not found: $initScript" }
if (-not (Test-Path (Join-Path $JavaHome 'bin\java.exe'))) { throw "JDK 17 not found at '$JavaHome'." }

# Confirm the artifact is actually in Maven Local before we start.
$pom = Join-Path $env:USERPROFILE ".m2\repository\com\microsoft\identity\common\$Version\common-$Version.pom"
if (-not (Test-Path $pom)) {
    Write-Warning "common:$Version not found in Maven Local ($pom). Did you run publish-common-to-maven-local.ps1 -Version $Version ?"
}

# Build the gradle argument list.
if ($VerifyOnly) {
    $SkipNativeBuild = $true
    $gargs = @(
        ':OneAuth:dependencyInsight',
        '--configuration','debugRuntimeClasspath',
        '--dependency','com.microsoft.identity:common'
    )
} else {
    $gargs = @($Task)
}
$gargs += "-PandroidCommonVersion=$Version"
if ($SkipNativeBuild) { $gargs += '-PskipNativeBuild=true' }
$gargs += @('--init-script', $initScript, '--console=plain')
$gargs += $GradleArgs

Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host " Building OneAuthTestApp against local Common" -ForegroundColor Cyan
Write-Host "   App dir      : $appDir"
Write-Host "   Common ver   : $Version"
Write-Host "   Init script  : $initScript"
Write-Host "   Gradle args  : $($gargs -join ' ')"
Write-Host "=====================================================================" -ForegroundColor Cyan

$env:JAVA_HOME = $JavaHome
Push-Location $appDir
try {
    & .\gradlew.bat @gargs
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($code -ne 0) {
    Write-Host "`nBUILD FAILED (exit $code)" -ForegroundColor Red
    exit $code
}

Write-Host "`n=====================================================================" -ForegroundColor Green
if ($VerifyOnly) {
    Write-Host " OK: OneAuthTestApp resolves com.microsoft.identity:common:$Version from Maven Local." -ForegroundColor Green
    Write-Host " (Look for the version above under debugRuntimeClasspath with no FAILED lines.)"
} else {
    Write-Host " OK: '$Task' completed against local Common $Version." -ForegroundColor Green
}
Write-Host "=====================================================================" -ForegroundColor Green
