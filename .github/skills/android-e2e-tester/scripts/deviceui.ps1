# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Read and drive an Android device/emulator UI via adb + uiautomator so the agent can
    perform AI-handleable inputs automatically (tap buttons, type credentials, grant
    permission dialogs, simulate a fingerprint, read on-screen text, capture screenshots).

.PARAMETER Command
    dump | find-text | tap-text | tap-desc | input-text | wait-text | screenshot |
    key | finger | finger-status | finger-enroll | current-app | tap-xy

.EXAMPLE
    ./deviceui.ps1 wait-text -Text "Sign in" -TimeoutSec 30
    ./deviceui.ps1 tap-text -Text "Sign in"
    ./deviceui.ps1 input-text -Text "user@contoso.com"
    ./deviceui.ps1 input-text -Text "user@contoso.com" -Clear -CharByChar   # defeat autofill/passkey overlays
    ./deviceui.ps1 input-text -Text $pw -Clear -CharByChar -Secret          # never echoes the value
    ./deviceui.ps1 key -Text ENTER
    ./deviceui.ps1 finger-status                  # is a fingerprint enrolled?
    ./deviceui.ps1 finger-enroll                  # enroll one on an emulator (or prompt if a real device)
    ./deviceui.ps1 finger -Text 1                 # simulate a touch of enrolled fingerprint id 1
    ./deviceui.ps1 screenshot -Out C:\runs\step1.png
    ./deviceui.ps1 current-app              # resolved/focused package + activity

.NOTES
    Text matching is case-insensitive substring by default. Use -Exact for equality.
    Always re-`dump` after an action; the UI tree changes between steps.
    input-text options: -Clear empties the focused field first (one adb round-trip); -CharByChar types one
    character at a time (defeats Chrome autofill/passkey overlays that swallow a bulk `input text`);
    -Secret suppresses echoing the value to the transcript (prints length only) — always use it for passwords.
    Fingerprint: `emu finger` only works on EMULATORS. On a real device a fingerprint must be enrolled
    by the user against the physical sensor — `finger-enroll` will print the steps and ask. When a step
    needs an injectable fingerprint/biometric (App Lock, biometric-gated number-match), prefer an emulator.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('dump', 'find-text', 'tap-text', 'tap-desc', 'input-text', 'wait-text',
        'screenshot', 'key', 'finger', 'finger-status', 'finger-enroll', 'current-app', 'tap-xy')]
    [string]$Command = 'dump',

    [string]$Serial,
    [string]$Text,
    [int]$X,
    [int]$Y,
    [int]$Index = 0,
    [switch]$Exact,
    [int]$TimeoutSec = 20,
    [string]$Pin = '1234',
    [string]$Out,
    [switch]$Clear,
    [switch]$CharByChar,
    [int]$PerCharDelayMs = 60,
    [switch]$Secret
)

$ErrorActionPreference = 'Stop'

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

$adb = Get-Adb
function Adb {
    if ($Serial) { & $adb -s $Serial @args } else { & $adb @args }
}

function Get-UiXml {
    for ($i = 0; $i -lt 3; $i++) {
        Adb shell uiautomator dump /sdcard/window_dump.xml 2>$null | Out-Null
        $xml = (Adb exec-out cat /sdcard/window_dump.xml 2>$null | Out-String)
        if ($xml -match '<hierarchy') { return $xml }
        Start-Sleep -Milliseconds 600
    }
    throw "Could not capture a UI hierarchy (uiautomator dump failed 3x). The screen may be mid-animation or a secure window."
}

function Get-Nodes {
    param([string]$Xml)
    $doc = [xml]$Xml
    $result = foreach ($n in $doc.SelectNodes('//node')) {
        $b = $n.GetAttribute('bounds')
        $cx = $null; $cy = $null
        if ($b -match '\[(\d+),(\d+)\]\[(\d+),(\d+)\]') {
            $cx = [int](([int]$Matches[1] + [int]$Matches[3]) / 2)
            $cy = [int](([int]$Matches[2] + [int]$Matches[4]) / 2)
        }
        [pscustomobject]@{
            Text = $n.GetAttribute('text'); Desc = $n.GetAttribute('content-desc');
            Res = $n.GetAttribute('resource-id'); Class = $n.GetAttribute('class');
            Clickable = $n.GetAttribute('clickable'); Bounds = $b; Cx = $cx; Cy = $cy
        }
    }
    return $result
}

function Find-ByField {
    param([string]$Field, [string]$Query)
    $nodes = Get-Nodes (Get-UiXml)
    $match = $nodes | Where-Object {
        $val = $_.$Field
        if ([string]::IsNullOrEmpty($val)) { return $false }
        if ($Exact) { $val -ieq $Query } else { $val -match [regex]::Escape($Query) }
    }
    return $match
}

function Encode-Input {
    param([string]$s)
    # adb 'input text' uses %s for space; backslash-escape shell metacharacters.
    $s = $s -replace '([()<>|;&*~"''`$\\])', '\$1'
    $s = $s -replace ' ', '%s'
    return $s
}

