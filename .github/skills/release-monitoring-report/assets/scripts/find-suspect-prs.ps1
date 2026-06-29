<#
.SYNOPSIS
    Find candidate PRs touching a class / file / method, across broker/, common/ and the
    authenticator/ app repo in parallel. Supports BOTH a date window (weekly-style) AND a
    release version range (release-style).

.DESCRIPTION
    Given a symbol (or arbitrary regex), runs `git log -S` (pickaxe, diff-content), optionally
    `git log -G` (diff-text regex) AND `git log --grep` (commit subject) against the selected
    repos, then prints a unified table sorted by date.

    Two windowing modes:
      * Date window  — default; -Since / -Until (weekly on-call use).
      * Version range — pass -Range 'v16.1.0..v16.2.0' (broker tags) or '6.2606.3817..6.2606.4029'
        (authenticator tags) to correlate exactly the commits that shipped between two releases
        (release-monitoring use). When -Range is set, -Since / -Until are ignored. Each repo is
        resolved by trying the range endpoints as its OWN refs first (works for broker and the
        authenticator app, which each carry the relevant tags); a repo that lacks the tags but is
        pinned as a broker submodule (common) is mapped via the broker submodule pointer; otherwise
        it is skipped with a warning (never silent).

    Two attribution flavours:
      * Server-returned auth errors (invalid_grant / interaction_required) — the trigger is in
        broker/ + common/ (the default repo set). Weight device-PoP / PRT / cache-path changes.
      * Authenticator crashes (new or rising App Center signatures) — search the owning repo with
        -Repos authenticator and a Range of the bundled app tags. CRITICAL: a crash frame names the
        object being inspected (the VICTIM), which is often NOT the file that broke. The culprit is
        usually a CALLER that passes that object into a failing API. So set -Symbol to the
        exception/API token from the stack (e.g. 'EntryPoints.get', 'GeneratedComponent'), NOT the
        crashing class — the pickaxe then finds the caller that introduced the bad call. ALWAYS also
        pass -DiffGrep with the same token, because a culprit PR's SUBJECT almost never mentions the
        subsystem it broke (e.g. a "TOTP Secret Fix" PR that added a Hilt EntryPoints.get call) so
        --grep alone misses it. Verified: searching -Symbol MfaAuthDialogActivity found nothing, but
        -Symbol 'EntryPoints.get' -DiffGrep 'EntryPoints' surfaced the real culprit on the first try.

    Use this AFTER reading the full `git log <range>` (ranges between two releases are small)
    and identifying the suspect code path.

.PARAMETER Symbol
    String to search for in commit diffs (passed to `git log -S`). Typically the class / method on
    the suspect path, e.g. 'AbstractDevicePopManager', 'generateAsymmetricKey'. For a CRASH, use the
    exception/API token from the stack (e.g. 'EntryPoints.get'), not the crashing class — the culprit
    is the caller that passes the crashing class into the failing API.

.PARAMETER GrepRegex
    Optional regex for `git log --grep` (commit SUBJECT only — low recall; a PR rarely names the
    subsystem it breaks). Omit to skip the subject search. Prefer -DiffGrep for crashes.

.PARAMETER DiffGrep
    Optional regex for `git log -G` (matches the DIFF TEXT, not just the subject). Use this for crash
    attribution so a culprit whose subject never mentions the broken subsystem is still found.

.PARAMETER Range
    Git revision range, e.g. 'v16.1.0..v16.2.0'. When set, overrides -Since / -Until.

.PARAMETER Since
    Inclusive start date (yyyy-MM-dd). Defaults to 28 days ago. Ignored if -Range is set.

.PARAMETER Until
    Inclusive end date. Defaults to today. Ignored if -Range is set.

.PARAMETER RepoRoot
    Root folder containing `broker/`, `common/` and `authenticator/` subfolders. Defaults to the
    git top-level of the current working directory (so running from any clone of android-complete
    works).

