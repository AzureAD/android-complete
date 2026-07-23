# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Read and drive an Android device/emulator UI via adb + uiautomator so the agent can
    perform AI-handleable inputs automatically (tap buttons, type credentials, grant
    permission dialogs, simulate a fingerprint, read on-screen text, capture screenshots).

.PARAMETER Command
    dump | find-text | tap-text | tap-desc | input-text | wait-text | screenshot |
    key | finger | finger-status | finger-enroll | current-app | tap-xy | unlock

.EXAMPLE
    ./deviceui.ps1 wait-text -Text "Sign in" -TimeoutSec 30
    ./deviceui.ps1 tap-text -Text "Next" -Then "Enter password"   # tap + verify next screen in ONE call
    ./deviceui.ps1 tap-text -Text "Sign in"
    ./deviceui.ps1 input-text -Text "user@contoso.com"                       # bulk (fast) — try this first
    ./deviceui.ps1 input-text -Text "user@contoso.com" -Clear -CharByChar    # fall back if autofill ate it
    ./deviceui.ps1 input-text -SecretRef labpw -Clear -CharByChar            # types a stored password, masked
    ./deviceui.ps1 unlock -Serial <serial>                                  # auto-uses the PIN saved for that device (secrets.ps1 set-device-pin)
    ./deviceui.ps1 unlock -SecretRef devicepin -Serial <serial>             # or name the secret explicitly; verifies + stops after -MaxAttempts (3)
    ./deviceui.ps1 key -Text ENTER
    ./deviceui.ps1 finger-status                  # is a fingerprint enrolled?
    ./deviceui.ps1 finger-enroll                  # enroll one on an emulator (or prompt if a real device)
    ./deviceui.ps1 finger -Text 1                 # simulate a touch of enrolled fingerprint id 1
    ./deviceui.ps1 screenshot -Out C:\runs\step1.png
    ./deviceui.ps1 current-app              # resolved/focused package + activity

.NOTES
    Text matching is case-insensitive substring by default. Use -Exact for equality.
    Speed (see references/run-speed.md): prefer `wait-text` / `tap-text -Then <anchor>` over a fixed
    Start-Sleep — they return the instant the screen is ready. `-Then` taps and then polls for the next
    screen's anchor in the SAME process, so a navigate+verify is one tool call, not two. Polling cadence is
    -PollMs (default 600ms); the first check is immediate. Re-`dump` only when you actually need to read new
    on-screen state — verify a navigation by its anchor instead of a reflexive re-dump after every tap.
    input-text options: -Clear empties the focused field first (one adb round-trip); -CharByChar types one
    character at a time (defeats Chrome autofill/passkey overlays that swallow a bulk `input text`);
    -Secret suppresses echoing the value to the transcript (prints length only) — always use it for passwords.
    -SecretRef <name> resolves a password/PIN from the encrypted store (scripts/secrets.ps1) and types it
    without ever printing it (implies -Secret) — prefer this over -Text for anything sensitive so the value
    never appears in the chat. `unlock -SecretRef <name>` enters a device lock-screen PIN the same way; it
    VERIFIES the keyguard actually cleared and STOPS after -MaxAttempts wrong tries (default 3) so a wrong
    PIN can't drive a physical device into an escalating Gatekeeper lockout (exit code 3 if it gives up).
    If you omit -SecretRef, `unlock -Serial <serial>` auto-resolves the per-device secret devicepin_<serial>
    saved by `secrets.ps1 set-device-pin` -- so you never type the serial's PIN name twice.
    See references/secrets-and-files.md for how the human seeds secrets and drops APKs/files for a run.
    Target a specific device with -Serial (each device — even the same model — has a unique adb serial;
    `adb devices -l` lists them). Omitting -Serial while several devices are attached makes adb error out
    rather than act on the wrong one.
    Try bulk `input-text` first (fast); only add -CharByChar if verification shows the value didn't land.
    Fingerprint: `emu finger` only works on EMULATORS. On a real device a fingerprint must be enrolled
    by the user against the physical sensor — `finger-enroll` will print the steps and ask. When a step
    needs an injectable fingerprint/biometric (App Lock, biometric-gated number-match), prefer an emulator.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('dump', 'find-text', 'tap-text', 'tap-desc', 'input-text', 'wait-text',
        'screenshot', 'key', 'finger', 'finger-status', 'finger-enroll', 'current-app', 'tap-xy', 'unlock')]
    [string]$Command = 'dump',

    [string]$Serial,
    [string]$Text,
    [string]$SecretRef,
    [string]$Then,
    [int]$X,
    [int]$Y,
    [int]$Index = 0,
    [switch]$Exact,
    [int]$TimeoutSec = 20,
    [int]$PollMs = 600,
    [string]$Pin = '1234',
    [string]$Out,
    [switch]$Clear,
    [switch]$CharByChar,
    [int]$PerCharDelayMs = 60,
    [int]$MaxAttempts = 3,
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

