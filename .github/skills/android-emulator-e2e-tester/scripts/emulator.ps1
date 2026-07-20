# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Android emulator lifecycle helper for E2E testing: resolve the SDK, list/inspect
    AVDs and running devices, create an AVD, start one, wait for boot, or do all of
    that in one shot with `ensure`.

.DESCRIPTION
    Self-resolves the Android SDK location (handles unexpanded %LOCALAPPDATA% in
    ANDROID_HOME) and never hardcodes machine paths. Prefers fast operations
    (`emulator -list-avds`, reading the system-images/ dir) over the slow
    avdmanager/sdkmanager where possible.

.PARAMETER Command
    resolve-sdk | list | list-images | status | create | start | ensure

.EXAMPLE
    # One-shot: guarantee a booted emulator meeting the feature's needs, print its serial.
    ./emulator.ps1 ensure -ApiLevel 34 -RequireGoogleApis -Wait

.EXAMPLE
    ./emulator.ps1 ensure -Avd Pixel_7 -Wait          # use/create+start a named AVD
    ./emulator.ps1 status                             # what is running right now
    ./emulator.ps1 list-images                        # installed system images (for create)
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('resolve-sdk', 'list', 'list-images', 'status', 'create', 'start', 'ensure')]
    [string]$Command = 'status',

    [string]$Avd,                 # explicit AVD name to use/create
    [int]$ApiLevel = 0,           # minimum API level requirement (0 = any)
    [string]$Image,               # explicit system-image package id for create
    [string]$Device = 'pixel_7',  # device profile for create
    [switch]$RequireGoogleApis,   # require google_apis or google_apis_playstore tag (GMS/push)
    [switch]$RequirePlayStore,    # require google_apis_playstore tag
    [switch]$Wait,                # wait for full boot
    [switch]$ColdBoot,            # start with a clean state (-no-snapshot-load)
    [switch]$NoWindow,            # headless (-no-window)
    [int]$TimeoutSec = 300
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# SDK + tool resolution
# ---------------------------------------------------------------------------
function Resolve-Sdk {
    $candidates = @()
    foreach ($v in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT)) {
        if ($v) { $candidates += [Environment]::ExpandEnvironmentVariables($v) }
    }
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'Android\Sdk') }
    $candidates += (Join-Path $HOME 'AppData\Local\Android\Sdk')          # Windows
    $candidates += (Join-Path $HOME 'Library/Android/sdk')                # macOS
    $candidates += (Join-Path $HOME 'Android/Sdk')                        # Linux
    foreach ($c in $candidates) {
        if ($c -and (Test-Path (Join-Path $c 'platform-tools'))) { return (Resolve-Path $c).Path }
    }
    throw "Could not locate the Android SDK. Set ANDROID_HOME to your SDK root (the folder containing platform-tools)."
}

function Get-Tool {
    param([string]$Sdk, [string]$RelNoExt, [string[]]$Extra)
    $exe = if ($IsWindows -or $env:OS -eq 'Windows_NT') { '.exe' } else { '' }
    $bat = if ($IsWindows -or $env:OS -eq 'Windows_NT') { '.bat' } else { '' }
    $probes = @(
        (Join-Path $Sdk "$RelNoExt$exe"),
        (Join-Path $Sdk "$RelNoExt$bat"),
        (Join-Path $Sdk $RelNoExt)
    )
    if ($Extra) { $probes += $Extra }
    foreach ($p in $probes) { if ($p -and (Test-Path $p)) { return $p } }
    return $null
}

function Get-Tools {
    $sdk = Resolve-Sdk
    $cmdlineExtra = @(
        (Join-Path $sdk 'cmdline-tools\latest\bin\avdmanager.bat'),
        (Join-Path $sdk 'cmdline-tools\latest\bin\sdkmanager.bat'),
        'C:\Android\cmdline-tools\latest\avdmanager.bat',
        'C:\Android\cmdline-tools\latest\sdkmanager.bat'
    )
    [pscustomobject]@{
        Sdk        = $sdk
        Adb        = Get-Tool $sdk 'platform-tools\adb'
        Emulator   = Get-Tool $sdk 'emulator\emulator'
        AvdManager = Get-Tool $sdk 'cmdline-tools\latest\bin\avdmanager' ($cmdlineExtra | Where-Object { $_ -like '*avdmanager*' })
        SdkManager = Get-Tool $sdk 'cmdline-tools\latest\bin\sdkmanager' ($cmdlineExtra | Where-Object { $_ -like '*sdkmanager*' })
    }
}