function Test-IsEmulator {
    # An explicit emulator-XXXX serial is an emulator; otherwise probe the running device.
    if ($Serial) { return ($Serial -match '^emulator-') }
    $qemu = (Adb shell getprop ro.kernel.qemu 2>$null | Out-String).Trim()
    if ($qemu -eq '1') { return $true }
    $chars = (Adb shell getprop ro.build.characteristics 2>$null | Out-String).Trim()
    return ($chars -match 'emulator')
}

function Get-FingerprintStatus {
    # Best-effort: 'yes' | 'no' | 'unknown'. dumpsys layout varies by API level, so match a few shapes.
    $dump = (Adb shell dumpsys fingerprint 2>$null | Out-String)
    if (-not $dump.Trim()) { return 'unknown' }
    # Modern JSON shape: {"prints":[{"id":<user>,"count":<enrolled>,...}]} — enrolled if any count >= 1.
    if ($dump -match '"prints"\s*:\s*\[') {
        $counts = [regex]::Matches($dump, '"count"\s*:\s*(\d+)') | ForEach-Object { [int]$_.Groups[1].Value }
        if (@($counts | Where-Object { $_ -ge 1 }).Count -gt 0) { return 'yes' }
        if ($counts.Count -gt 0) { return 'no' }
    }
    # Common markers of an enrolled template across older versions.
    if ($dump -match 'Fingerprint\s*\(.*id=\d+' -or
        $dump -match 'enrolledTemplates?=\s*\[?\s*[1-9]' -or
        $dump -match 'mEnrolledFingerprints=\[[^\]]+\]' -or
        $dump -match 'numEnrolled=\s*[1-9]' -or
        $dump -match 'templates?:\s*[1-9]') {
        return 'yes'
    }
    # Explicit "none enrolled" shapes.
    if ($dump -match 'enrolledTemplates?=\s*\[\s*\]' -or $dump -match 'numEnrolled=\s*0' -or $dump -match 'mEnrolledFingerprints=\[\]') {
        return 'no'
    }
    return 'unknown'
}

