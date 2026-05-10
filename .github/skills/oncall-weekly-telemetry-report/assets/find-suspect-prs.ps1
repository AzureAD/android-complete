<#
.SYNOPSIS
    Find candidate PRs touching a class / file / method, across broker/ and common/ in parallel.

.DESCRIPTION
    Speeds up the PR-grep workflow in SKILL.md Step 4. Given a class name (or
    arbitrary regex), runs `git log -S` (pickaxe) AND `git log --grep` against
    both broker/ and common/ over the supplied window, then prints a unified
    table sorted by date.

    Use this AFTER you have identified the throw-site / wrapper class from the
    Originator pre-check (assets/queries/error-message-and-location.kql).

.PARAMETER Symbol
    String to search for in commit diffs (passed to `git log -S`). Typically
    the class name or method that hosts the throw site, e.g.
    'ExceptionAdapter', 'clientExceptionFromException', 'getKnownAuthorityResult'.

.PARAMETER GrepRegex
    Optional regex for `git log --grep` (commit message). Defaults to $Symbol.

.PARAMETER Since
    Inclusive start date (yyyy-MM-dd). Defaults to 28 days ago.

.PARAMETER Until
    Inclusive end date. Defaults to today.

.PARAMETER RepoRoot
    Defaults to C:\Users\<you>\Repos\android-complete. Overrides via -RepoRoot.

.EXAMPLE
    .\find-suspect-prs.ps1 -Symbol ExceptionAdapter -Since 2026-04-01

.EXAMPLE
    .\find-suspect-prs.ps1 -Symbol clientExceptionFromException -Since 2026-04-01 -Until 2026-05-09

.NOTES
    Cites repos with the URL pattern in SKILL.md (broker -> ad-accounts-for-android,
    common -> microsoft-authentication-library-common-for-android).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Symbol,
    [string]$GrepRegex,
    [string]$Since = (Get-Date).AddDays(-28).ToString('yyyy-MM-dd'),
    [string]$Until = (Get-Date).ToString('yyyy-MM-dd'),
    [string]$RepoRoot = (Join-Path $env:USERPROFILE 'Repos\android-complete')
)

if (-not $GrepRegex) { $GrepRegex = [regex]::Escape($Symbol) }

$repos = @(
    @{ Name='broker'; Path=(Join-Path $RepoRoot 'broker'); UrlBase='https://github.com/identity-authnz-teams/ad-accounts-for-android/pull/' }
    @{ Name='common'; Path=(Join-Path $RepoRoot 'common'); UrlBase='https://github.com/AzureAD/microsoft-authentication-library-common-for-android/pull/' }
)

$results = @()
foreach ($r in $repos) {
    if (-not (Test-Path $r.Path)) { Write-Warning "Repo path not found: $($r.Path)"; continue }
    Push-Location $r.Path
    try {
        # Pickaxe: PRs whose diff added or removed the symbol
        $pickaxeRaw = git log --since=$Since --until=$Until -S $Symbol --pretty=format:'%h|%ai|%an|%s' 2>$null
        # Grep: PRs whose subject mentions the regex (case-insensitive)
        $grepRaw    = git log --since=$Since --until=$Until --pretty=format:'%h|%ai|%an|%s' --grep=$GrepRegex -i 2>$null

        $seen = @{}
        foreach ($line in @($pickaxeRaw, $grepRaw | Where-Object { $_ })) {
            foreach ($l in @($line)) {
                if (-not $l) { continue }
                $parts = $l -split '\|', 4
                if ($parts.Count -lt 4) { continue }
                $sha = $parts[0]
                if ($seen.ContainsKey($sha)) { continue }
                $seen[$sha] = $true
                # Try to pull the PR number out of the subject (#NNN at end of MS PR convention)
                $prNum = $null
                if ($parts[3] -match '#(\d{2,5})\b') { $prNum = $Matches[1] }
                $results += [pscustomobject]@{
                    Repo    = $r.Name
                    Date    = $parts[1].Substring(0, 10)
                    Author  = $parts[2]
                    Sha     = $sha
                    PR      = if ($prNum) { '#' + $prNum } else { '' }
                    Url     = if ($prNum) { $r.UrlBase + $prNum } else { '' }
                    Subject = $parts[3]
                }
            }
        }
    } finally { Pop-Location }
}

if ($results.Count -eq 0) {
    Write-Host "No PRs match in window $Since .. $Until for symbol '$Symbol'."
    Write-Host "  Tip: try a shorter symbol (just the class name), or widen -Since."
    exit 0
}

$results | Sort-Object Date -Descending | Format-Table Repo, Date, Author, Sha, PR, @{n='Subject';e={$_.Subject.Substring(0, [Math]::Min(80, $_.Subject.Length))}} -AutoSize
Write-Host ""
Write-Host "PR URLs for citation in attribution cards:"
$results | Where-Object Url | Sort-Object Date -Descending | ForEach-Object { Write-Host "  $($_.Repo) #$($_.PR.TrimStart('#')): $($_.Url)" }
