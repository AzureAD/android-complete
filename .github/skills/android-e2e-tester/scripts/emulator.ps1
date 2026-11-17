# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Android device-pool helper for E2E testing: resolve the SDK, enumerate the device pool
    (AVDs, running emulators, AND connected real devices), create an AVD, start one, wait for
    boot, or do all of that in one shot with `ensure`.

.DESCRIPTION
    Self-resolves the Android SDK location (handles unexpanded %LOCALAPPDATA% in
    ANDROID_HOME) and never hardcodes machine paths. Prefers fast operations
    (`emulator -list-avds`, reading the system-images/ dir) over the slow
    avdmanager/sdkmanager where possible.

    The "device pool" is emulators PLUS any real devices connected over adb. `ensure` can hand
    back a connected real device that meets the feature's requirements (use `-PreferPhysical` to
    favor one, or `-NoPhysical` to stick to emulators). Real devices are never created/booted —
    they're only used if already connected and booted.

    PERFORMANCE: emulator launches auto-pick the fastest usable `-gpu` mode (host GPU when present,
    else SwiftShader software) and generous `-cores`/`-memory` from the host. On a GPU-less host
    (Cloud PC / VM / RDP) the emulator can only do slow software rendering, so `ensure` will
    automatically prefer a connected physical device when one is available (override with
    `-NoPhysical`). Run `resolve-sdk` to see the host GPU/perf profile.

.PARAMETER Command
    resolve-sdk | list | list-images | status | pool | create | start | ensure

.EXAMPLE
    # One-shot: guarantee a booted emulator meeting the feature's needs, print its serial.
    ./emulator.ps1 ensure -ApiLevel 34 -RequireGoogleApis -Wait

.EXAMPLE
    ./emulator.ps1 resolve-sdk                         # SDK paths + host GPU/perf profile
    ./emulator.ps1 ensure -Avd Pixel_7 -Wait          # use/create+start a named AVD
    ./emulator.ps1 ensure -PreferPhysical -Wait        # prefer a connected real device if one fits
    ./emulator.ps1 ensure -Cores 6 -Memory 6144 -Gpu host -Wait   # tune emulator resources/GPU
    ./emulator.ps1 pool                                # the whole device pool (emulators + real devices)
    ./emulator.ps1 status                             # what is running right now
    ./emulator.ps1 list-images                        # installed system images (for create)
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('resolve-sdk', 'list', 'list-images', 'status', 'pool', 'create', 'start', 'ensure')]
    [string]$Command = 'status',

    [string]$Avd,                 # explicit AVD name to use/create
    [string]$Serial,              # pin an exact device (emulator serial or real-device serial)
    [int]$ApiLevel = 0,           # minimum API level requirement (0 = any)
    [string]$Image,               # explicit system-image package id for create
    [string]$Device = 'pixel_7',  # device profile for create
    [int]$Cores = 0,              # vCPU cores for the emulator (0 = auto: min(6, host/2))
    [int]$Memory = 0,             # RAM MB for the emulator (0 = auto: 4096, or 6144 when host RAM allows)
    [string]$Gpu,                 # emulator -gpu mode (host|swiftshader_indirect|auto). Empty = auto-detect.
    [switch]$RequireGoogleApis,   # require google_apis or google_apis_playstore tag (GMS/push)
    [switch]$RequirePlayStore,    # require google_apis_playstore tag
    [switch]$PreferPhysical,      # prefer a connected real device over booting an emulator
    [switch]$NoPhysical,          # exclude connected real devices from the pool
    [switch]$Wait,                # wait for full boot
    [switch]$ColdBoot,            # start with a clean state (-no-snapshot-load)
    [switch]$NoWindow,            # headless (-no-window)
    [switch]$Json,                # machine-readable output (for `pool`)
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

