# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Call the MSID LAB user-manager API (https://labusermanagerapi.azurewebsites.net) to provision and
    manage lab test accounts for E2E runs. The API is EasyAuth-protected (Azure App Service
    Authentication), so a service token from `az account get-access-token` is rejected with
    consent_required. This script authenticates the way the LAB "URL generator" web app does: it opens
    the endpoint in **headless Edge**, which reuses your existing Entra WAM SSO session, then parses the
    JSON the endpoint returns. If Edge's `--dump-dom` returns 0 bytes (a known Edge 150+ headless
    regression), it automatically falls back to the **DevTools protocol (CDP)** over a WebSocket to read
    the response, so provisioning keeps working across Edge versions.

.DESCRIPTION
    Commands (function endpoints return JSON and are parsed automatically):
      create-user     -UserType <type>                 CreateTempUserID4SLab2   (temp user, auto-deletes in 60 min)
      reset           -Upn <upn> -Operation mfa|password ResetID4SLab2          (password reset = temp users only)
      enable-policy   -Upn <upn> -Policy <policy>        EnablePolicyID4SLab2
      disable-policy  -Upn <upn> -Policy <policy>        DisablePolicyID4SLab2
      delete-device   -Upn <upn> -DeviceId <id>          DeleteDeviceID4SLab2
      open            -Url <url>                          launch an interactive browser (for KeyVault deep-links:
                                                          "List of Test Accounts" / "Fetch Password for Tenant")

    UserType : Basic | GlobalMFA | MAMCA | MDMCA | MFAONSPO | MFAONEXO | FIDOBasic | FIDOMDM | AuthappLBAC | AuthappRichContext
    Policy   : GlobalMFA | MAMCA | MDMCA | MFAONSPO | MFAONEXO | AuthappLBAC | AuthappRichContext
    Operation: mfa | password

    Auth/entitlements: you must be signed into Edge with an account that holds TM-MSIDLabs-Ext (RO+RW)
    and, for KeyVault deep-links, TM-MSIDLABS-DevKV (RO+RW). Manage at
    https://coreidentity.microsoft.com/manage/entitlement . See references/lab-api.md.

.EXAMPLE
    ./labapi.ps1 create-user -UserType GlobalMFA
    ./labapi.ps1 reset -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -Operation mfa
    ./labapi.ps1 disable-policy -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -Policy GlobalMFA

.NOTES
    - Never print the fetched password into the transcript. `create-user` returns the UPN; the password
      for temp users is the shared lab password the user supplies for the run.
    - The first call in a session may pop a silent WAM prompt; subsequent calls reuse the cached profile
      (a stable, isolated user-data-dir) so they're fast. Use -Fresh to force a throwaway profile.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('create-user', 'reset', 'enable-policy', 'disable-policy', 'delete-device', 'open')]
    [string]$Command = 'create-user',

    [string]$UserType = 'GlobalMFA',
    [string]$Upn,
    [ValidateSet('mfa', 'password')]
    [string]$Operation,
    [string]$Policy,
    [string]$DeviceId,
    [string]$Url,
    [int]$TimeoutSec = 30,
    [switch]$Fresh,
    [switch]$Raw
)

$ErrorActionPreference = 'Stop'
$ApiBase = 'https://labusermanagerapi.azurewebsites.net/api'

function Get-Edge {
    foreach ($p in @(
            "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
            "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe")) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    $cmd = Get-Command msedge -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "Microsoft Edge not found. Install Edge, or use 'open' and complete the call in your own browser."
}

function Get-JsonPayload {
    # Pull the first JSON object/array out of either a DOM string (-IsHtml) or plain innerText.
    param([string]$Content, [switch]$IsHtml)
    if ([string]::IsNullOrWhiteSpace($Content)) { return $null }
    $payload = $null
    if ($IsHtml) {
        # Prefer a <pre> block (text/plain and application/json render inside <pre>).
        $m = [regex]::Match($Content, '<pre[^>]*>(.*?)</pre>', 'Singleline, IgnoreCase')
        if ($m.Success) { $payload = [System.Net.WebUtility]::HtmlDecode(($m.Groups[1].Value -replace '<[^>]+>', '')) }
        if (-not $payload) {
            $text = [System.Net.WebUtility]::HtmlDecode(([regex]::Replace($Content, '<[^>]+>', ' ')))
            $jm = [regex]::Match($text, '(\{.*\}|\[.*\])', 'Singleline')
            if ($jm.Success) { $payload = $jm.Value }
        }
    }
    else {
        # Plain text (e.g. CDP document.body.innerText): grab the JSON object/array directly.
        $jm = [regex]::Match($Content, '(\{.*\}|\[.*\])', 'Singleline')
        if ($jm.Success) { $payload = $jm.Value }
    }
    return $payload
}

