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
    set        Prompt (hidden) for a value and store it encrypted. Run this yourself, not via chat.
    list       List stored secret NAMES only (never values), plus any E2E_SECRET_* env overrides.
    test       Print only whether a name resolves and its length (e.g. "resolves=yes length=18").
    get-masked Print the name with the value masked (asterisks) and its length -- for a quick check.
    remove     Delete a stored secret.
    path       Print the store directory.

.EXAMPLE
    # Do this once, in your terminal (NOT through the chat):
    ./secrets.ps1 set -Name labpw
    # ...then just tell the agent: "the lab password is in secret 'labpw'".

.EXAMPLE
    ./secrets.ps1 set  -Name devicepin     # a real device's lock-screen PIN
    ./secrets.ps1 list                     # names only
    ./secrets.ps1 test -Name labpw         # -> resolves=yes length=18
    ./secrets.ps1 remove -Name labpw

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
    [ValidateSet('list', 'set', 'test', 'get-masked', 'remove', 'path')]
    [string]$Command = 'list',
    [string]$Name
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