switch ($Command) {
    'dump' {
        Get-Nodes (Get-UiXml) | Where-Object { $_.Text -or $_.Desc } |
            Select-Object Text, Desc, Res, Clickable, Bounds | Format-Table -AutoSize | Out-String | Write-Host
    }
    'find-text' {
        $m = Find-ByField 'Text' $Text
        if (-not $m) { $m = Find-ByField 'Desc' $Text }
        if (-not $m) { Write-Host "NOT FOUND: '$Text'"; exit 2 }
        $m | Select-Object Text, Desc, Res, Bounds, Cx, Cy | Format-Table -AutoSize | Out-String | Write-Host
    }
    'tap-text' {
        $m = @(Find-ByField 'Text' $Text)
        if (-not $m) { $m = @(Find-ByField 'Desc' $Text) }
        if (-not $m) { Write-Host "NOT FOUND: '$Text'"; exit 2 }
        $t = $m[[Math]::Min($Index, $m.Count - 1)]
        if ($null -eq $t.Cx) { Write-Host "Element '$Text' has no tappable bounds."; exit 3 }
        Adb shell input tap $t.Cx $t.Cy | Out-Null
        Write-Host "Tapped '$Text' at ($($t.Cx),$($t.Cy))"
    }
    'tap-desc' {
        $m = @(Find-ByField 'Desc' $Text)
        if (-not $m) { Write-Host "NOT FOUND (content-desc): '$Text'"; exit 2 }
        $t = $m[[Math]::Min($Index, $m.Count - 1)]
        Adb shell input tap $t.Cx $t.Cy | Out-Null
        Write-Host "Tapped desc '$Text' at ($($t.Cx),$($t.Cy))"
    }
    'tap-xy' {
        Adb shell input tap $X $Y | Out-Null
        Write-Host "Tapped ($X,$Y)"
    }
    'input-text' {
        # Optionally clear the focused field first: MOVE_END then a burst of DEL — all in ONE adb call.
        if ($Clear) {
            $clearCodes = @('input', 'keyevent', '123') + (1..80 | ForEach-Object { '67' })
            Adb shell $clearCodes | Out-Null
        }
        if ($CharByChar) {
            # Type one character at a time. Chrome autofill / passkey overlays swallow a bulk `input text`
            # (the whole string lands in an unexpected field or is dropped); per-char typing lands reliably.
            foreach ($ch in $Text.ToCharArray()) {
                Adb shell input text (Encode-Input ([string]$ch)) | Out-Null
                if ($PerCharDelayMs -gt 0) { Start-Sleep -Milliseconds $PerCharDelayMs }
            }
            if ($Secret) { Write-Host "Typed (char-by-char): [$($Text.Length) chars]" }
            else { Write-Host "Typed (char-by-char): $Text" }
        }
        else {
            Adb shell input text (Encode-Input $Text) | Out-Null
            if ($Secret) { Write-Host "Typed: [$($Text.Length) chars]" }
            else { Write-Host "Typed: $Text" }
        }
    }
    'wait-text' {
        $deadline = (Get-Date).AddSeconds($TimeoutSec)
        while ((Get-Date) -lt $deadline) {
            $m = Find-ByField 'Text' $Text
            if (-not $m) { $m = Find-ByField 'Desc' $Text }
            if ($m) { Write-Host "FOUND: '$Text'"; exit 0 }
            Start-Sleep -Seconds 2
        }
        Write-Host "TIMEOUT waiting for '$Text' (${TimeoutSec}s)"; exit 4
    }
    'screenshot' {
        if (-not $Out) { $Out = Join-Path (Get-Location) ("screen_{0}.png" -f (Get-Date -Format 'yyyyMMdd_HHmmss')) }
        $d = Split-Path $Out -Parent; if ($d -and -not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
        Adb shell screencap -p /sdcard/_sc.png | Out-Null
        Adb pull /sdcard/_sc.png $Out | Out-Null
        Adb shell rm -f /sdcard/_sc.png | Out-Null
        Write-Host "Saved screenshot: $Out"
    }
    'key' {
        $map = @{ BACK = 4; HOME = 3; ENTER = 66; TAB = 61; MENU = 82; APP_SWITCH = 187; DEL = 67; ESCAPE = 111; SEARCH = 84 }
        $code = if ($map.ContainsKey($Text.ToUpper())) { $map[$Text.ToUpper()] } else { $Text }
        Adb shell input keyevent $code | Out-Null
        Write-Host "Key: $Text ($code)"
    }
    'finger' {
        $id = if ($Text) { $Text } else { '1' }
        if (Test-IsEmulator) {
            if ($Serial) { & $adb -s $Serial emu finger touch $id | Out-Null } else { & $adb -e emu finger touch $id | Out-Null }
            Write-Host "Simulated fingerprint touch id=$id on emulator."
        }
        else {
            Write-Host "PROMPT-USER: This is a real device — `emu finger` cannot simulate a touch."
            Write-Host "Please press the enrolled finger on the device's sensor now, then continue."
            exit 5
        }
    }
    'finger-status' {
        $status = Get-FingerprintStatus
        Write-Host "Fingerprint enrolled: $status"
        if ($status -eq 'yes') { exit 0 } elseif ($status -eq 'no') { exit 2 } else { exit 3 }
    }
    'finger-enroll' {
        $id = if ($Text) { $Text } else { '1' }
        if ((Get-FingerprintStatus) -eq 'yes') { Write-Host "A fingerprint is already enrolled; nothing to do."; exit 0 }

        if (-not (Test-IsEmulator)) {
            Write-Host "PROMPT-USER: Real device — a fingerprint must be enrolled against the physical sensor."
            Write-Host "  1) Settings > Security > Fingerprint  (set a screen lock/PIN first if asked)"
            Write-Host "  2) Add a fingerprint and follow the prompts on the sensor."
            Write-Host "  3) Re-run the test once enrolled."
            exit 5
        }

        Write-Host "Enrolling a fingerprint on the emulator (id=$id)..."
        # A screen lock is a prerequisite for fingerprint enrollment.
        Adb shell locksettings set-pin $Pin 2>$null | Out-Null
        # Open the enrollment flow (action exists API 28+); fall back to Security settings.
        Adb shell am start -a android.settings.FINGERPRINT_ENROLL 2>$null | Out-Null
        Start-Sleep -Seconds 2
        # If it asks to confirm the existing PIN, enter it.
        try { Adb shell input text $Pin 2>$null | Out-Null; Adb shell input keyevent 66 2>$null | Out-Null } catch { }
        Start-Sleep -Seconds 1
        # The enroll wizard advances on each sensor touch; emulate several with brief pauses,
        # tapping Next/Done/Confirm/OK when they appear.
        for ($i = 0; $i -lt 8; $i++) {
            if ($Serial) { & $adb -s $Serial emu finger touch $id | Out-Null } else { & $adb -e emu finger touch $id | Out-Null }
            Start-Sleep -Milliseconds 700
            foreach ($label in @('Next', 'Done', 'Confirm', 'OK', 'Got it')) {
                $m = Find-ByField 'Text' $label
                if ($m) { $t = @($m)[0]; if ($t.Cx) { Adb shell input tap $t.Cx $t.Cy | Out-Null }; break }
            }
        }
        Start-Sleep -Seconds 1
        $status = Get-FingerprintStatus
        if ($status -eq 'yes') {
            Write-Host "Fingerprint enrolled (id=$id). Use `finger -Text $id` to simulate a touch."
            exit 0
        }
        Write-Host "PROMPT-USER: Could not confirm automatic fingerprint enrollment on this emulator."
        Write-Host "Please enroll one manually, then re-run:"
        Write-Host "  Settings > Security > Fingerprint > Add fingerprint; when it asks for a touch, run:"
        Write-Host "    ./deviceui.ps1 finger -Text $id -Serial $Serial   (repeat until it completes)"
        exit 5
    }
    'current-app' {
        $win = (Adb shell dumpsys window 2>$null | Out-String)
        $focus = ($win -split "`n" | Where-Object { $_ -match 'mCurrentFocus|mFocusedApp' }) -join "`n"
        Write-Host $focus
    }
}
