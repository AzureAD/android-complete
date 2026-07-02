<#
.SYNOPSIS
    App Center token preflight for the release-monitoring-report skill's crash layer.
    Two modes: -Check (non-interactive validate, machine-readable status) and
    -Setup (interactive first-time secure token capture).

.DESCRIPTION
    The crash layer (Step 3b) needs a read-only App Center API token. There is no
    interactive OAuth for App Center API tokens, so a token must be generated once in
    the web UI and cached locally. This helper removes the guesswork:

      -Check  (default) — resolves the token the same way fetch-appcenter-crashes.js does
              (--token-file path -> $APPCENTER_API_TOKEN -> ~/.android-release-reports/appcenter.token),
              then validates it with one cheap `GET /apps/{owner}/{app}` call. It NEVER
              prompts and NEVER prints the token. It emits a one-word STATUS line (and,
              with -Json, a compact JSON object) plus a distinct exit code so the skill
              can branch:

                  STATUS  exit  meaning
                  ok        0   token present and authorized for the app  -> pull crashes
                  missing   2   no token found in any of the 3 locations  -> run -Setup
                  invalid   3   token present but 401 (expired / revoked)  -> run -Setup
                  no-access 4   token valid but 403/404 for this app/org   -> wrong scope/org
                  network   5   could not reach App Center (offline/proxy) -> retry later

      -Setup  — INTERACTIVE. Run this in YOUR OWN terminal (an AI agent cannot securely
              capture a pasted secret). Opens the App Center API-tokens page, reads the
              token with Read-Host -AsSecureString (never echoed), validates it, and only
              on success writes it to the token file with a user-only ACL. On any failure
              it writes nothing.

    The skill auto-runs -Check at the top of Step 3b. The engineer only ever runs -Setup
    once (or again when a token expires) — after that every run is hands-off.

.PARAMETER Check
    Non-interactive validation. This is the default when neither switch is given.

.PARAMETER Setup
    Interactive first-time secure token capture. Run in your own terminal.

.PARAMETER Owner
    App Center org (owner) name. Default: authapp-t7qc.

.PARAMETER App
    App Center app name. Default: Microsoft-Authenticator-Android-Prod-App-Center.

.PARAMETER TokenFile
    Explicit token-file path. Overrides the default cache location for both modes.
    Default: ~/.android-release-reports/appcenter.token.

.PARAMETER Json
    Emit a compact machine-readable JSON status object on the last stdout line
    (the skill parses this).

.PARAMETER Force
    In -Setup, re-capture even if a valid token already exists.

.EXAMPLE
    # what the skill runs automatically at Step 3b:
    pwsh -File preflight-appcenter.ps1 -Check -Json

.EXAMPLE
    # what the engineer runs once, in their own terminal, when Check says missing/invalid:
    pwsh -File preflight-appcenter.ps1 -Setup
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Setup,
    [string]$Owner = 'authapp-t7qc',
    [string]$App = 'Microsoft-Authenticator-Android-Prod-App-Center',
    [string]$TokenFile,
    [switch]$Json,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ApiBase = 'https://api.appcenter.ms/v0.1'
$TokenPageUrl = 'https://appcenter.ms/settings/apitokens'

function Get-DefaultTokenFile {
    Join-Path ([Environment]::GetFolderPath('UserProfile')) '.android-release-reports\appcenter.token'
}

# Resolve the token WITHOUT printing it. Mirrors fetch-appcenter-crashes.js resolveToken()
# order: explicit file -> $APPCENTER_API_TOKEN -> default cache file.
function Resolve-Token {
    param([string]$ExplicitFile)

    if ($ExplicitFile) {
        if (Test-Path -LiteralPath $ExplicitFile) {
            $t = (Get-Content -LiteralPath $ExplicitFile -Raw).Trim()
            if ($t) { return [pscustomobject]@{ Token = $t; Source = "file:$ExplicitFile" } }
        }
        return [pscustomobject]@{ Token = $null; Source = "file-missing:$ExplicitFile" }
    }

    if ($env:APPCENTER_API_TOKEN) {
        return [pscustomobject]@{ Token = $env:APPCENTER_API_TOKEN.Trim(); Source = 'env:APPCENTER_API_TOKEN' }
    }

    $def = Get-DefaultTokenFile
    if (Test-Path -LiteralPath $def) {
        $t = (Get-Content -LiteralPath $def -Raw).Trim()
        if ($t) { return [pscustomobject]@{ Token = $t; Source = "file:$def" } }
    }

    return [pscustomobject]@{ Token = $null; Source = 'none' }
}