# Real hardware connected over adb (USB / Wi-Fi / cloud) — anything in 'device' state whose
# serial is not an emulator-XXXX console serial. These join the pool alongside emulators.
function Get-PhysicalDevices {
    param($Tools)
    & $Tools.Adb start-server 2>$null | Out-Null
    $out = & $Tools.Adb devices 2>$null
    $result = @()
    foreach ($line in $out) {
        # SERIAL<TAB|spaces>device  — exclude emulators, the header, and offline/unauthorized states.
        if ($line -match '^(\S+)\s+device(\s|$)' -and $line -notmatch '^emulator-' -and $Matches[1] -ne 'List') {
            $serial = $Matches[1]
            $model = (& $Tools.Adb -s $serial shell getprop ro.product.model 2>$null | Out-String).Trim()
            $sdk = (& $Tools.Adb -s $serial shell getprop ro.build.version.sdk 2>$null | Out-String).Trim()
            $api = 0; if ($sdk -match '^\d+$') { $api = [int]$sdk }
            $gms = (& $Tools.Adb -s $serial shell pm list packages com.google.android.gms 2>$null | Out-String)
            $play = (& $Tools.Adb -s $serial shell pm list packages com.android.vending 2>$null | Out-String)
            $booted = (& $Tools.Adb -s $serial shell getprop sys.boot_completed 2>$null | Out-String).Trim()
            $result += [pscustomobject]@{
                Serial     = $serial
                Model      = $model
                Api        = $api
                GoogleApis = ($gms -match 'com.google.android.gms')
                PlayStore  = ($play -match 'com.android.vending')
                Booted     = ($booted -eq '1')
            }
        }
    }
    return $result
}

# Does a real device satisfy the feature's requirements? (Emulator reqs are checked via AVD tags.)
function Test-PhysicalMeetsReqs {
    param($Dev, [int]$MinApi, [switch]$NeedGoogle, [switch]$NeedPlay)
    if ($MinApi -gt 0 -and $Dev.Api -gt 0 -and $Dev.Api -lt $MinApi) { return $false }
    if ($NeedPlay -and -not $Dev.PlayStore) { return $false }
    if ($NeedGoogle -and -not $Dev.GoogleApis) { return $false }
    return $true
}

