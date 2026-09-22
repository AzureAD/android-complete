# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Lease a device from the pool so concurrent E2E tests don't collide on the same emulator/real
    device. Records who holds each device; reclaims a device automatically when its owning
    agent/session is no longer alive; caps how many devices the pool may use at once.

.DESCRIPTION
    A lease is one small JSON file per device under:
        $env:USERPROFILE\android-e2e-runs\.leases\<serial>.json
    containing { serial, owner, feature, host, pid, startTime, heartbeat }.

    "owner" identifies the holder — pass your agent/session id so a dead agent's lease can be
    reclaimed. Liveness is heartbeat-based: refresh the heartbeat at each test phase; a lease whose
    heartbeat is older than -StaleMinutes is considered abandoned and is reclaimed on the next
    acquire/reap. (A PID is also recorded and, when the owner is on this host, a dead PID makes the
    lease immediately reclaimable.)

    `acquire` is one-stop: it reaps stale leases, picks a free device from the pool
    (emulator.ps1 pool), respects -MaxPoolSize, and — if the pool has no free device but the cap
    allows — boots/creates an emulator via emulator.ps1 to add one, then leases it. Lease creation is
    atomic (create-new file) so two agents racing for the same free device can't both win.

.PARAMETER Command
    acquire | release | heartbeat | list | reap

.EXAMPLE
    # Get a leased, booted device for this agent (same reqs you'd pass emulator.ps1 ensure):
    ./devicelease.ps1 acquire -Owner $AgentId -Feature signin-e2e -RequireGoogleApis -ApiLevel 30 -Wait
    #  -> prints SERIAL=<serial>; use it for the whole run.

    ./devicelease.ps1 heartbeat -Owner $AgentId -Serial emulator-5554   # keep the lease fresh (each phase)
    ./devicelease.ps1 release   -Owner $AgentId -Serial emulator-5554   # when the test finishes
    ./devicelease.ps1 list                                              # who holds what + free devices
    ./devicelease.ps1 reap                                              # drop abandoned leases now

.NOTES
    Leases live OUTSIDE the repo (never committed). Always release when done; if the agent dies
    without releasing, the lease self-expires after -StaleMinutes and becomes reclaimable.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('acquire', 'release', 'heartbeat', 'list', 'reap')]
    [string]$Command = 'list',

    [string]$Owner,                # agent/session id holding the lease (identifies the holder)
    [string]$Serial,               # pin/target a specific device serial
    [string]$Feature,              # free-text label for what's being tested
    [int]$MaxPoolSize = 4,         # cap on how many devices may be leased at once
    [int]$StaleMinutes = 30,       # a lease older than this (no heartbeat) is reclaimable
    [int]$ApiLevel = 0,            # passed through to emulator.ps1 when booting a new device
    [switch]$RequireGoogleApis,
    [switch]$RequirePlayStore,
    [switch]$PreferPhysical,
    [switch]$NoPhysical,
    [string]$Avd,
    [switch]$NoBoot,               # don't boot a new emulator if the pool has no free device
    [switch]$Wait,                 # wait for boot when a device is (re)used
    [switch]$Force,                # release/reap even if owner doesn't match
    [int]$TimeoutSec = 300
)

$ErrorActionPreference = 'Stop'

$LeaseDir = Join-Path $env:USERPROFILE 'android-e2e-runs\.leases'
$EmulatorScript = Join-Path $PSScriptRoot 'emulator.ps1'

function Ensure-LeaseDir { if (-not (Test-Path $LeaseDir)) { New-Item -ItemType Directory -Force -Path $LeaseDir | Out-Null } }
function Get-LeaseFile { param([string]$S) Join-Path $LeaseDir (($S -replace '[:/\\.]', '_') + '.json') }

function Resolve-Owner {
    param([string]$Explicit)
    if ($Explicit) { return $Explicit }
    if ($env:E2E_AGENT_ID) { return $env:E2E_AGENT_ID }
    return "$env:COMPUTERNAME-$PID"
}

