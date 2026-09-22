# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Local, DPAPI-encrypted secret store for the android-e2e-tester skill so that passwords and
    device lock-screen PINs NEVER appear in the chat transcript.

    You (the human) run `set` yourself, in your OWN terminal, and TYPE the value at a masked
    prompt. It is DPAPI-encrypted (bound to your Windows user account on this machine) and written
    to %USERPROFILE%\.android-e2e-secrets\<name>.sec. The agent's scripts resolve it at runtime and
    type it straight onto the device with the input masked -- the plaintext is never printed, never
    logged, and never committed (the store lives OUTSIDE the repo).

    There is deliberately NO command that prints a stored secret in clear text.

.PARAMETER Command
    set            Prompt (hidden) for a value and store it encrypted. Run this yourself, not via chat.
    set-device-pin Prompt (hidden) for a lock-screen PIN for a SPECIFIC connected device and store it under
                   a per-device name (devicepin_<serial>). If more than one device is connected you are shown
                   a numbered menu to choose one; with a single device it is selected automatically. Pass
                   -Serial to skip the menu. The PIN is typed twice to guard against a typo (a wrong stored
                   PIN would trip the unlock 3-try lockout stop). Run this yourself, not via chat.
    list           List stored secret NAMES only (never values), plus any E2E_SECRET_* env overrides.
    test           Print only whether a name resolves and its length (e.g. "resolves=yes length=18").
    get-masked     Print the name with the value masked (asterisks) and its length -- for a quick check.
    remove         Delete a stored secret.
    path           Print the store directory.

.PARAMETER Name
    Secret name for set/test/get-masked/remove. For set-device-pin it OVERRIDES the auto per-device name.

.PARAMETER Serial
    adb serial of the target device (set-device-pin only). If omitted with several devices connected you are
    prompted to choose; with one device it is auto-selected.

.EXAMPLE
    # Do this once, in your terminal (NOT through the chat):
    ./secrets.ps1 set -Name labpw
    # ...then just tell the agent: "the lab password is in secret 'labpw'".

.EXAMPLE
    ./secrets.ps1 set  -Name devicepin     # a real device's lock-screen PIN (generic name)
    ./secrets.ps1 list                     # names only
    ./secrets.ps1 test -Name labpw         # -> resolves=yes length=18
    ./secrets.ps1 remove -Name labpw

.EXAMPLE
    # Save a PIN for a PARTICULAR device. With >1 device connected you get a numbered menu:
    ./secrets.ps1 set-device-pin
    #   Multiple devices connected:
    #     [1] R5CXB0P430X      physical  SM-F741U1
    #     [2] emulator-5554    emulator  sdk_gphone64_x86_64
    #   Choose a device [1-2]: 1
    #   -> stored as secret 'devicepin_R5CXB0P430X'
    ./secrets.ps1 set-device-pin -Serial emulator-5554   # target one directly, no menu
    # ...then unlock without re-typing the serial's PIN name:
    ./deviceui.ps1 unlock -Serial R5CXB0P430X            # auto-uses secret 'devicepin_R5CXB0P430X'

.NOTES
    Resolution order (used here and by deviceui.ps1 -SecretRef):
      1. env var  E2E_SECRET_<NAME-UPPERCASE>   (convenient for a one-off; value sits in your user env)
      2. DPAPI file  %USERPROFILE%\.android-e2e-secrets\<name>.sec   (recommended; encrypted at rest)
    A DPAPI file is decryptable ONLY by the same Windows user on the same machine. Works under both
    Windows PowerShell 5.1 and PowerShell 7 (Windows).

    Consumers: deviceui.ps1 input-text -SecretRef <name>   (types the secret, masked)
               deviceui.ps1 unlock      -SecretRef <name>   (enters a lock-screen PIN, masked)
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('list', 'set', 'set-device-pin', 'test', 'get-masked', 'remove', 'path')]
    [string]$Command = 'list',
    [string]$Name,
    [string]$Serial
)

$ErrorActionPreference = 'Stop'

function Get-StoreDir {
    $d = Join-Path $env:USERPROFILE '.android-e2e-secrets'
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    return $d
}

function Get-SecretPath {
    param([string]$n)
    return (Join-Path (Get-StoreDir) ("{0}.sec" -f $n))
}