# ---------------------------------------------------------------------------
# AVD + device inspection
# ---------------------------------------------------------------------------
function Get-AvdHome {
    if ($env:ANDROID_AVD_HOME) { return $env:ANDROID_AVD_HOME }
    return (Join-Path $HOME '.android\avd')
}

function Get-Avds {
    param($Tools)
    $names = @()
    if ($Tools.Emulator) { $names = & $Tools.Emulator -list-avds 2>$null | Where-Object { $_ -and $_.Trim() } }
    $avdHome = Get-AvdHome
    $names | ForEach-Object {
        $name = $_.Trim()
        $api = 0; $tag = ''; $dev = ''
        $cfg = Join-Path $avdHome "$name.avd\config.ini"
        if (Test-Path $cfg) {
            $lines = Get-Content $cfg
            $sysdir = ($lines | Where-Object { $_ -like 'image.sysdir.1=*' }) -replace '.*=', ''
            if ($sysdir -match 'android-(\d+)') { $api = [int]$Matches[1] }
            if ($sysdir -match 'google_apis_playstore') { $tag = 'google_apis_playstore' }
            elseif ($sysdir -match 'google_apis') { $tag = 'google_apis' }
            elseif ($sysdir -match 'system-images/[^/]+/([^/]+)/') { $tag = $Matches[1] }
            $tagLine = ($lines | Where-Object { $_ -like 'tag.id=*' }) -replace '.*=', ''
            if (-not $tag -and $tagLine) { $tag = $tagLine }
            $dev = (($lines | Where-Object { $_ -like 'hw.device.name=*' }) -replace '.*=', '')
        }
        [pscustomobject]@{ Name = $name; Api = $api; Tag = $tag; Device = $dev }
    }
}

function Get-RunningEmulators {
    param($Tools)
    & $Tools.Adb start-server 2>$null | Out-Null
    $out = & $Tools.Adb devices 2>$null
    $result = @()
    foreach ($line in $out) {
        if ($line -match '^(emulator-\d+)\s+device') {
            $serial = $Matches[1]
            $name = ''
            $an = & $Tools.Adb -s $serial emu avd name 2>$null
            if ($an) { $name = ($an | Where-Object { $_ -and $_.Trim() -ne 'OK' } | Select-Object -First 1).Trim() }
            $booted = (& $Tools.Adb -s $serial shell getprop sys.boot_completed 2>$null | Out-String).Trim()
            $result += [pscustomobject]@{ Serial = $serial; Avd = $name; Booted = ($booted -eq '1') }
        }
    }
    return $result
}

function Get-InstalledImages {
    param($Tools)
    $root = Join-Path $Tools.Sdk 'system-images'
    if (-not (Test-Path $root)) { return @() }
    $images = @()
    Get-ChildItem $root -Directory | ForEach-Object {
        $apiDir = $_.Name
        Get-ChildItem $_.FullName -Directory | ForEach-Object {
            $tag = $_.Name
            Get-ChildItem $_.FullName -Directory | ForEach-Object {
                $abi = $_.Name
                $api = 0; if ($apiDir -match 'android-(\d+)') { $api = [int]$Matches[1] }
                $images += [pscustomobject]@{
                    Package = "system-images;$apiDir;$tag;$abi"; Api = $api; Tag = $tag; Abi = $abi
                }
            }
        }
    }
    return $images
}

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
function Wait-Boot {
    param($Tools, [string]$Serial, [int]$Timeout)
    $deadline = (Get-Date).AddSeconds($Timeout)
    Write-Host "Waiting for $Serial to finish booting (timeout ${Timeout}s)..."
    & $Tools.Adb -s $Serial wait-for-device 2>$null
    while ((Get-Date) -lt $deadline) {
        $b = (& $Tools.Adb -s $Serial shell getprop sys.boot_completed 2>$null | Out-String).Trim()
        if ($b -eq '1') {
            Start-Sleep -Seconds 2
            & $Tools.Adb -s $Serial shell input keyevent 82 2>$null | Out-Null   # dismiss keyguard
            & $Tools.Adb -s $Serial shell wm dismiss-keyguard 2>$null | Out-Null
            Write-Host "Boot complete: $Serial"
            return $true
        }
        Start-Sleep -Seconds 3
    }
    throw "Emulator $Serial did not finish booting within ${Timeout}s."
}