function Get-Adb {
    foreach ($v in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT)) {
        if ($v) {
            $p = Join-Path ([Environment]::ExpandEnvironmentVariables($v)) 'platform-tools\adb.exe'
            if (Test-Path $p) { return $p }
            $p2 = Join-Path ([Environment]::ExpandEnvironmentVariables($v)) 'platform-tools/adb'
            if (Test-Path $p2) { return $p2 }
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

function Get-Pool {
    # Ask emulator.ps1 for the current pool (running emulators + connected real devices).
    if (-not (Test-Path $EmulatorScript)) { throw "emulator.ps1 not found next to devicelease.ps1 ($EmulatorScript)." }
    $json = & $EmulatorScript pool -Json -NoPhysical:$NoPhysical 2>$null | Out-String
    if (-not $json.Trim()) { return @() }
    $parsed = $json | ConvertFrom-Json
    if ($null -eq $parsed) { return @() }
    if ($parsed -isnot [array]) { $parsed = @($parsed) }
    return $parsed
}

function Get-Leases {
    Ensure-LeaseDir
    Get-ChildItem $LeaseDir -Filter '*.json' -ErrorAction SilentlyContinue | ForEach-Object {
        try { $o = Get-Content $_.FullName -Raw | ConvertFrom-Json; $o | Add-Member -NotePropertyName _File -NotePropertyValue $_.FullName -Force; $o }
        catch { }  # ignore a half-written lease file; it'll be rewritten/reaped
    }
}

function Test-OwnerAlive {
    # Heartbeat is the authority. On the same host we can also treat a dead recorded PID as dead.
    param($Lease)
    $ageMin = ((Get-Date) - [datetime]$Lease.heartbeat).TotalMinutes
    if ($ageMin -gt $StaleMinutes) { return $false }
    if ($Lease.host -eq $env:COMPUTERNAME -and $Lease.pid) {
        if (-not (Get-Process -Id $Lease.pid -ErrorAction SilentlyContinue)) {
            # PID gone, but only call it dead if the heartbeat is also not fresh (< 2 min protects a
            # brand-new lease taken by a short-lived shell whose PID already exited).
            if ($ageMin -gt 2) { return $false }
        }
    }
    return $true
}

function Reap-StaleLeases {
    $pool = Get-Pool
    $poolSerials = @($pool | ForEach-Object { $_.Serial })
    $reaped = @()
    foreach ($lease in (Get-Leases)) {
        $dead = -not (Test-OwnerAlive $lease)
        $gone = ($poolSerials -notcontains $lease.serial)   # device no longer connected -> lease moot
        if ($dead -or $gone) {
            Remove-Item $lease._File -Force -ErrorAction SilentlyContinue
            $reason = if ($dead) { 'stale/dead-owner' } else { 'device-disconnected' }
            $reaped += [pscustomobject]@{ Serial = $lease.serial; Owner = $lease.owner; Reason = $reason }
        }
    }
    return $reaped
}

function Try-WriteLease {
    # Atomically create the lease file; return $true only if WE created it (won the race).
    param([string]$S, [string]$OwnerId, [string]$Feat)
    $file = Get-LeaseFile $S
    $payload = [pscustomobject]@{
        serial    = $S
        owner     = $OwnerId
        feature   = $Feat
        host      = $env:COMPUTERNAME
        pid       = $PID
        startTime = (Get-Date).ToString('o')
        heartbeat = (Get-Date).ToString('o')
    } | ConvertTo-Json
    try {
        $fs = [System.IO.File]::Open($file, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
            $fs.Write($bytes, 0, $bytes.Length)
        }
        finally { $fs.Dispose() }
        return $true
    }
    catch [System.IO.IOException] {
        return $false   # someone else holds it (file already exists)
    }
}

$adb = Get-Adb
function Wait-Booted {
    # `adb wait-for-device` only blocks until the device is *online* (adbd up), not until Android has
    # finished booting — so poll sys.boot_completed until it flips to 1 (or the timeout elapses) before
    # returning success, otherwise callers race ahead and drive a half-booted device.
    param([string]$S, [int]$TimeoutSec = 120)
    & $adb -s $S wait-for-device 2>$null
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        $b = (& $adb -s $S shell getprop sys.boot_completed 2>$null | Out-String).Trim()
        if ($b -eq '1') { return $true }
        Start-Sleep -Milliseconds 750
    } while ((Get-Date) -lt $deadline)
    return $false
}

switch ($Command) {
    'list' {
        $leases = @(Get-Leases)
        $pool = Get-Pool
        Write-Host "== Active leases ($($leases.Count)) =="
        if ($leases) {
            $leases | ForEach-Object {
                $ageMin = [math]::Round(((Get-Date) - [datetime]$_.heartbeat).TotalMinutes, 1)
                [pscustomobject]@{ Serial = $_.serial; Owner = $_.owner; Feature = $_.feature; Host = $_.host; AgeMin = $ageMin; Alive = (Test-OwnerAlive $_) }
            } | Format-Table -AutoSize | Out-String | Write-Host
        }
        else { Write-Host "(none)`n" }
        $leasedSerials = @($leases | ForEach-Object { $_.serial })
        $free = @($pool | Where-Object { $leasedSerials -notcontains $_.Serial })
        Write-Host "== Free devices in pool ($($free.Count)) =="
        if ($free) { $free | Format-Table -AutoSize Type, Serial, Name, Booted | Out-String | Write-Host } else { Write-Host "(none)`n" }
    }

    'reap' {
        $reaped = Reap-StaleLeases
        if ($reaped) { Write-Host "Reclaimed $($reaped.Count) lease(s):"; $reaped | Format-Table -AutoSize | Out-String | Write-Host }
        else { Write-Host "No stale/abandoned leases to reclaim." }
    }

    'heartbeat' {
        if (-not $Serial) { throw "Provide -Serial." }
        $owner = Resolve-Owner $Owner
        $file = Get-LeaseFile $Serial
        if (-not (Test-Path $file)) { throw "No lease exists for $Serial (nothing to heartbeat). Re-acquire." }
        $lease = Get-Content $file -Raw | ConvertFrom-Json
        if ($lease.owner -ne $owner -and -not $Force) { throw "Lease on $Serial is held by '$($lease.owner)', not '$owner'. Use -Force to override." }
        $lease.heartbeat = (Get-Date).ToString('o')
        $lease | Select-Object serial, owner, feature, host, pid, startTime, heartbeat | ConvertTo-Json | Set-Content -Path $file -Encoding utf8
        Write-Host "Heartbeat updated for $Serial (owner=$owner)."
    }

    'release' {
        if (-not $Serial) { throw "Provide -Serial." }
        $owner = Resolve-Owner $Owner
        $file = Get-LeaseFile $Serial
        if (-not (Test-Path $file)) { Write-Host "No lease for $Serial (already released)."; exit 0 }
        $lease = Get-Content $file -Raw | ConvertFrom-Json
        if ($lease.owner -ne $owner -and -not $Force) { throw "Lease on $Serial is held by '$($lease.owner)', not '$owner'. Use -Force to override." }
        Remove-Item $file -Force
        Write-Host "Released $Serial (owner=$owner)."
    }

    'acquire' {
        $owner = Resolve-Owner $Owner
        Ensure-LeaseDir

        # 1) Reclaim anything abandoned before we count active leases.
        $reaped = Reap-StaleLeases
        if ($reaped) { Write-Host "Reclaimed $($reaped.Count) abandoned lease(s) before acquiring: $((($reaped | ForEach-Object { "$($_.Serial)[$($_.Reason)]" }) -join ', '))" }

        $leases = @(Get-Leases)
        $leasedSerials = @($leases | ForEach-Object { $_.serial })
        $pool = Get-Pool

        # 2) If this owner already holds a lease and no specific serial was requested, reuse it (idempotent).
        if (-not $Serial) {
            $mine = $leases | Where-Object { $_.owner -eq $owner } | Select-Object -First 1
            if ($mine) {
                & (Join-Path $PSScriptRoot 'devicelease.ps1') heartbeat -Serial $mine.serial -Owner $owner | Out-Null
                if ($Wait) { Wait-Booted $mine.serial | Out-Null }
                Write-Host "Reusing existing lease for this owner."
                Write-Host "SERIAL=$($mine.serial)"
                exit 0
            }
        }

        # 3) Choose a candidate free device, or boot one if the cap allows.
        $target = $null
        if ($Serial) {
            $held = $leases | Where-Object { $_.serial -eq $Serial } | Select-Object -First 1
            if ($held -and $held.owner -ne $owner) { throw "Requested device $Serial is leased by '$($held.owner)'." }
            $target = $Serial
        }
        else {
            $free = @($pool | Where-Object { $leasedSerials -notcontains $_.Serial })
            # Prefer an already-booted device; among those, honor -PreferPhysical.
            $booted = @($free | Where-Object { $_.Booted })
            if ($PreferPhysical) { $booted = @($booted | Sort-Object { if ($_.Type -eq 'physical') { 0 } else { 1 } }) }
            if ($booted.Count -gt 0) { $target = $booted[0].Serial }
            elseif ($free.Count -gt 0) { $target = $free[0].Serial }
        }

        # 4) If nothing free, either boot a new emulator (cap permitting) or fail with guidance.
        if (-not $target) {
            if ($leases.Count -ge $MaxPoolSize) {
                throw "Device pool is at capacity ($($leases.Count)/$MaxPoolSize) and every device is held by a live owner. Wait and retry, raise -MaxPoolSize, connect another device, or release a lease. Current holders:`n" + (($leases | ForEach-Object { "  $($_.serial) <- $($_.owner) ($($_.feature))" }) -join "`n")
            }
            if ($NoBoot) { throw "No free device in the pool and -NoBoot was set. Connect/boot a device or drop -NoBoot." }
            Write-Host "No free device; booting one via emulator.ps1 ensure (active leases $($leases.Count)/$MaxPoolSize)..."
            $ensureArgs = @('ensure', '-Wait')
            if ($Avd) { $ensureArgs += @('-Avd', $Avd) }
            if ($ApiLevel -gt 0) { $ensureArgs += @('-ApiLevel', $ApiLevel) }
            if ($RequireGoogleApis) { $ensureArgs += '-RequireGoogleApis' }
            if ($RequirePlayStore) { $ensureArgs += '-RequirePlayStore' }
            if ($PreferPhysical) { $ensureArgs += '-PreferPhysical' }
            if ($NoPhysical) { $ensureArgs += '-NoPhysical' }
            $out = & $EmulatorScript @ensureArgs 2>&1
            $out | Write-Host
            $serialLine = ($out | Select-String '^SERIAL=(.+)$' | Select-Object -Last 1)
            if (-not $serialLine) { throw "emulator.ps1 ensure did not yield a SERIAL=. Cannot acquire a device." }
            $target = $serialLine.Matches[0].Groups[1].Value.Trim()
        }

        # 5) Claim it atomically. If we lost the race, retry the whole acquire once.
        if (-not (Try-WriteLease $target $owner $Feature)) {
            Write-Host "Lost the race for $target (another agent claimed it); retrying acquire..."
            $retryArgs = @('acquire', '-Owner', $owner, '-MaxPoolSize', $MaxPoolSize, '-StaleMinutes', $StaleMinutes, '-TimeoutSec', $TimeoutSec)
            if ($Feature) { $retryArgs += @('-Feature', $Feature) }
            if ($ApiLevel -gt 0) { $retryArgs += @('-ApiLevel', $ApiLevel) }
            if ($RequireGoogleApis) { $retryArgs += '-RequireGoogleApis' }
            if ($RequirePlayStore) { $retryArgs += '-RequirePlayStore' }
            if ($PreferPhysical) { $retryArgs += '-PreferPhysical' }
            if ($NoPhysical) { $retryArgs += '-NoPhysical' }
            if ($NoBoot) { $retryArgs += '-NoBoot' }
            if ($Wait) { $retryArgs += '-Wait' }
            & (Join-Path $PSScriptRoot 'devicelease.ps1') @retryArgs
            exit $LASTEXITCODE
        }

        if ($Wait) { Wait-Booted $target | Out-Null }
        Write-Host "Leased $target for owner=$owner (feature=$Feature). Refresh with 'heartbeat', free with 'release'."
        Write-Host "SERIAL=$target"
    }
}