.PARAMETER Repos
    Which repos to search. Defaults to broker + common (the auth-code attribution set). For crash
    attribution pass -Repos authenticator (the crashing frame's owning repo). Accepts any subset of
    broker, common, authenticator.

.EXAMPLE
    .\find-suspect-prs.ps1 -Symbol AbstractDevicePopManager -Range v16.1.0..v16.2.0

.EXAMPLE
    .\find-suspect-prs.ps1 -Symbol generateAsymmetricKey -Since 2026-05-01 -Until 2026-06-19

.EXAMPLE
    # Authenticator crash attribution — search the EXCEPTION TOKEN from the stack (not the crashing
    # class) and use -DiffGrep so a culprit with an unrelated subject is still found. This exact
    # search surfaced PR 15896454 ("TOTP Secret Fix") as the culprit for the dagger.hilt crash on
    # MfaAuthDialogActivity, which a -Symbol MfaAuthDialogActivity search had completely missed:
    .\find-suspect-prs.ps1 -Repos authenticator -Range 6.2606.3817..6.2606.4029 `
        -Symbol 'EntryPoints.get' -DiffGrep 'EntryPoints|GeneratedComponent'

.NOTES
    Cites repos with the URL patterns: broker -> ad-accounts-for-android (GitHub PR),
    common -> microsoft-authentication-library-common-for-android (GitHub PR),
    authenticator -> AD-MFA-phonefactor-phoneApp-android (ADO pullrequest; PR # parsed from the
    "Merged PR NNNNNNNN:" commit-subject convention).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Symbol,
    [string]$GrepRegex,
    [string]$DiffGrep,
    [string]$Range,
    [string]$Since = (Get-Date).AddDays(-28).ToString('yyyy-MM-dd'),
    [string]$Until = (Get-Date).ToString('yyyy-MM-dd'),
    [string]$RepoRoot,
    [string[]]$Repos
)

# Resolve repo root: explicit -RepoRoot wins; otherwise discover via `git rev-parse --show-toplevel`.
if (-not $RepoRoot) {
    $gitRoot = (git rev-parse --show-toplevel 2>$null)
    if ($gitRoot) {
        $RepoRoot = $gitRoot.Trim()
    } else {
        $RepoRoot = (Join-Path $env:USERPROFILE 'Repos\android-complete')
        Write-Warning "Not inside a git working tree. Falling back to legacy default: $RepoRoot. Pass -RepoRoot explicitly to silence this."
    }
}

if (-not $GrepRegex) { $GrepRegex = [regex]::Escape($Symbol) }

$repoDefs = @(
    @{ Name='broker';        Path=(Join-Path $RepoRoot 'broker');        UrlBase='https://github.com/identity-authnz-teams/ad-accounts-for-android/pull/' }
    @{ Name='common';        Path=(Join-Path $RepoRoot 'common');        UrlBase='https://github.com/AzureAD/microsoft-authentication-library-common-for-android/pull/' }
    @{ Name='authenticator'; Path=(Join-Path $RepoRoot 'authenticator'); UrlBase='https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/'; Ado=$true }
)

# Which repos to search. Default = broker + common (auth-code attribution); authenticator is
# only searched when explicitly requested (-Repos authenticator) so the broker/common auth-code
# flow never noisily probes the app repo with a broker-tag range.
if ($Repos -and $Repos.Count -gt 0) {
    $unknown = @($Repos | Where-Object { $_ -notin $repoDefs.Name })
    if ($unknown.Count -gt 0) {
        Write-Error "Unknown -Repos value(s): $($unknown -join ', '). Known: $($repoDefs.Name -join ', ')."
        exit 2
    }
    $repoDefs = @($repoDefs | Where-Object { $Repos -contains $_.Name })
} else {
    $repoDefs = @($repoDefs | Where-Object { $_.Name -in @('broker','common') })
}

# FAIL LOUDLY if none of the requested subrepos exist under the resolved root.
$availableRepos = @($repoDefs | Where-Object { Test-Path $_.Path })
if ($availableRepos.Count -eq 0) {
    Write-Error @"
None of the requested repos ($($repoDefs.Name -join ', ')) found under -RepoRoot $RepoRoot.

Expected layout:
  $RepoRoot\broker\          (clone of identity-authnz-teams/ad-accounts-for-android)
  $RepoRoot\common\          (clone of AzureAD/microsoft-authentication-library-common-for-android)
  $RepoRoot\authenticator\   (clone of msazure One/AD-MFA-phonefactor-phoneApp-android)

Pass -RepoRoot pointing at the parent of those clones. The android-complete mono-repo
at the repo root works because broker/, common/ and authenticator/ are submodules there.
"@
    exit 2
}
if ($availableRepos.Count -lt $repoDefs.Count) {
    $missing = $repoDefs | Where-Object { -not (Test-Path $_.Path) } | ForEach-Object { $_.Name }
    Write-Warning "Skipping $($missing -join ', ') — not found under $RepoRoot. Results will be incomplete."
}

# Build the per-repo windowing args. For -Range, a repo is resolved by trying the range
# endpoints as its OWN refs first: broker (v16.x tags) and the authenticator app
# (6.xxxx.xxxx tags) both carry the relevant release tags, so they use the range directly.
# A repo that lacks the tags but is pinned as a broker submodule (common — which reuses the
# v16.x numbers for an unrelated older namespace) is mapped to the SHA the broker tree pins
# for it at each endpoint, so it scans exactly the commits that shipped in that broker release.
$brokerPath = (Join-Path $RepoRoot 'broker')
function Get-WindowArgs($repo) {
    if (-not $Range) { return @("--since=$Since", "--until=$Until") }

    $ends = $Range -split '\.\.', 2
    if ($ends.Count -ne 2) { Write-Warning "Malformed -Range '$Range'."; return @() }

    # 1) If BOTH endpoints resolve as this repo's own refs (tags/commits), use the range directly.
    $bothLocal = $true
    foreach ($e in $ends) {
        if ($e -and -not (git -C $repo.Path rev-parse --verify --quiet "$e^{commit}" 2>$null)) { $bothLocal = $false; break }
    }
    if ($bothLocal) { return @($Range) }

    # 2) Otherwise map each broker tag -> the SHA the broker tree pins for this repo as a submodule.
    if (-not (Test-Path $brokerPath)) {
        Write-Warning "broker/ not found at $brokerPath; cannot translate '$Range' to the $($repo.Name) submodule range. Skipping $($repo.Name)."
        return @()
    }
    $subName = $repo.Name   # submodule directory name inside the broker tree (e.g. 'common')
    $sha = @()
    foreach ($e in $ends) {
        $entry = git -C $brokerPath ls-tree $e $subName 2>$null
        if ($entry -match '160000 commit ([0-9a-f]{40})') {
            $s = $Matches[1]
            if (-not (git -C $repo.Path rev-parse --verify --quiet "$s^{commit}" 2>$null)) {
                Write-Warning "Submodule SHA $s (from broker $e) not present in $($repo.Path) — run 'git -C $($repo.Path) fetch'. Skipping $($repo.Name)."
                return @()
            }
            $sha += $s
        } else {
            Write-Warning "Could not read '$subName' submodule pointer at broker '$e'. Skipping $($repo.Name)."
            return @()
        }
    }
    return @("$($sha[0])..$($sha[1])")
}

$results = @()
foreach ($r in $availableRepos) {
    $winArgs = @(Get-WindowArgs $r)
    if ($winArgs.Count -eq 0) { continue }
    Push-Location $r.Path
    try {
        # Pickaxe: PRs whose diff added or removed the symbol (content-level — finds the CALLER
        # that introduced/removed a reference, which for a crash is usually the culprit, not the
        # crashing class itself). Set -Symbol to the exception/API token from the stack.
        $pickaxeRaw = git log @winArgs -S $Symbol --pretty=format:'%h|%ai|%an|%s' 2>$null
        # Diff-content grep (-G): PRs whose DIFF text matches the regex anywhere — catches a culprit
        # whose subject never mentions the broken subsystem (the #1 reason --grep misses a crash PR).
        $diffGrepRaw = if ($DiffGrep) { git log @winArgs -G $DiffGrep --pretty=format:'%h|%ai|%an|%s' 2>$null } else { @() }
        # Grep: PRs whose SUBJECT mentions the regex (case-insensitive) — subject-only, low recall.
        $grepRaw    = if ($GrepRegex) { git log @winArgs --pretty=format:'%h|%ai|%an|%s' --grep=$GrepRegex -i 2>$null } else { @() }

        $seen = @{}
        foreach ($line in @($pickaxeRaw, $diffGrepRaw, $grepRaw | Where-Object { $_ })) {
            foreach ($l in @($line)) {
                if (-not $l) { continue }
                $parts = $l -split '\|', 4
                if ($parts.Count -lt 4) { continue }
                $sha = $parts[0]
                if ($seen.ContainsKey($sha)) { continue }
                $seen[$sha] = $true
                # Pull the PR number from the subject. GitHub squash-merges read "...(#NNN)";
                # ADO merges read "Merged PR NNNNNNNN: <title>" (8-digit PR ids, no '#').
                $prNum = $null
                if ($r.Ado) {
                    if ($parts[3] -match 'Merged PR (\d+)') { $prNum = $Matches[1] }
                } else {
                    if ($parts[3] -match '#(\d{2,5})\b') { $prNum = $Matches[1] }
                }
                $prLabel = if (-not $prNum) { '' } elseif ($r.Ado) { 'PR ' + $prNum } else { '#' + $prNum }
                $results += [pscustomobject]@{
                    Repo    = $r.Name
                    Date    = $parts[1].Substring(0, 10)
                    Author  = $parts[2]
                    Sha     = $sha
                    PR      = $prLabel
                    Url     = if ($prNum) { $r.UrlBase + $prNum } else { '' }
                    Subject = $parts[3]
                }
            }
        }
    } finally { Pop-Location }
}

$windowLabel = if ($Range) { "range $Range" } else { "window $Since .. $Until" }
if ($results.Count -eq 0) {
    Write-Host "No PRs match in $windowLabel for symbol '$Symbol'."
    Write-Host "  Tip: try a shorter symbol (just the class name), or widen the window/range."
    exit 0
}

$results | Sort-Object Date -Descending | Format-Table Repo, Date, Author, Sha, PR, @{n='Subject';e={$_.Subject.Substring(0, [Math]::Min(80, $_.Subject.Length))}} -AutoSize
Write-Host ""
Write-Host "PR URLs for citation in attribution cards ($windowLabel):"
$results | Where-Object Url | Sort-Object Date -Descending | ForEach-Object { Write-Host "  $($_.Repo) $($_.PR): $($_.Url)" }
