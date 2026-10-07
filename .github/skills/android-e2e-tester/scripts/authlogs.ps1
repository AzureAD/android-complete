# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Capture and analyze logcat to decide whether an E2E scenario passed, and to surface
    root-cause evidence (crashes, exceptions, error codes, eSTS correlation IDs) when it fails.

.PARAMETER Command
    clear | snapshot | scan | grep | watch

.DESCRIPTION
    clear    -> flush the logcat buffer right before running the scenario.
    snapshot -> dump the current buffer to a file (raw + auth-filtered view).
    scan     -> classify PASS / FAIL / INCONCLUSIVE from success & failure signals and
                extract evidence (crash stacks, AADSTS codes, correlation IDs, error codes).
    grep     -> search the buffer/file for a custom -Pattern.
    watch    -> stream live for -TimeoutSec, teeing to a file (long scenarios).

.EXAMPLE
    ./authlogs.ps1 clear
    # ... run the scenario ...
    ./authlogs.ps1 scan -Package com.microsoft.brokerhost -Out C:\runs\r1\logcat.txt

.NOTES
    The verdict is a heuristic. Always read the extracted evidence and reason about it;
    do not blindly trust PASS/FAIL. See references/log-signals.md.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('clear', 'snapshot', 'scan', 'grep', 'watch')]
    [string]$Command = 'scan',

    [string]$Serial,
    [string]$Package,
    [string]$Pattern,
    [string]$Out,
    [int]$TimeoutSec = 60,
    [int]$Context = 2
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

$adb = Get-Adb
function Adb {
    if ($Serial) { & $adb -s $Serial @args } else { & $adb @args }
}

# Keywords that identify auth-relevant log lines across MSAL / Common / Broker.
$AuthKeywords = 'MSAL|Broker|Common|ADAL|OneAuth|AcquireToken|PRT|eSTS|ESTS|correlation|AADSTS|TokenResult|SilentToken|InteractiveToken|WorkplaceJoin|SSO|Authenticator|CompanyPortal|identity'

# Failure signals (regex, case-sensitive where it matters for tags like "E ").
$FailSignals = @(
    'FATAL EXCEPTION', 'E AndroidRuntime', 'AndroidRuntime:.*Exception',
    'ANR in ', 'Force finishing', 'process .* died',
    'AADSTS\d+', 'error_code[=:\s]', 'errorCode[=:\s]', 'invalid_grant',
    'INTERACTION_REQUIRED', 'UNKNOWN_ERROR', 'No PRT present',
    'CertPathValidatorException', 'NullPointerException', 'NoSuchMethodError',
    'ClassNotFoundException', 'SecurityException', 'BrokerCommunicationException',
    'failed to acquire', 'Authentication failed', 'operation failed', ' FAILED'
)
# Success signals.
$PassSignals = @(
    'executed successfully', 'AcquireToken.*[Ss]uccess', 'Token.*acquired',
    'TokenResult.*SUCCESS', 'SILENT.*SUCCESS', 'Silent request.*success',
    'succeeded', 'SUCCEEDED', 'Saved.*token', 'Retrieved.*token from cache'
)

function Read-Buffer {
    if ($Out -and (Test-Path $Out)) { return Get-Content $Out }
    return (Adb logcat -d -v threadtime 2>$null)
}

function Ensure-Dir { param([string]$Path) $d = Split-Path $Path -Parent; if ($d -and -not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null } }

