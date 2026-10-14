# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Build, install, launch, and reset the app-under-test on a device/emulator for E2E runs.

.PARAMETER Command
    build | install | launch | stop | clear | uninstall | list-apks | grant | is-installed

.DESCRIPTION
    Thin wrappers over gradlew (build/install) and adb (launch/state/permissions).
    `clear` resets app state for a clean E2E run without a factory reset.

.EXAMPLE
    ./appcontrol.ps1 build   -Module :msalTestApp -Variant localDebug
    ./appcontrol.ps1 install -Module :msalTestApp -Variant localDebug   # gradle install<Variant>
    ./appcontrol.ps1 install -Apk C:\path\app-localDebug.apk            # direct apk (adb -r -g)
    ./appcontrol.ps1 clear   -Package com.msft.identity.client.sample.local
    ./appcontrol.ps1 launch  -Package com.msft.identity.client.sample.local
    ./appcontrol.ps1 grant   -Package <pkg> -Permission android.permission.POST_NOTIFICATIONS

.NOTES
    Gradle builds require the repo's Maven credentials (see README) and can take minutes.
    Prefer reusing an APK already built by Android Studio when one exists (list-apks).
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('build', 'install', 'launch', 'stop', 'clear', 'uninstall', 'list-apks', 'grant', 'is-installed')]
    [string]$Command = 'list-apks',

    [string]$Serial,
    [string]$Module,             # gradle module path, e.g. :msalTestApp
    [string]$Package,            # applicationId, e.g. com.microsoft.brokerhost
    [string]$Apk,                # explicit apk path for direct install
    [string]$Activity,           # optional fully-qualified activity for launch
    [string]$Variant = 'localDebug',
    [string]$Permission,
    [string]$RepoRoot,
    [int]$TimeoutSec = 300       # bound a direct-APK 'install' so a wedged `adb install` is killed, not hung forever (0 = no timeout)
)

$ErrorActionPreference = 'Stop'

function Get-Adb {
    foreach ($v in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT)) {
        if ($v) {
            $p = Join-Path ([Environment]::ExpandEnvironmentVariables($v)) 'platform-tools\adb.exe'
            if (Test-Path $p) { return $p }
        }
    }
    foreach ($root in @((Join-Path $env:LOCALAPPDATA 'Android\Sdk'), (Join-Path $HOME 'AppData\Local\Android\Sdk'), (Join-Path $HOME 'Library/Android/sdk'), (Join-Path $HOME 'Android/Sdk'))) {
        foreach ($exe in @('platform-tools\adb.exe', 'platform-tools/adb')) {
            $p = Join-Path $root $exe
            if ($root -and (Test-Path $p)) { return $p }
        }
    }
    $cmd = Get-Command adb -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "adb not found. Set ANDROID_HOME to your SDK root."
}

function Find-RepoRoot {
    if ($RepoRoot) { return $RepoRoot }
    $d = Get-Location
    while ($d) {
        foreach ($w in @('gradlew.bat', 'gradlew')) {
            if (Test-Path (Join-Path $d $w)) { return $d.Path }
        }
        $parent = Split-Path $d -Parent
        if (-not $parent -or $parent -eq $d) { break }
        $d = Get-Item $parent
    }
    throw "Could not find the android-complete repo root (no gradlew found walking up). Pass -RepoRoot."
}

function Cap { param([string]$s) if ($s) { $s.Substring(0, 1).ToUpper() + $s.Substring(1) } else { $s } }

$adb = Get-Adb
function Adb {
    if ($Serial) { & $adb -s $Serial @args } else { & $adb @args }
}

# Run an adb invocation in a child process with a hard wall-clock timeout. A hung `adb install`
# (adb server / device wedged) otherwise blocks the caller — and any supervising agent — forever,
# defeating the per-point abort cap. On timeout we kill the process tree and throw so the caller
# fails fast and can recover the adb server instead of freezing.
function Invoke-AdbTimed {
    param([string[]]$AdbArgs, [int]$TimeoutSec)
    $full = @()
    if ($Serial) { $full += @('-s', $Serial) }
    $full += $AdbArgs
    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath $adb -ArgumentList $full -NoNewWindow -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        if (-not $p.WaitForExit($TimeoutSec * 1000)) {
            try { $p.Kill($true) } catch { try { $p.Kill() } catch { } }
            Get-Content $outFile, $errFile -ErrorAction SilentlyContinue | Write-Host
            throw "adb $($AdbArgs -join ' ') exceeded ${TimeoutSec}s and was killed. adb/device likely wedged — recover with 'adb kill-server; adb start-server' and re-lease the device; do NOT reboot the device mid-install."
        }
        Get-Content $outFile, $errFile -ErrorAction SilentlyContinue | Write-Host
        return $p.ExitCode
    }
    finally { Remove-Item $outFile, $errFile -ErrorAction SilentlyContinue }
}