function Select-Image {
    param($Tools, [int]$MinApi, [switch]$NeedGoogle, [switch]$NeedPlay)
    $imgs = Get-InstalledImages $Tools
    if ($MinApi -gt 0) { $imgs = $imgs | Where-Object { $_.Api -ge $MinApi } }
    if ($NeedPlay) { $imgs = $imgs | Where-Object { $_.Tag -eq 'google_apis_playstore' } }
    elseif ($NeedGoogle) { $imgs = $imgs | Where-Object { $_.Tag -like 'google_apis*' } }
    # Prefer x86_64/arm64 host-matching abi, then lowest API that still satisfies MinApi.
    $imgs = $imgs | Sort-Object Api
    return ($imgs | Select-Object -First 1)
}

function New-Avd {
    param($Tools, [string]$Name, [string]$Package, [string]$DeviceProfile)
    if (-not $Tools.AvdManager) { throw "avdmanager not found; cannot create AVD '$Name'." }
    if (-not $Package) { throw "No system image available to create AVD '$Name'. Install one via Android Studio SDK Manager, or run: sdkmanager 'system-images;android-34;google_apis;x86_64'" }
    Write-Host "Creating AVD '$Name' from '$Package' (device: $DeviceProfile)..."
    'no' | & $Tools.AvdManager create avd -n $Name -k $Package -d $DeviceProfile --force 2>&1 | Write-Host
    Write-Host "Created AVD '$Name'."
}

function Start-Emu {
    param($Tools, [string]$Name, [switch]$Cold, [switch]$Headless, [int]$Timeout, [switch]$DoWait)
    $before = (Get-RunningEmulators $Tools).Serial
    $emuArgs = @('-avd', $Name, '-no-boot-anim', '-netdelay', 'none', '-netspeed', 'full')
    if ($Cold) { $emuArgs += '-no-snapshot-load' }
    if ($Headless) { $emuArgs += '-no-window'; $emuArgs += '-gpu'; $emuArgs += 'swiftshader_indirect' }
    Write-Host "Starting emulator: $($Tools.Emulator) $($emuArgs -join ' ')"
    Start-Process -FilePath $Tools.Emulator -ArgumentList $emuArgs -WindowStyle Minimized | Out-Null
    # Wait for a NEW emulator serial to appear that reports the target AVD name.
    $deadline = (Get-Date).AddSeconds($Timeout)
    $serial = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        $running = Get-RunningEmulators $Tools
        $candidate = $running | Where-Object { $_.Avd -eq $Name -or $before -notcontains $_.Serial }
        $match = $candidate | Where-Object { $_.Avd -eq $Name } | Select-Object -First 1
        if (-not $match) { $match = $candidate | Where-Object { $before -notcontains $_.Serial } | Select-Object -First 1 }
        if ($match) { $serial = $match.Serial; break }
    }
    if (-not $serial) { throw "Emulator for AVD '$Name' did not register with adb within ${Timeout}s." }
    Write-Host "Emulator online: $serial (avd=$Name)"
    if ($DoWait) { Wait-Boot $Tools $serial $Timeout | Out-Null }
    return $serial
}

# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------
$tools = Get-Tools