function Save-Snapshot {
    if (-not $Out) {
        $dir = Join-Path ([System.IO.Path]::GetTempPath()) 'android-e2e'
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $Out = Join-Path $dir ("logcat_{0}.txt" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
    }
    Ensure-Dir $Out
    $lines = Adb logcat -d -v threadtime 2>$null
    $lines | Set-Content -Path $Out -Encoding utf8
    return @{ Path = $Out; Lines = $lines }
}

switch ($Command) {
    'clear' {
        Adb logcat -c 2>$null | Out-Null
        Write-Host "logcat buffer cleared."
    }
    'snapshot' {
        $s = Save-Snapshot
        $auth = $s.Lines | Where-Object { $_ -match $AuthKeywords }
        $authPath = ($s.Path -replace '\.txt$', '') + '.auth.txt'
        $auth | Set-Content -Path $authPath -Encoding utf8
        Write-Host "Raw log:  $($s.Path)  ($($s.Lines.Count) lines)"
        Write-Host "Auth log: $authPath  ($($auth.Count) lines)"
    }
    'grep' {
        if (-not $Pattern) { throw "Provide -Pattern." }
        $lines = Read-Buffer
        $lines | Select-String -Pattern $Pattern -Context $Context, $Context | ForEach-Object { $_.ToString() } | Write-Host
    }
    'watch' {
        if (-not $Out) { $Out = Join-Path ([System.IO.Path]::GetTempPath()) ("e2e_watch_{0}.txt" -f (Get-Date -Format 'yyyyMMdd_HHmmss')) }
        Write-Host "Streaming logcat for ${TimeoutSec}s -> $Out (Ctrl-C to stop early)"
        $job = Start-Job -ScriptBlock { param($a, $s) if ($s) { & $a -s $s logcat -v threadtime } else { & $a logcat -v threadtime } } -ArgumentList $adb, $Serial
        Start-Sleep -Seconds $TimeoutSec
        Receive-Job $job | Set-Content -Path $Out -Encoding utf8
        Stop-Job $job -ErrorAction SilentlyContinue; Remove-Job $job -Force -ErrorAction SilentlyContinue
        Write-Host "Saved: $Out"
    }
    'scan' {
        $s = Save-Snapshot
        $lines = $s.Lines
        if ($Package) {
            $pid0 = (Adb shell pidof $Package 2>$null | Out-String).Trim()
            $filtered = $lines | Where-Object { $_ -match $AuthKeywords -or ($Package -and $_ -match [regex]::Escape($Package)) -or ($pid0 -and $_ -match "\s$pid0\s") }
        }
        else {
            $filtered = $lines | Where-Object { $_ -match $AuthKeywords }
        }

        $failRe = ($FailSignals -join '|')
        $passRe = ($PassSignals -join '|')
        # Scope pass/fail signals to auth-relevant / app-filtered lines to avoid unrelated
        # system-log noise (e.g. "Failed Usage reports"). Crashes stay global because
        # AndroidRuntime crash stacks are not auth-tagged.
        $failHits = @($filtered | Select-String -Pattern $failRe -CaseSensitive:$false)
        $passHits = @($filtered | Select-String -Pattern $passRe -CaseSensitive:$false)
        $crash = @($lines | Select-String -Pattern 'FATAL EXCEPTION|E AndroidRuntime' -CaseSensitive:$false)

        $joined = ($lines -join "`n")
        $aadsts = @([regex]::Matches($joined, 'AADSTS\d+') | ForEach-Object { $_.Value }) | Sort-Object -Unique
        $corr = @([regex]::Matches($joined, 'correlation[_ ]?id["\s:=]+([0-9a-fA-F-]{36})') | ForEach-Object { $_.Groups[1].Value }) | Sort-Object -Unique

        $verdict = 'INCONCLUSIVE'
        if ($crash.Count -gt 0) { $verdict = 'FAIL (crash)' }
        elseif ($failHits.Count -gt 0 -and $passHits.Count -eq 0) { $verdict = 'FAIL' }
        elseif ($passHits.Count -gt 0 -and $crash.Count -eq 0 -and $failHits.Count -eq 0) { $verdict = 'PASS' }
        elseif ($passHits.Count -gt 0 -and $failHits.Count -gt 0) { $verdict = 'MIXED (review evidence)' }

        Write-Host "==================== E2E LOG SCAN ===================="
        Write-Host "Verdict:        $verdict"
        Write-Host "Snapshot:       $($s.Path)"
        Write-Host "Pass signals:   $($passHits.Count)   Fail signals: $($failHits.Count)   Crashes: $($crash.Count)"
        if ($aadsts.Count -gt 0) { Write-Host "AADSTS codes:   $($aadsts -join ', ')" }
        if ($corr.Count -gt 0) { Write-Host "Correlation IDs: $($corr -join ', ')" }
        Write-Host ""
        if ($crash.Count -gt 0) {
            Write-Host "--- CRASH (first 25 lines) ---"
            $idx = [array]::IndexOf($lines, $crash[0].Line)
            if ($idx -ge 0) { ($lines[$idx..([Math]::Min($idx + 24, $lines.Count - 1))]) | Write-Host }
        }
        if ($failHits.Count -gt 0) {
            Write-Host "--- FAILURE EVIDENCE (top 15) ---"
            $failHits | Select-Object -First 15 | ForEach-Object { $_.Line } | Write-Host
        }
        if ($passHits.Count -gt 0) {
            Write-Host "--- SUCCESS EVIDENCE (top 8) ---"
            $passHits | Select-Object -First 8 | ForEach-Object { $_.Line } | Write-Host
        }
        Write-Host "======================================================"
        if ($verdict -like 'FAIL*') { exit 1 }
    }
}