switch ($Command) {
    'build' {
        if (-not $Module) { throw "Provide -Module (e.g. :msalTestApp)." }
        $root = Find-RepoRoot
        $gw = if (Test-Path (Join-Path $root 'gradlew.bat')) { Join-Path $root 'gradlew.bat' } else { Join-Path $root 'gradlew' }
        $task = "assemble$(Cap $Variant)"
        Write-Host "Building $Module`:$task  (cwd=$root)"
        Push-Location $root
        try { & $gw "$Module`:$task" }
        finally { Pop-Location }
    }
    'install' {
        if ($Apk) {
            if (-not (Test-Path $Apk)) { throw "APK not found: $Apk" }
            if ($TimeoutSec -gt 0) {
                Write-Host "Installing (adb -r -g, timeout ${TimeoutSec}s): $Apk"
                $code = Invoke-AdbTimed -AdbArgs @('install', '-r', '-g', $Apk) -TimeoutSec $TimeoutSec
                if ($code -ne 0) { throw "adb install exited $code for $Apk" }
            }
            else {
                Write-Host "Installing (adb -r -g, no timeout): $Apk"
                Adb install -r -g $Apk | Write-Host
            }
        }
        elseif ($Module) {
            $root = Find-RepoRoot
            $gw = if (Test-Path (Join-Path $root 'gradlew.bat')) { Join-Path $root 'gradlew.bat' } else { Join-Path $root 'gradlew' }
            $task = "install$(Cap $Variant)"
            Write-Host "Installing via gradle: $Module`:$task"
            Push-Location $root
            try { & $gw "$Module`:$task" }
            finally { Pop-Location }
        }
        else { throw "Provide -Apk <path> or -Module <:module>." }
    }
    'list-apks' {
        $root = Find-RepoRoot
        $apks = Get-ChildItem $root -Recurse -Filter '*.apk' -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match 'build[\\/]outputs[\\/]apk' }
        if ($Variant) { $apks = $apks | Where-Object { $_.Name -match $Variant -or $_.FullName -match $Variant } }
        if (-not $apks) { Write-Host "No built APKs found under build/outputs/apk. Build first (Android Studio or ./appcontrol.ps1 build)."; exit 0 }
        $apks | Select-Object FullName, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } }, LastWriteTime |
            Sort-Object LastWriteTime -Descending | Format-Table -AutoSize | Out-String | Write-Host
    }
    'launch' {
        if (-not $Package) { throw "Provide -Package." }
        if ($Activity) {
            Adb shell am start -n "$Package/$Activity" | Write-Host
        }
        else {
            Adb shell monkey -p $Package -c android.intent.category.LAUNCHER 1 2>$null | Out-Null
            Write-Host "Launched default activity of $Package"
        }
    }
    'stop' {
        if (-not $Package) { throw "Provide -Package." }
        Adb shell am force-stop $Package | Out-Null
        Write-Host "Force-stopped $Package"
    }
    'clear' {
        if (-not $Package) { throw "Provide -Package." }
        Adb shell pm clear $Package | Write-Host
        Write-Host "Cleared app state for $Package"
    }
    'uninstall' {
        if (-not $Package) { throw "Provide -Package." }
        Adb uninstall $Package | Write-Host
    }
    'grant' {
        if (-not $Package -or -not $Permission) { throw "Provide -Package and -Permission." }
        Adb shell pm grant $Package $Permission | Write-Host
        Write-Host "Granted $Permission to $Package"
    }
    'is-installed' {
        if (-not $Package) { throw "Provide -Package." }
        $found = (Adb shell pm list packages $Package 2>$null | Out-String)
        if ($found -match [regex]::Escape("package:$Package")) { Write-Host "INSTALLED: $Package"; exit 0 }
        Write-Host "NOT INSTALLED: $Package"; exit 2
    }
}