function Invoke-CdpEval {
    # Evaluate a JS expression in a page via a one-shot DevTools (CDP) WebSocket; returns the string value.
    # Works on Windows PowerShell 5.1+ and PowerShell 7 (System.Net.WebSockets.ClientWebSocket).
    param([string]$WsUrl, [string]$Expr)
    $ws = New-Object System.Net.WebSockets.ClientWebSocket
    $ct = [System.Threading.CancellationToken]::None
    try {
        $ws.ConnectAsync([Uri]$WsUrl, $ct).Wait()
        $req = (@{ id = 1; method = 'Runtime.evaluate'; params = @{ expression = $Expr; returnByValue = $true } } | ConvertTo-Json -Compress)
        $buf = [System.Text.Encoding]::UTF8.GetBytes($req)
        $ws.SendAsync([System.ArraySegment[byte]]::new($buf), [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $ct).Wait()
        $rbuf = New-Object byte[] 131072
        $sb = New-Object System.Text.StringBuilder
        do {
            $r = $ws.ReceiveAsync([System.ArraySegment[byte]]::new($rbuf), $ct); $r.Wait()
            [void]$sb.Append([System.Text.Encoding]::UTF8.GetString($rbuf, 0, $r.Result.Count))
        } while (-not $r.Result.EndOfMessage)
        $reply = $sb.ToString() | ConvertFrom-Json
        return [string]$reply.result.result.value
    }
    finally { try { $ws.Dispose() } catch {} }
}

function Invoke-LabApiCdp {
    # Fallback when Edge `--dump-dom` returns 0 bytes (a known Edge 150+ headless regression): drive
    # headless Edge over the DevTools protocol and read document.body.innerText. A dedicated profile
    # still gets Entra SSO via the OS WAM broker, so this stays non-interactive after the first consent.
    param([string]$Endpoint)
    $edge = Get-Edge
    $prof = if ($Fresh) { Join-Path $env:TEMP ("labapi_cdp_" + [guid]::NewGuid().ToString('N')) }
    else { Join-Path $env:TEMP 'labapi_edge_cdp_profile' }
    $port = Get-Random -Minimum 9300 -Maximum 9599
    $proc = Start-Process -FilePath $edge -PassThru -WindowStyle Hidden -ArgumentList @(
        '--headless=new', '--disable-gpu', '--disable-sync',
        "--remote-debugging-port=$port", "--user-data-dir=$prof",
        '--no-first-run', '--no-default-browser-check', $Endpoint)
    try {
        $deadline = (Get-Date).AddSeconds([Math]::Max(12, $TimeoutSec))
        $wsUrl = $null
        while ((Get-Date) -lt $deadline -and -not $wsUrl) {
            Start-Sleep -Milliseconds 400
            try { $targets = Invoke-RestMethod "http://127.0.0.1:$port/json/list" -TimeoutSec 3 } catch { continue }
            # Only the real content page (http/https); skip edge:// dialogs (e.g. sync-confirmation).
            $page = $targets | Where-Object { $_.type -eq 'page' -and $_.url -like 'http*' } | Select-Object -First 1
            if ($page) { $wsUrl = $page.webSocketDebuggerUrl }
        }
        if (-not $wsUrl) { return '' }
        $text = ''
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 500
            try { $text = Invoke-CdpEval -WsUrl $wsUrl -Expr 'document.body.innerText' } catch { $text = '' }
            if ($text -match '[\{\[]') { break }                                  # JSON payload arrived
            if ($text -match 'AADSTS|Sign in|Pick an account|Enter password') { break }  # stuck on interactive auth
        }
        return $text
    }
    finally {
        try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
        if ($Fresh) { Remove-Item $prof -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Invoke-LabApi {
    param([string]$Endpoint)
    $edge = Get-Edge
    # Stable, isolated profile caches WAM SSO across calls (fast). -Fresh forces a throwaway dir.
    $profile = if ($Fresh) { Join-Path $env:TEMP ("labapi_edge_" + [guid]::NewGuid().ToString('N')) }
    else { Join-Path $env:TEMP 'labapi_edge_profile' }
    $dom = Join-Path $env:TEMP ("labapi_dom_" + [guid]::NewGuid().ToString('N') + ".html")
    $budget = [Math]::Max(5000, $TimeoutSec * 1000)
    try {
        & $edge --headless=new --disable-gpu --user-data-dir=$profile --no-first-run --no-default-browser-check `
            --dump-dom --virtual-time-budget=$budget $Endpoint 2>$null |
            Out-File -FilePath $dom -Encoding utf8
        $html = if (Test-Path $dom) { Get-Content $dom -Raw } else { '' }
    }
    finally {
        Remove-Item $dom -ErrorAction SilentlyContinue
        if ($Fresh) { Remove-Item $profile -Recurse -Force -ErrorAction SilentlyContinue }
    }

    $payload = Get-JsonPayload -Content $html -IsHtml
    $probe = $html
    # Edge 150+ headless `--dump-dom` can return 0 bytes. If the DOM came back empty (or yielded no
    # JSON), retry over the DevTools protocol, which is unaffected by that regression.
    if (-not $payload) {
        $cdpText = Invoke-LabApiCdp -Endpoint $Endpoint
        if (-not [string]::IsNullOrWhiteSpace($cdpText)) {
            $probe = $cdpText
            $payload = Get-JsonPayload -Content $cdpText
        }
    }

    if ($Raw) { return [pscustomobject]@{ Raw = $probe; Json = $null } }
    if (-not $payload) {
        if ($probe -match 'AADSTS|Sign in|login\.microsoftonline|consent') {
            throw "LAB API call did not return data - Edge appears to need an interactive sign-in/consent. Run '.\labapi.ps1 open -Url `"$Endpoint`"' once in a visible browser to establish the session, then retry."
        }
        throw "Could not extract a JSON payload from the LAB API response (dump-dom was empty and the CDP fallback found no JSON). Re-run with -Raw to inspect, or use '.\labapi.ps1 open -Url `"$Endpoint`"'."
    }
    $json = $null
    try { $json = $payload | ConvertFrom-Json } catch { }
    return [pscustomobject]@{ Raw = $payload; Json = $json }
}

function Show-Result {
    param($Result)
    if ($Result.Json) {
        $Result.Json | ConvertTo-Json -Depth 8
    }
    else {
        Write-Host $Result.Raw
    }
}

switch ($Command) {
    'create-user' {
        $ep = "$ApiBase/CreateTempUserID4SLab2?usertype=$([uri]::EscapeDataString($UserType))"
        Write-Host "Creating temp $UserType user (auto-deletes in ~60 min)..."
        $r = Invoke-LabApi $ep
        Show-Result $r
        # Surface the UPN prominently for the caller to reuse.
        $upn = $null
        if ($r.Json) {
            foreach ($p in 'upn', 'Upn', 'UPN', 'userPrincipalName', 'username', 'User') {
                if ($r.Json.PSObject.Properties.Name -contains $p -and $r.Json.$p) { $upn = $r.Json.$p; break }
            }
        }
        if (-not $upn) { $m = [regex]::Match($r.Raw, '[\w.\-+]+@[\w.\-]+onmicrosoft\.com'); if ($m.Success) { $upn = $m.Value } }
        if ($upn) { Write-Host "UPN=$upn" }
    }
    'reset' {
        if (-not $Upn) { throw "-Upn is required." }
        if (-not $Operation) { throw "-Operation mfa|password is required." }
        $ep = "$ApiBase/ResetID4SLab2?upn=$([uri]::EscapeDataString($Upn))&operation=$([uri]::EscapeDataString($Operation))"
        Write-Host "Resetting '$Operation' for $Upn ..."
        Show-Result (Invoke-LabApi $ep)
    }
    'enable-policy' {
        if (-not $Upn) { throw "-Upn is required." }
        if (-not $Policy) { throw "-Policy is required (e.g. GlobalMFA)." }
        $ep = "$ApiBase/EnablePolicyID4SLab2?upn=$([uri]::EscapeDataString($Upn))&policy=$([uri]::EscapeDataString($Policy))"
        Write-Host "Enabling policy '$Policy' for $Upn ..."
        Show-Result (Invoke-LabApi $ep)
    }
    'disable-policy' {
        if (-not $Upn) { throw "-Upn is required." }
        if (-not $Policy) { throw "-Policy is required (e.g. GlobalMFA)." }
        $ep = "$ApiBase/DisablePolicyID4SLab2?upn=$([uri]::EscapeDataString($Upn))&policy=$([uri]::EscapeDataString($Policy))"
        Write-Host "Disabling policy '$Policy' for $Upn ..."
        Show-Result (Invoke-LabApi $ep)
    }
    'delete-device' {
        if (-not $Upn) { throw "-Upn is required." }
        if (-not $DeviceId) { throw "-DeviceId is required." }
        $ep = "$ApiBase/DeleteDeviceID4SLab2?upn=$([uri]::EscapeDataString($Upn))&deviceid=$([uri]::EscapeDataString($DeviceId))"
        Write-Host "Deleting device $DeviceId for $Upn ..."
        Show-Result (Invoke-LabApi $ep)
    }
    'open' {
        if (-not $Url) { throw "-Url is required (e.g. a List-of-Test-Accounts or Fetch-Password KeyVault deep-link)." }
        $edge = Get-Edge
        # Interactive (non-headless) so KeyVault/Azure Portal deep-links can render for the user.
        & $edge $Url | Out-Null
        Write-Host "Opened in Edge: $Url"
    }
}
