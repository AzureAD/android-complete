# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Read and drive an Android device/emulator UI via adb + uiautomator so the agent can
    perform AI-handleable inputs automatically (tap buttons, type credentials, grant
    permission dialogs, simulate a fingerprint, read on-screen text, capture screenshots).

.PARAMETER Command
    dump | find-text | tap-text | tap-desc | input-text | wait-text | screenshot |
    key | finger | current-app | tap-xy

.EXAMPLE
    ./deviceui.ps1 wait-text -Text "Sign in" -TimeoutSec 30
    ./deviceui.ps1 tap-text -Text "Sign in"
    ./deviceui.ps1 input-text -Text "user@contoso.com"
    ./deviceui.ps1 key -Text ENTER
    ./deviceui.ps1 finger -Text 1           # simulate enrolled fingerprint id 1
    ./deviceui.ps1 screenshot -Out C:\runs\step1.png
    ./deviceui.ps1 current-app              # resolved/focused package + activity

.NOTES
    Text matching is case-insensitive substring by default. Use -Exact for equality.
    Always re-`dump` after an action; the UI tree changes between steps.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('dump', 'find-text', 'tap-text', 'tap-desc', 'input-text', 'wait-text',
        'screenshot', 'key', 'finger', 'current-app', 'tap-xy')]
    [string]$Command = 'dump',

    [string]$Serial,
    [string]$Text,
    [int]$X,
    [int]$Y,
    [int]$Index = 0,
    [switch]$Exact,
    [int]$TimeoutSec = 20,
    [string]$Out
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
        $enc = Encode-Input $Text
        Adb shell input text $enc | Out-Null
        Write-Host "Typed: $Text"
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
        $ser = if ($Serial) { $Serial } else { '' }
        if ($ser) { & $adb -s $ser emu finger touch $id | Out-Null } else { & $adb -e emu finger touch $id | Out-Null }
        Write-Host "Simulated fingerprint id=$id"
    }
    'current-app' {
        $win = (Adb shell dumpsys window 2>$null | Out-String)
        $focus = ($win -split "`n" | Where-Object { $_ -match 'mCurrentFocus|mFocusedApp' }) -join "`n"
        Write-Host $focus
    }
}