function Get-DevicePinName {
    # The per-device secret name convention shared with deviceui.ps1: 'devicepin_' + serial (non-alphanumeric
    # chars -> '_'), e.g. R5CXB0P430X -> devicepin_R5CXB0P430X, emulator-5554 -> devicepin_emulator_5554.
    param([Parameter(Mandatory)][string]$Serial)
    return ('devicepin_' + ($Serial -replace '[^A-Za-z0-9]', '_'))
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

function Get-ConnectedDevices {
    # Returns objects { Serial; Kind; Model } for every device in adb 'device' state (emulators + physical).
    param([Parameter(Mandatory)][string]$Adb)
    & $Adb start-server 2>$null | Out-Null
    $out = & $Adb devices 2>$null
    $list = @()
    foreach ($line in $out) {
        if ($line -match '^(\S+)\s+device(\s|$)' -and $Matches[1] -ne 'List') {
            $serial = $Matches[1]
            $isEmu = ($serial -match '^emulator-')
            $model = (& $Adb -s $serial shell getprop ro.product.model 2>$null | Out-String).Trim()
            $list += [pscustomobject]@{
                Serial = $serial
                Kind   = $(if ($isEmu) { 'emulator' } else { 'physical' })
                Model  = $model
            }
        }
    }
    return , $list
}

function Select-Device {
    # Auto-pick a single device; otherwise show a numbered menu and read a valid choice (up to 3 tries).
    param([Parameter(Mandatory)]$Devices)
    if ($Devices.Count -eq 0) {
        throw "No devices in adb 'device' state. Connect a device/emulator (check 'adb devices -l') and retry."
    }
    if ($Devices.Count -eq 1) {
        Write-Host ("Only one device connected: {0} ({1}) {2} -- selecting it." -f $Devices[0].Serial, $Devices[0].Kind, $Devices[0].Model)
        return $Devices[0].Serial
    }
    Write-Host "Multiple devices connected:"
    for ($i = 0; $i -lt $Devices.Count; $i++) {
        Write-Host ("  [{0}] {1,-16} {2,-9} {3}" -f ($i + 1), $Devices[$i].Serial, $Devices[$i].Kind, $Devices[$i].Model)
    }
    for ($try = 1; $try -le 3; $try++) {
        $ans = Read-Host ("Choose a device [1-{0}]" -f $Devices.Count)
        if ($ans -match '^\d+$') {
            $n = [int]$ans
            if ($n -ge 1 -and $n -le $Devices.Count) { return $Devices[$n - 1].Serial }
        }
        Write-Host "  Invalid choice; enter a number from the list."
    }
    throw "No valid device selected after 3 tries; nothing stored."
}

function Test-SecureStringsEqual {
    # Compare two SecureStrings by their transient plaintext (never printed), then zero the buffers.
    param([Parameter(Mandatory)][Security.SecureString]$A, [Parameter(Mandatory)][Security.SecureString]$B)
    $pa = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($A)
    $pb = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($B)
    try {
        return ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pa) -ceq [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pb))
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pa)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pb)
    }
}