# One cheap authorized call. Returns ok / invalid / no-access / network.
# Works on both Windows PowerShell 5.1 and PowerShell 7 exception shapes.
function Test-Token {
    param([string]$Token)

    $uri = "$ApiBase/apps/$Owner/$App"
    try {
        $null = Invoke-WebRequest -Uri $uri -Method Get -Headers @{ 'X-API-Token' = $Token } `
            -UseBasicParsing -TimeoutSec 30
        return 'ok'
    }
    catch {
        $code = $null
        $resp = $_.Exception.Response
        if ($resp) {
            try { $code = [int]$resp.StatusCode.value__ } catch { }
            if ($null -eq $code) { try { $code = [int]$resp.StatusCode } catch { } }
        }
        switch ($code) {
            401 { return 'invalid' }
            403 { return 'no-access' }
            404 { return 'no-access' }
            default { if ($code) { return 'no-access' } else { return 'network' } }
        }
    }
}

function Write-Status {
    param(
        [string]$Status,
        [string]$Source,
        [string]$Message
    )
    $exit = switch ($Status) {
        'ok'        { 0 }
        'missing'   { 2 }
        'invalid'   { 3 }
        'no-access' { 4 }
        'network'   { 5 }
        default     { 1 }
    }
    Write-Host "STATUS: $Status"
    if ($Message) { Write-Host $Message }
    if ($Json) {
        $obj = [ordered]@{ status = $Status; source = $Source; owner = $Owner; app = $App }
        Write-Output ($obj | ConvertTo-Json -Compress)
    }
    exit $exit
}

# ---- CHECK MODE (default) --------------------------------------------------
function Invoke-Check {
    $r = Resolve-Token -ExplicitFile $TokenFile
    if (-not $r.Token) {
        Write-Status -Status 'missing' -Source $r.Source `
            -Message "No App Center token found. Run once in your own terminal:`n  pwsh -File `"$PSCommandPath`" -Setup"
    }
    $verdict = Test-Token -Token $r.Token
    switch ($verdict) {
        'ok' {
            Write-Status -Status 'ok' -Source $r.Source -Message "Token OK (source $($r.Source)) — crash layer can pull."
        }
        'invalid' {
            Write-Status -Status 'invalid' -Source $r.Source `
                -Message "Token is expired or revoked (401). Refresh it:`n  pwsh -File `"$PSCommandPath`" -Setup"
        }
        'no-access' {
            Write-Status -Status 'no-access' -Source $r.Source `
                -Message "Token is valid but not authorized for $Owner/$App (403/404). Generate a token in the right org with read access, then:`n  pwsh -File `"$PSCommandPath`" -Setup"
        }
        default {
            Write-Status -Status 'network' -Source $r.Source `
                -Message 'Could not reach App Center (offline/proxy). Retry later.'
        }
    }
}

# ---- SETUP MODE (interactive, run in your own terminal) --------------------
function Invoke-Setup {
    if ([Environment]::UserInteractive -eq $false) {
        Write-Host 'STATUS: error'
        Write-Host '-Setup must be run interactively in your own terminal (an agent/CI shell cannot securely capture a pasted secret).'
        exit 1
    }

    $dest = if ($TokenFile) { $TokenFile } else { Get-DefaultTokenFile }

    if (-not $Force) {
        $existing = Resolve-Token -ExplicitFile $TokenFile
        if ($existing.Token -and (Test-Token -Token $existing.Token) -eq 'ok') {
            Write-Host "A valid App Center token already exists (source $($existing.Source))."
            Write-Host 'Re-run with -Force to overwrite it. Nothing changed.'
            exit 0
        }
    }

    Write-Host ''
    Write-Host 'App Center read-only token setup'
    Write-Host '--------------------------------'
    Write-Host "1. Opening the App Center API-tokens page: $TokenPageUrl"
    Write-Host '2. Click "New API token", choose scope "Read-only", copy the value.'
    Write-Host '3. Paste it below (input is hidden and never echoed).'
    Write-Host ''
    try { Start-Process $TokenPageUrl | Out-Null } catch { Write-Host "   (could not auto-open a browser — open $TokenPageUrl manually)" }

    $secure = Read-Host -AsSecureString 'Paste App Center read-only token'
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    $plain = ($plain | Out-String).Trim()

    if (-not $plain) {
        Write-Host 'STATUS: error'
        Write-Host 'No token entered — nothing saved.'
        exit 1
    }

    Write-Host 'Validating token against App Center...'
    $verdict = Test-Token -Token $plain
    if ($verdict -ne 'ok') {
        $plain = $null
        Write-Host "STATUS: $verdict"
        switch ($verdict) {
            'invalid'   { Write-Host 'That token was rejected (401). Generate a fresh read-only token and try again.' }
            'no-access' { Write-Host "That token is valid but not authorized for $Owner/$App. Use a token from the correct org. Nothing saved." }
            default     { Write-Host 'Could not reach App Center to validate. Nothing saved — retry later.' }
        }
        exit 1
    }

    $dir = Split-Path -Parent $dest
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    # Write with no trailing newline / no BOM.
    [System.IO.File]::WriteAllText($dest, $plain, (New-Object System.Text.UTF8Encoding($false)))
    $plain = $null

    # Best-effort lock down the file to the current user only (Windows).
    try {
        $acl = Get-Acl -LiteralPath $dest
        $acl.SetAccessRuleProtection($true, $false)
        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($me, 'FullControl', 'Allow')
        $acl.SetAccessRule($rule)
        Set-Acl -LiteralPath $dest -AclObject $acl
    }
    catch { Write-Host "   (note: could not tighten file ACL: $($_.Exception.Message))" }

    Write-Host 'STATUS: ok'
    Write-Host "Saved a validated read-only token to: $dest"
    Write-Host 'The crash layer will now run automatically on every report. Re-run -Setup if it ever expires.'
    exit 0
}

# ---- dispatch --------------------------------------------------------------
if ($Setup) { Invoke-Setup }
else { Invoke-Check }