switch ($Command) {
    'resolve-sdk' {
        Write-Host "SDK:         $($tools.Sdk)"
        Write-Host "adb:         $($tools.Adb)"
        Write-Host "emulator:    $($tools.Emulator)"
        Write-Host "avdmanager:  $($tools.AvdManager)"
        Write-Host "sdkmanager:  $($tools.SdkManager)"
    }
    'list' {
        Write-Host "== AVDs =="
        Get-Avds $tools | Format-Table -AutoSize | Out-String | Write-Host
        Write-Host "== Running =="
        Get-RunningEmulators $tools | Format-Table -AutoSize | Out-String | Write-Host
    }
    'list-images' {
        Get-InstalledImages $tools | Sort-Object Api | Format-Table -AutoSize | Out-String | Write-Host
    }
    'status' {
        $running = Get-RunningEmulators $tools
        if ($Avd) { $running = $running | Where-Object { $_.Avd -eq $Avd } }
        if (-not $running) { Write-Host 'No matching emulator running.'; exit 0 }
        $running | Format-Table -AutoSize | Out-String | Write-Host
    }
    'create' {
        if (-not $Avd) { throw "Provide -Avd <name> to create." }
        $pkg = $Image
        if (-not $pkg) {
            $sel = Select-Image $tools -MinApi $ApiLevel -NeedGoogle:$RequireGoogleApis -NeedPlay:$RequirePlayStore
            if ($sel) { $pkg = $sel.Package }
        }
        New-Avd $tools $Avd $pkg $Device
    }
    'start' {
        if (-not $Avd) { throw "Provide -Avd <name> to start." }
        $running = Get-RunningEmulators $tools | Where-Object { $_.Avd -eq $Avd } | Select-Object -First 1
        if ($running) {
            Write-Host "Already running: $($running.Serial)"
            if ($Wait) { Wait-Boot $tools $running.Serial $TimeoutSec | Out-Null }
            Write-Host "SERIAL=$($running.Serial)"; exit 0
        }
        $serial = Start-Emu $tools $Avd -Cold:$ColdBoot -Headless:$NoWindow -Timeout $TimeoutSec -DoWait:$Wait
        Write-Host "SERIAL=$serial"
    }
    'ensure' {
        # 1) Pick or create an AVD that satisfies the feature's requirements.
        $target = $null
        $avds = Get-Avds $tools
        if ($Avd) {
            $target = $avds | Where-Object { $_.Name -eq $Avd } | Select-Object -First 1
            if (-not $target) {
                $pkg = $Image
                if (-not $pkg) {
                    $sel = Select-Image $tools -MinApi $ApiLevel -NeedGoogle:$RequireGoogleApis -NeedPlay:$RequirePlayStore
                    if ($sel) { $pkg = $sel.Package }
                }
                New-Avd $tools $Avd $pkg $Device
                $target = [pscustomobject]@{ Name = $Avd }
            }
        }
        else {
            $match = $avds
            if ($ApiLevel -gt 0) { $match = $match | Where-Object { $_.Api -ge $ApiLevel -or $_.Api -eq 0 } }
            if ($RequirePlayStore) { $match = $match | Where-Object { $_.Tag -eq 'google_apis_playstore' } }
            elseif ($RequireGoogleApis) { $match = $match | Where-Object { $_.Tag -like 'google_apis*' } }
            # Prefer an already-running AVD among the matches.
            $runningNames = (Get-RunningEmulators $tools).Avd
            $target = $match | Where-Object { $runningNames -contains $_.Name } | Select-Object -First 1
            if (-not $target) { $target = $match | Sort-Object Api | Select-Object -First 1 }
            if (-not $target) {
                # No suitable AVD exists: create one.
                $name = "android_auth_e2e"
                if ($ApiLevel -gt 0) { $name += "_api$ApiLevel" }
                $sel = Select-Image $tools -MinApi $ApiLevel -NeedGoogle:$RequireGoogleApis -NeedPlay:$RequirePlayStore
                if (-not $sel) { throw "No installed system image satisfies the requirements (minApi=$ApiLevel, googleApis=$RequireGoogleApis, playStore=$RequirePlayStore). Install one first." }
                New-Avd $tools $name $sel.Package $Device
                $target = [pscustomobject]@{ Name = $name }
            }
        }

        # 2) Reuse if running, else start. Then wait for boot.
        $running = Get-RunningEmulators $tools | Where-Object { $_.Avd -eq $target.Name } | Select-Object -First 1
        if ($running) {
            Write-Host "Reusing running emulator: $($running.Serial) (avd=$($target.Name))"
            $serial = $running.Serial
            Wait-Boot $tools $serial $TimeoutSec | Out-Null
        }
        else {
            $serial = Start-Emu $tools $target.Name -Cold:$ColdBoot -Headless:$NoWindow -Timeout $TimeoutSec -DoWait
        }
        Write-Host "AVD=$($target.Name)"
        Write-Host "SERIAL=$serial"
    }
}