function Wait-ForText {
    # Poll for text/content-desc until found or the deadline passes. Checks immediately, then every
    # $PollMilliseconds. Returns $true as soon as it appears — the point is to return the instant the
    # screen is ready instead of paying a fixed Start-Sleep. See references/run-speed.md.
    param([string]$Query, [int]$TimeoutSeconds, [int]$PollMilliseconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ($true) {
        $m = Find-ByField 'Text' $Query
        if (-not $m) { $m = Find-ByField 'Desc' $Query }
        if ($m) { return $true }
        if ((Get-Date) -ge $deadline) { return $false }
        Start-Sleep -Milliseconds $PollMilliseconds
    }
}

function Encode-Input {
    param([string]$s)
    # adb 'input text' uses %s for space; backslash-escape shell metacharacters.
    $s = $s -replace '([()<>|;&*~"''`$\\])', '\$1'
    $s = $s -replace ' ', '%s'
    return $s
}

function Resolve-SecretValue {
    # Resolve a named secret WITHOUT ever printing it, so passwords / device PINs stay out of the
    # transcript. Mirrors scripts/secrets.ps1: env var E2E_SECRET_<NAME> first, then the DPAPI file
    # %USERPROFILE%\.android-e2e-secrets\<name>.sec. Returns plaintext for on-device typing only.
    param([Parameter(Mandatory)][string]$Name)
    $envName = 'E2E_SECRET_' + ($Name.ToUpper() -replace '[^A-Z0-9_]', '_')
    $envVal = [Environment]::GetEnvironmentVariable($envName)
    if ($envVal) { return $envVal }
    $p = Join-Path (Join-Path $env:USERPROFILE '.android-e2e-secrets') ("{0}.sec" -f $Name)
    if (-not (Test-Path $p)) {
        throw "No secret named '$Name' (env $envName unset and $p missing). Add it with: scripts/secrets.ps1 set -Name $Name"
    }
    $ss = ConvertTo-SecureString (Get-Content -Raw -Path $p)   # DPAPI decrypt (per-user/machine)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Get-DevicePinName {
    # Per-device secret-name convention shared with scripts/secrets.ps1 (set-device-pin): 'devicepin_' + serial
    # with non-alphanumerics -> '_'. Lets `unlock -Serial <s>` find the PIN saved for that exact device.
    param([Parameter(Mandatory)][string]$Serial)
    return ('devicepin_' + ($Serial -replace '[^A-Za-z0-9]', '_'))
}

function Test-SecretExists {
    # True if a named secret resolves (env override or DPAPI file), WITHOUT decrypting/printing it.
    param([Parameter(Mandatory)][string]$Name)
    $envName = 'E2E_SECRET_' + ($Name.ToUpper() -replace '[^A-Z0-9_]', '_')
    if ([Environment]::GetEnvironmentVariable($envName)) { return $true }
    return (Test-Path (Join-Path (Join-Path $env:USERPROFILE '.android-e2e-secrets') ("{0}.sec" -f $Name)))
}

function Test-IsEmulator {
    # An explicit emulator-XXXX serial is an emulator; otherwise probe the running device.
    if ($Serial) { return ($Serial -match '^emulator-') }
    $qemu = (Adb shell getprop ro.kernel.qemu 2>$null | Out-String).Trim()
    if ($qemu -eq '1') { return $true }
    $chars = (Adb shell getprop ro.build.characteristics 2>$null | Out-String).Trim()
    return ($chars -match 'emulator')
}

function Test-KeyguardLocked {
    # Read-only. Returns $true (keyguard up), $false (unlocked), or 'unknown'. The exact dumpsys flags vary
    # by Android version, so we combine several signals. Used by 'unlock' to STOP as soon as the device is
    # unlocked and to never over-attempt a PIN (a wrong PIN counts toward the Gatekeeper lockout throttle).
    $w = (Adb shell dumpsys window 2>$null | Out-String)
    if ($w) {
        if ($w -match 'mDreamingLockscreen=true' -or $w -match 'mShowingLockscreen=true' -or
            $w -match 'mKeyguardShowing=true' -or $w -match 'KeyguardShowing=true') { return $true }
        if ($w -match 'mDreamingLockscreen=false' -or $w -match 'mShowingLockscreen=false') { return $false }
        $m = [regex]::Match($w, 'mCurrentFocus=Window\{[^}]*\}')
        if ($m.Success) {
            if ($m.Value -match 'Keyguard|NotificationShade|StatusBar|DreamOverlay') { return $true }
            if ($m.Value -match '/') { return $false }   # an app/launcher window is focused => unlocked
        }
    }
    # Fallback: KeyguardController block in the activity dump (API 28+).
    $a = (Adb shell dumpsys activity activities 2>$null | Out-String)
    if ($a -match 'mKeyguardShowing=true') { return $true }
    if ($a -match 'mKeyguardShowing=false') { return $false }
    return 'unknown'
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
        if ($Then) {
            # Tap + verify the next screen in ONE process — no extra tool call, no fixed sleep.
            if (Wait-ForText $Then $TimeoutSec $PollMs) { Write-Host "FOUND: '$Then'" }
            else { Write-Host "TIMEOUT waiting for '$Then' (${TimeoutSec}s)"; exit 4 }
        }
    }
    'tap-desc' {
        $m = @(Find-ByField 'Desc' $Text)
        if (-not $m) { Write-Host "NOT FOUND (content-desc): '$Text'"; exit 2 }
        $t = $m[[Math]::Min($Index, $m.Count - 1)]
        Adb shell input tap $t.Cx $t.Cy | Out-Null
        Write-Host "Tapped desc '$Text' at ($($t.Cx),$($t.Cy))"
        if ($Then) {
            if (Wait-ForText $Then $TimeoutSec $PollMs) { Write-Host "FOUND: '$Then'" }
            else { Write-Host "TIMEOUT waiting for '$Then' (${TimeoutSec}s)"; exit 4 }
        }
    }
    'tap-xy' {
        Adb shell input tap $X $Y | Out-Null
        Write-Host "Tapped ($X,$Y)"
    }
    'unlock' {
        # Wake the screen, reveal the keyguard PIN pad, enter the PIN, and VERIFY. A wrong PIN counts toward
        # the device's Gatekeeper lockout throttle, so we check keyguard state after each attempt and STOP as
        # soon as the device is unlocked. Total tries are capped at -MaxAttempts (default 3) so a wrong stored
        # PIN can never drive a physical device into an escalating lockout. Prefer -SecretRef (never printed);
        # if it's omitted we auto-resolve the per-device secret devicepin_<serial> (see secrets.ps1
        # set-device-pin); -Pin is a last resort for a throwaway emulator PIN. See references/common-blockers.md.
        if ($SecretRef) {
            $pinVal = Resolve-SecretValue $SecretRef
        }
        elseif (-not $PSBoundParameters.ContainsKey('Pin') -and $Serial) {
            $autoName = Get-DevicePinName $Serial
            if (Test-SecretExists $autoName) {
                $pinVal = Resolve-SecretValue $autoName
                Write-Host ("Using stored PIN for device {0} (secret '{1}')." -f $Serial, $autoName)
            }
            else {
                throw "No PIN stored for device '$Serial' (looked for secret '$autoName') and no -SecretRef/-Pin given. Save one with: scripts/secrets.ps1 set-device-pin -Serial $Serial"
            }
        }
        else {
            $pinVal = $Pin
        }
        if ((Test-KeyguardLocked) -eq $false) { Write-Host 'Device already unlocked; nothing to do.'; $pinVal = $null; break }
        $ok = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
            Adb shell input keyevent 224 | Out-Null                 # KEYCODE_WAKEUP
            Start-Sleep -Milliseconds 300
            Adb shell input swipe 540 1600 540 500 150 | Out-Null   # swipe up to reveal the PIN pad
            Start-Sleep -Milliseconds 400
            Adb shell input text (Encode-Input $pinVal) | Out-Null
            Adb shell input keyevent 66 | Out-Null                  # ENTER / confirm
            Start-Sleep -Milliseconds 800                            # let the keyguard settle before checking
            $state = Test-KeyguardLocked
            if ($state -eq $false) {
                $ok = $true
                Write-Host ("Unlocked on attempt {0}/{1} with a {2}-digit PIN." -f $attempt, $MaxAttempts, $pinVal.Length)
                break
            }
            $label = if ($state -eq $true) { 'still locked' } else { 'could not confirm' }
            Write-Host ("Attempt {0}/{1} did not unlock ({2})." -f $attempt, $MaxAttempts, $label)
        }
        $pinVal = $null
        if (-not $ok) {
            $ref = if ($SecretRef) { $SecretRef } else { '<pin>' }
            Write-Host ("STOP: device still locked after {0} attempt(s). Not retrying, to avoid the Gatekeeper lockout throttle. Verify the stored PIN with: secrets.ps1 get-masked -Name {1}  (or ask the user for the correct PIN)." -f $MaxAttempts, $ref)
            exit 3
        }
    }
    'input-text' {
        # -SecretRef resolves a stored secret (see scripts/secrets.ps1) and types it WITHOUT ever
        # echoing it -- use it for passwords instead of putting the value in -Text (which would land
        # in the chat). It implies -Secret (masked output).
        if ($SecretRef) { $Text = Resolve-SecretValue $SecretRef; $Secret = $true }
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
        if (Wait-ForText $Text $TimeoutSec $PollMs) { Write-Host "FOUND: '$Text'"; exit 0 }
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