# Unified device pool: running emulators + connected real devices, as one list with a Type column.
function Get-DevicePool {
    param($Tools, [switch]$NoPhysical)
    $pool = @()
    foreach ($e in (Get-RunningEmulators $Tools)) {
        $pool += [pscustomobject]@{ Type = 'emulator'; Serial = $e.Serial; Name = $e.Avd; Api = $null; GoogleApis = $null; PlayStore = $null; Booted = $e.Booted }
    }
    if (-not $NoPhysical) {
        foreach ($p in (Get-PhysicalDevices $Tools)) {
            $pool += [pscustomobject]@{ Type = 'physical'; Serial = $p.Serial; Name = $p.Model; Api = $p.Api; GoogleApis = $p.GoogleApis; PlayStore = $p.PlayStore; Booted = $p.Booted }
        }
    }
    return $pool
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
# Host performance profiling (GPU / virtualization) — drives fast, correct emulator flags
# ---------------------------------------------------------------------------
# Returns $true if the host has a real, emulator-usable GPU. On a Cloud PC / VM / RDP session there is
# typically only a "Microsoft Basic/Hyper-V/Remote Display" adapter with no dedicated memory, in which
# case the emulator can only do SLOW SwiftShader software rendering — and a physical device is far better.
function Test-HostGpuAvailable {
    try {
        $vc = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
        foreach ($g in $vc) {
            $n = "$($g.Name)"
            if ($n -match 'Microsoft (Basic|Hyper-V|Remote)') { continue }        # virtual/remote adapters
            if ($n -match 'RDP|Citrix|VMware|VirtualBox|Parsec') { continue }      # remoting adapters
            if ($g.AdapterRAM -and $g.AdapterRAM -gt 0) { return $true }           # a real GPU with VRAM
            if ($n -match 'NVIDIA|AMD|Radeon|Intel|Arc|GeForce|Quadro') { return $true }
        }
    } catch { }
    return $false
}

function Test-VirtualOrRemoteHost {
    $reasons = @()
    try {
        $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
        if ("$($cs.Model)" -match 'Virtual Machine|VMware|VirtualBox' -or "$($cs.Manufacturer)" -match 'QEMU|Xen|innotek') { $reasons += "VM ($($cs.Model))" }
    } catch { }
    if ($env:SESSIONNAME -and $env:SESSIONNAME -match '^rdp') { $reasons += "RDP session" }
    if ("$env:COMPUTERNAME" -match '^CPC-') { $reasons += "Cloud PC" }
    return $reasons
}

# Pick the fastest emulator -gpu mode the host can actually use.
function Get-BestGpuMode {
    param([switch]$Headless)
    if ($Gpu) { return $Gpu }                                   # explicit override wins
    if ($Headless) { return 'swiftshader_indirect' }            # headless/CI: software is the safe choice
    if (Test-HostGpuAvailable) { return 'host' }                # real GPU: hardware acceleration
    return 'swiftshader_indirect'                               # GPU-less VM/RDP: software (auto would pick this anyway)
}

# Resolve auto cores/memory from the host (plenty of headroom on a 16-vCPU/64-GB Cloud PC).
function Resolve-EmuResources {
    $cs = $null; try { $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue } catch { }
    $hostCores = if ($cs) { [int]$cs.NumberOfLogicalProcessors } else { 4 }
    $hostGb = if ($cs) { [math]::Round($cs.TotalPhysicalMemory / 1GB, 0) } else { 8 }
    $cores = if ($Cores -gt 0) { $Cores } else { [Math]::Max(4, [Math]::Min(6, [int]($hostCores / 2))) }
    $mem = if ($Memory -gt 0) { $Memory } else { if ($hostGb -ge 32) { 6144 } elseif ($hostGb -ge 16) { 4096 } else { 3072 } }
    return @{ Cores = $cores; Memory = $mem }
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
    $res = Resolve-EmuResources
    $gpuMode = Get-BestGpuMode -Headless:$Headless
    $emuArgs = @('-avd', $Name, '-no-boot-anim', '-netdelay', 'none', '-netspeed', 'full',
        '-gpu', $gpuMode, '-cores', "$($res.Cores)", '-memory', "$($res.Memory)")
    if ($Cold) { $emuArgs += '-no-snapshot-load' }
    if ($Headless) { $emuArgs += '-no-window' }
    if ($gpuMode -eq 'swiftshader_indirect' -and -not $Headless) {
        $why = (Test-VirtualOrRemoteHost) -join ', '
        Write-Host "PERF NOTE: no host GPU detected$(if ($why) { " ($why)" }); the emulator will use SLOW software rendering (SwiftShader)." -ForegroundColor Yellow
        Write-Host "           For a fast run, use a connected physical device: emulator.ps1 ensure -PreferPhysical  (or devicelease.ps1 acquire -PreferPhysical)." -ForegroundColor Yellow
    }
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
        # Host perf profile — explains emulator speed and drives the fast defaults.
        $gpuOk = Test-HostGpuAvailable
        $virt = (Test-VirtualOrRemoteHost) -join ', '
        $res = Resolve-EmuResources
        Write-Host "host GPU:     $(if ($gpuOk) { 'available -> emulator uses HARDWARE (host) rendering' } else { 'NONE -> emulator uses SLOW software (SwiftShader) rendering' })"
        if ($virt) { Write-Host "host type:    $virt" }
        Write-Host "emu defaults: -gpu $(Get-BestGpuMode) -cores $($res.Cores) -memory $($res.Memory)"
        if (-not $gpuOk) { Write-Host "TIP:          use a physical device for a fast run (emulator.ps1 ensure -PreferPhysical)." -ForegroundColor Yellow }
    }
    'list' {
        Write-Host "== AVDs =="
        Get-Avds $tools | Format-Table -AutoSize | Out-String | Write-Host
        Write-Host "== Running emulators =="
        Get-RunningEmulators $tools | Format-Table -AutoSize | Out-String | Write-Host
        if (-not $NoPhysical) {
            Write-Host "== Connected real devices =="
            $phys = Get-PhysicalDevices $tools
            if ($phys) { $phys | Format-Table -AutoSize | Out-String | Write-Host } else { Write-Host "(none)`n" }
        }
    }
    'pool' {
        $pool = Get-DevicePool $tools -NoPhysical:$NoPhysical
        if ($Json) {
            $pool | ConvertTo-Json -Depth 4
        }
        else {
            if (-not $pool) { Write-Host 'Device pool is empty (no running emulator or connected real device).'; exit 0 }
            $pool | Format-Table -AutoSize Type, Serial, Name, Api, GoogleApis, PlayStore, Booted | Out-String | Write-Host
        }
    }
    'list-images' {
        Get-InstalledImages $tools | Sort-Object Api | Format-Table -AutoSize | Out-String | Write-Host
    }
    'status' {
        $running = Get-RunningEmulators $tools
        if ($Avd) { $running = $running | Where-Object { $_.Avd -eq $Avd } }
        $phys = if ($NoPhysical) { @() } else { Get-PhysicalDevices $tools }
        if (-not $running -and -not $phys) { Write-Host 'No emulator running and no real device connected.'; exit 0 }
        if ($running) { Write-Host "== Running emulators =="; $running | Format-Table -AutoSize | Out-String | Write-Host }
        if ($phys) { Write-Host "== Connected real devices =="; $phys | Format-Table -AutoSize | Out-String | Write-Host }
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
        # 0) If a specific device serial was pinned, verify it's connected and use it as-is.
        if ($Serial) {
            $pool = Get-DevicePool $tools -NoPhysical:$NoPhysical
            $pinned = $pool | Where-Object { $_.Serial -eq $Serial } | Select-Object -First 1
            if (-not $pinned) { throw "Pinned device '$Serial' is not connected (not in the device pool)." }
            if ($pinned.Type -eq 'emulator') { Wait-Boot $tools $Serial $TimeoutSec | Out-Null }
            elseif (-not $pinned.Booted) { throw "Pinned real device '$Serial' is connected but not fully booted." }
            Write-Host "Using pinned device: $Serial (type=$($pinned.Type))"
            Write-Host "SERIAL=$Serial"
            exit 0
        }

        # 0b) Prefer a connected real device when asked (and one meets the requirements).
        if ($PreferPhysical -and -not $NoPhysical) {
            $physMatch = Get-PhysicalDevices $tools |
                Where-Object { $_.Booted -and (Test-PhysicalMeetsReqs $_ -MinApi $ApiLevel -NeedGoogle:$RequireGoogleApis -NeedPlay:$RequirePlayStore) } |
                Select-Object -First 1
            if ($physMatch) {
                Write-Host "Using connected real device: $($physMatch.Serial) (model=$($physMatch.Model), api=$($physMatch.Api))"
                Write-Host "SERIAL=$($physMatch.Serial)"
                exit 0
            }
            Write-Host "No connected real device meets the requirements; falling back to an emulator."
        }

        # 0c) On a GPU-less host (Cloud PC / VM / RDP) the emulator is painfully slow (software rendering).
        #     If a suitable real device is already connected, auto-prefer it — that's what the developer wants.
        if (-not $PreferPhysical -and -not $NoPhysical -and -not (Test-HostGpuAvailable)) {
            $physMatch = Get-PhysicalDevices $tools |
                Where-Object { $_.Booted -and (Test-PhysicalMeetsReqs $_ -MinApi $ApiLevel -NeedGoogle:$RequireGoogleApis -NeedPlay:$RequirePlayStore) } |
                Select-Object -First 1
            $why = (Test-VirtualOrRemoteHost) -join ', '
            if ($physMatch) {
                Write-Host "No host GPU$(if ($why) { " ($why)" }) => emulator would be slow; using the connected real device instead: $($physMatch.Serial) (model=$($physMatch.Model)). Pass -NoPhysical to force the emulator." -ForegroundColor Yellow
                Write-Host "SERIAL=$($physMatch.Serial)"
                exit 0
            }
            Write-Host "PERF NOTE: no host GPU$(if ($why) { " ($why)" }); the emulator will use SLOW software rendering. Connect a physical device for a fast run (it will be used automatically)." -ForegroundColor Yellow
        }

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
            # Next best: a connected real device that meets the requirements (reuse beats a cold boot).
            if (-not $target -and -not $NoPhysical) {
                $physMatch = Get-PhysicalDevices $tools |
                    Where-Object { $_.Booted -and (Test-PhysicalMeetsReqs $_ -MinApi $ApiLevel -NeedGoogle:$RequireGoogleApis -NeedPlay:$RequirePlayStore) } |
                    Select-Object -First 1
                if ($physMatch) {
                    Write-Host "Using connected real device: $($physMatch.Serial) (model=$($physMatch.Model), api=$($physMatch.Api))"
                    Write-Host "SERIAL=$($physMatch.Serial)"
                    exit 0
                }
            }
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
