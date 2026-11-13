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

.PARAMETER Wait
    (Check mode) Seconds to block-poll for a valid token before giving up. When > 0 and the
    token isn't yet valid, the script re-checks every -IntervalSec seconds and returns `ok` the
    instant the engineer finishes -Setup in their own terminal — so the skill auto-resumes with
    no "done, re-check" handshake. Host-agnostic (app / VS Code / CLI). Default 0 (single shot).

.PARAMETER IntervalSec
    (Check mode) Poll interval in seconds while -Wait is active. Default 10 (min 2).

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
    [switch]$Force,
    [int]$Wait = 0,
    [int]$IntervalSec = 10
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

# Resolve + validate once, returning a {status; source} object WITHOUT printing or exiting.
# Used by both the single-shot check and the -Wait poll loop.
function Get-Verdict {
    $r = Resolve-Token -ExplicitFile $TokenFile
    if (-not $r.Token) { return [pscustomobject]@{ Status = 'missing'; Source = $r.Source } }
    $verdict = Test-Token -Token $r.Token
    return [pscustomobject]@{ Status = $verdict; Source = $r.Source }
}

$StatusMessages = @{
    'ok'        = 'Token OK — crash layer can pull.'
    'missing'   = "No App Center token found. Run once in your own terminal:`n  pwsh -File `"$PSCommandPath`" -Setup"
    'invalid'   = "Token is expired or revoked (401). Refresh it:`n  pwsh -File `"$PSCommandPath`" -Setup"
    'no-access' = "Token is valid but not authorized for $Owner/$App (403/404). Generate a read-only token in the right org, then:`n  pwsh -File `"$PSCommandPath`" -Setup"
    'network'   = 'Could not reach App Center (offline/proxy). Retry later.'
}

# ---- CHECK MODE (default) --------------------------------------------------
# With -Wait <sec>, block-poll until the token becomes valid (or timeout) so the
# skill auto-resumes the instant the engineer finishes -Setup in their own
# terminal — no "done, re-check" handshake. Host-agnostic: works in the desktop
# app, VS Code Copilot Chat, and Copilot CLI alike.
function Invoke-Check {
    $v = Get-Verdict

    if ($v.Status -ne 'ok' -and $Wait -gt 0) {
        $deadline = (Get-Date).AddSeconds($Wait)
        $interval = [Math]::Max(2, $IntervalSec)
        Write-Host "Waiting up to ${Wait}s for a valid App Center token (polling every ${interval}s)..."
        Write-Host "Finish setup in your terminal:  pwsh -File `"$PSCommandPath`" -Setup"
        while ($v.Status -ne 'ok' -and (Get-Date) -lt $deadline) {
            Start-Sleep -Seconds $interval
            $v = Get-Verdict
        }
        if ($v.Status -eq 'ok') { Write-Host 'Detected a valid token — resuming automatically.' }
    }

    $msg = $StatusMessages[$v.Status]
    if (-not $msg) { $msg = "Status: $($v.Status)" }
    if ($v.Status -eq 'ok') { $msg = "Token OK (source $($v.Source)) — crash layer can pull." }
    Write-Status -Status $v.Status -Source $v.Source -Message $msg
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
    Write-Host '2. Click "New API token", choose scope "Read-only", and copy the value.'
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