function Resolve-Secret {
    # Returns the plaintext as a [string]. Deliberately writes NOTHING to the host/pipeline other than
    # the returned value, so callers can capture it without it ever being displayed.
    param([Parameter(Mandatory)][string]$Name)
    $envName = 'E2E_SECRET_' + ($Name.ToUpper() -replace '[^A-Z0-9_]', '_')
    $envVal = [Environment]::GetEnvironmentVariable($envName)
    if ($envVal) { return $envVal }
    $p = Get-SecretPath $Name
    if (-not (Test-Path $p)) {
        throw "No secret named '$Name' (env $envName is unset and $p does not exist). Add it with: secrets.ps1 set -Name $Name"
    }
    $enc = Get-Content -Raw -Path $p
    $ss = ConvertTo-SecureString $enc     # DPAPI decrypt (per-user, per-machine)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

# Only run the CLI dispatch when invoked directly, not when dot-sourced for Resolve-Secret.
if ($MyInvocation.InvocationName -ne '.') {
    switch ($Command) {
        'set' {
            if (-not $Name) { throw "-Name is required for 'set'." }
            $ss = Read-Host -AsSecureString ("Enter secret value for '{0}' (input hidden)" -f $Name)
            if ($ss.Length -eq 0) { throw "Empty value; nothing stored." }
            $enc = ConvertFrom-SecureString $ss    # DPAPI encrypt
            Set-Content -Path (Get-SecretPath $Name) -Value $enc -NoNewline -Encoding ASCII
            Write-Host ("Stored secret '{0}' (DPAPI-encrypted) at {1}" -f $Name, (Get-SecretPath $Name))
        }
        'set-device-pin' {
            # Save a lock-screen PIN for a SPECIFIC connected device. Choose the device (auto if one, menu if
            # many, or -Serial to target directly), then type the PIN twice. Stored under devicepin_<serial>
            # (or -Name override) so deviceui.ps1 unlock -Serial <s> can auto-resolve it. PIN is never printed.
            $adb = Get-Adb
            $devices = Get-ConnectedDevices -Adb $adb
            if ($Serial) {
                if (-not ($devices | Where-Object { $_.Serial -eq $Serial })) {
                    $have = (($devices | ForEach-Object { $_.Serial }) -join ', ')
                    throw "Serial '$Serial' is not connected (adb 'device' state). Connected: $have"
                }
                $chosen = $Serial
                Write-Host ("Target device: {0}" -f $chosen)
            }
            else {
                $chosen = Select-Device -Devices $devices
            }
            $secretName = if ($Name) { $Name } else { Get-DevicePinName $chosen }
            $ss1 = Read-Host -AsSecureString ("Enter lock-screen PIN for {0} (input hidden)" -f $chosen)
            if ($ss1.Length -eq 0) { throw "Empty PIN; nothing stored." }
            $ss2 = Read-Host -AsSecureString "Re-enter the PIN to confirm (input hidden)"
            if (-not (Test-SecureStringsEqual $ss1 $ss2)) {
                throw "PINs did not match; nothing stored. Re-run 'set-device-pin' and type the same PIN twice."
            }
            $enc = ConvertFrom-SecureString $ss1   # DPAPI encrypt
            Set-Content -Path (Get-SecretPath $secretName) -Value $enc -NoNewline -Encoding ASCII
            Write-Host ("Stored PIN for device {0} as secret '{1}' (DPAPI-encrypted) at {2}" -f $chosen, $secretName, (Get-SecretPath $secretName))
            Write-Host ("Unlock later with:  ./deviceui.ps1 unlock -Serial {0}   (auto-uses secret '{1}')" -f $chosen, $secretName)
        }
        'test' {
            if (-not $Name) { throw "-Name is required for 'test'." }
            try { $v = Resolve-Secret -Name $Name; Write-Host ("resolves=yes length={0}" -f $v.Length) }
            catch { Write-Host "resolves=no"; exit 2 }
        }
        'get-masked' {
            if (-not $Name) { throw "-Name is required for 'get-masked'." }
            $v = Resolve-Secret -Name $Name
            Write-Host ("{0} = {1} ({2} chars)" -f $Name, ('*' * [Math]::Min($v.Length, 12)), $v.Length)
        }
        'list' {
            $d = Get-StoreDir
            $files = @(Get-ChildItem -Path $d -Filter *.sec -ErrorAction SilentlyContinue)
            if ($files.Count -eq 0) { Write-Host "(no secrets stored in $d)" }
            else { Write-Host "Stored secrets (names only):"; $files | ForEach-Object { Write-Host ("  {0}" -f $_.BaseName) } }
            $envOverrides = @(Get-ChildItem Env: | Where-Object { $_.Name -like 'E2E_SECRET_*' })
            if ($envOverrides.Count -gt 0) {
                Write-Host "Env overrides (names only):"
                $envOverrides | ForEach-Object { Write-Host ("  {0}" -f $_.Name) }
            }
        }
        'remove' {
            if (-not $Name) { throw "-Name is required for 'remove'." }
            $p = Get-SecretPath $Name
            if (Test-Path $p) { Remove-Item $p -Force; Write-Host "Removed secret '$Name'." }
            else { Write-Host "No secret '$Name' to remove." }
        }
        'path' { Write-Host (Get-StoreDir) }
    }
}
