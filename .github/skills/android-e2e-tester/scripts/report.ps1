# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Render a standard E2E test report (TestReport.html + TestReport.md) from a run-metadata JSON file, or
    (with `summary`) an overall multi-case run report (SUMMARY.html + SUMMARY.md) across a folder of
    per-case runs. For ADO test cases the per-case report is MANDATORY (Phase 7) — generate it on every
    outcome (PASS / FAIL / BLOCKED / PARTIAL), not only on success. When a batch of test cases is run,
    also generate the `summary` report over the batch's run folder.

    A test CASE may have more than one test POINT (e.g. an ECS-build point and a Local-build point). To keep
    everything in ONE report per case, put each point in a `testPoints` array (see schema below); `render`
    then emits a per-point section for each, plus a SINGLE shared "Proposed test steps" section at the end.
    Omit `testPoints` for a single-point run — the run object itself is treated as the one point (backward
    compatible).

.DESCRIPTION
    Feed it a JSON file describing the run; it writes TestReport.html and TestReport.md next to it (or to
    -OutDir). The renderer is defensive: any missing field is simply omitted, so a partial run still
    produces a report. NEVER put passwords/tokens in the JSON — include the account UPN only.

    JSON schema (all fields optional except title + verdict):
    {
      "title":       "Register AAD MFA cloud account via Sign in flow",
      "verdict":     "PASS",                 // PASS | FAIL | BLOCKED | PARTIAL (overall; derived from points if omitted)
      "verdictNote": "core objective met; browser number-match blocked by App Lock (env constraint)",
      "feature":     "AAD MFA sign-in + first-time MFA setup",
      "ado": { "testCaseId": 1579381, "planId": 714514, "suiteId": 3503165, "url": "https://..." },  // CASE-level ids
      // ---- SINGLE test point: put the point fields at the top level (no testPoints array) ----
      "device": { "model": "Samsung SM-F741U1", "serial": "R5CX...", "os": "Android 16 (SDK 36)",
                  "resolution": "1080x2640", "type": "physical" },
      "app":     { "package": "com.azure.authenticator", "version": "6.2607.4584" },
      "account": { "upn": "Locked_xxx@ID4SLab2.onmicrosoft.com", "usertype": "GlobalMFA", "tenant": "ID4SLab2.onmicrosoft.com" },
      "started": "2026-07-21 18:45", "finished": "2026-07-21 19:05", "iterations": 1,
      "steps": [ { "n":1, "action":"Launch Authenticator", "expected":"First-run screen",
                   "result":"PASS", "notes":"...", "screenshot":"iter1/01_firstrun.png" } ],
      "evidence":  [ "Account appears in Authenticator list (06_authenticator_accountlist.xml)" ],
      "blockers":  [ "App Lock gates browser approval behind device PIN/biometric" ],
      "artifacts": [ "iter1/logcat_scan.txt", "iter1/01_firstrun.png" ],
      // ---- MULTIPLE test points of the SAME case: use a testPoints array instead of the top-level point fields ----
      // Each entry carries its own point-level fields: verdict, verdictNote, ado{testPointId,configuration,buildSource},
      // device, app, account, started/finished/iterations, steps, evidence, blockers, artifacts. Reference each point's
      // screenshots by a path relative to the case report folder (e.g. "ecs/iter1/07_token.png", "local/iter1/07_token.png").
      "testPoints": [
        { "ado": { "testPointId": 3150577, "configuration": "RC MSAL - RC Broker", "buildSource": "ECS" },
          "verdict": "PASS", "device": {...}, "app": {...}, "account": {...}, "steps": [...], "evidence": [...] },
        { "ado": { "testPointId": 3150578, "configuration": "RC MSAL - RC Broker (LocalFlights)", "buildSource": "Local" },
          "verdict": "PASS", "device": {...}, "app": {...}, "account": {...}, "steps": [...], "evidence": [...] }
      ],
      // OPTIONAL recommendation block — CASE-level, rendered ONCE at the END as "Proposed test steps".
      // Use it to suggest clearer wording WITHOUT editing the ADO test case. It MUST be GENERIC across every
      // test point of this case (do NOT mention ECS vs Local or any specific build). "proposedSteps" renders in
      // the ADO Steps format (# / Action / Expected result / Attachments) and MUST be fully self-contained: fold
      // every prerequisite (account creation, app install, clean state) into the FIRST numbered steps — there is
      // no separate "preconditions" section, exactly like the ADO Steps editor. Set each step's optional
      // "attachment" to a relative screenshot path (or URL) for that step to fill the Attachments column. Each
      // step's "automation" hint is rendered SEPARATELY (skill-only) so it never pollutes the paste-ready steps.
      "proposedScope": "Full rewrite as N self-contained steps (or e.g. 'Minor — modify steps 1 and 2 only').",
      "proposedSteps": [ { "n":1, "action":"Create a temp user ...", "expected":"...", "attachment":"ecs/iter1/01_setup.png",
                           "automation":"skill-only hint, rendered below the ADO table" } ],
      "proposedMinimalEdits": [ "Step 1: change 'outlook.com' -> 'https://outlook.office.com/mail/'" ],
      "skillNotes":    [ "Pre-warm the temp user to avoid ESTS propagation lag" ]
    }

.EXAMPLE
    ./report.ps1 render -In C:\runs\aad-mfa\run.json
    ./report.ps1 render -In C:\runs\tc497038\run.json            # a case with a testPoints[] array → ONE report
    ./report.ps1 render -In C:\runs\aad-mfa\run.json -OutDir C:\runs\aad-mfa
    ./report.ps1 summary -In C:\runs\wpj-suite-20260723   # overall report across every run.json under the folder
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][ValidateSet('render', 'summary')][string]$Command = 'render',
    [Parameter(Mandatory)][string]$In,
    [string]$OutDir,
    [string]$Title
)

$ErrorActionPreference = 'Stop'

function Val($o, $name) { if ($o -and ($o.PSObject.Properties.Name -contains $name)) { return $o.$name } return $null }
function He([string]$s) { if ($null -eq $s) { return '' } [System.Net.WebUtility]::HtmlEncode([string]$s) }

# ======================= summary: overall report across many per-case runs =======================
# Scans a batch/run folder for every per-case run.json and emits SUMMARY.html + SUMMARY.md with a
# per-test-point verdict table (linked to each TestReport.html) and overall counts. Used whenever more than
# one ADO test case is run in a single session (see SKILL Phase 7 / "Running multiple test cases"). A case
# whose run.json carries a testPoints[] array contributes one row per point, all linking to the one case report.
if ($Command -eq 'summary') {
    if (-not (Test-Path $In)) { throw "Summary root not found: $In" }
    $rootItem = Get-Item $In
    $root = if ($rootItem.PSIsContainer) { (Resolve-Path $In).Path } else { Split-Path -Parent (Resolve-Path $In).Path }
    if (-not $OutDir) { $OutDir = $root }
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
    $OutDirFull = (Resolve-Path $OutDir).Path
    if (-not $Title) { $Title = 'E2E Test Run Summary' }

    $runFiles = Get-ChildItem -Path $root -Recurse -Filter 'run.json' -File -ErrorAction SilentlyContinue | Sort-Object FullName
    if (-not $runFiles) { throw "No run.json files found under $root" }

    function Vupper($o) { if (Val $o 'verdict') { return ([string](Val $o 'verdict')).ToUpper() } return 'UNKNOWN' }

    $cases = @()
    foreach ($rf in $runFiles) {
        try { $r = Get-Content $rf.FullName -Raw | ConvertFrom-Json } catch { Write-Warning "Skipping unreadable $($rf.FullName)"; continue }
        $dir = Split-Path -Parent $rf.FullName
        $htmlFull = Join-Path $dir 'TestReport.html'
        $rel = ''
        if (Test-Path $htmlFull) {
            if ($htmlFull.StartsWith($OutDirFull, [System.StringComparison]::OrdinalIgnoreCase)) {
                $rel = $htmlFull.Substring($OutDirFull.Length).TrimStart('\', '/')
            }
            else { $rel = $htmlFull }
        }
        $adoObj = Val $r 'ado'
        $tcId = (Val $adoObj 'testCaseId')
        $title = [string](Val $r 'title')
        $tps = Val $r 'testPoints'
        if ($tps) {
            # one row per test point, all linking to the single consolidated case report
            foreach ($pt in @($tps)) {
                $pAdo = Val $pt 'ado'; $pDev = Val $pt 'device'
                $cases += [pscustomobject]@{
                    tcId    = $tcId
                    title   = $title
                    verdict = (Vupper $pt)
                    note    = [string](Val $pt 'verdictNote')
                    device  = $(if ($pDev) { [string](Val $pDev 'serial') } else { '' })
                    config  = [string](Val $pAdo 'configuration')
                    build   = [string](Val $pAdo 'buildSource')
                    report  = $rel
                }
            }
        }
        else {
            $devObj = Val $r 'device'
            $cases += [pscustomobject]@{
                tcId    = $tcId
                title   = $title
                verdict = (Vupper $r)
                note    = [string](Val $r 'verdictNote')
                device  = $(if ($devObj) { [string](Val $devObj 'serial') } else { '' })
                config  = [string](Val $adoObj 'configuration')
                build   = [string](Val $adoObj 'buildSource')
                report  = $rel
            }
        }
    }

    $vColor = @{ PASS = '#107c10'; FAIL = '#d13438'; BLOCKED = '#c19c00'; PARTIAL = '#c19c00'; UNKNOWN = '#605e5c' }
    $order = @{ FAIL = 0; BLOCKED = 1; PARTIAL = 2; PASS = 3; UNKNOWN = 4 }
    $cases = @($cases | Sort-Object `
            @{ Expression = { $o = $order[$_.verdict]; if ($null -eq $o) { 5 } else { $o } } }, `
            @{ Expression = { [string]$_.tcId } }, `
            @{ Expression = { if ($_.build -eq 'ECS') { 0 } elseif ($_.build -eq 'Local') { 1 } else { 2 } } })

    $counts = [ordered]@{ PASS = 0; FAIL = 0; BLOCKED = 0; PARTIAL = 0; UNKNOWN = 0 }
    foreach ($c in $cases) { $v = $c.verdict; if (-not $counts.Contains($v)) { $v = 'UNKNOWN' }; $counts[$v]++ }
    $total = @($cases).Count
    $overall = if ($counts['FAIL'] -gt 0) { 'FAIL' } elseif ($counts['PASS'] -eq $total) { 'PASS' } else { 'PARTIAL' }
    $oc = $vColor[$overall]; if (-not $oc) { $oc = '#605e5c' }
    $countsLine = (@('PASS', 'FAIL', 'BLOCKED', 'PARTIAL', 'UNKNOWN') | Where-Object { $counts[$_] -gt 0 } | ForEach-Object { "$_ $($counts[$_])" }) -join ' · '
    $plural = if ($total -ne 1) { 's' } else { '' }

    # ---- Markdown ----
    $sm = New-Object System.Text.StringBuilder
    [void]$sm.AppendLine("# $Title")
    [void]$sm.AppendLine("")
    [void]$sm.AppendLine("**Overall: $overall** — $total test point$plural · $countsLine")
    [void]$sm.AppendLine("")
    [void]$sm.AppendLine("Generated $(Get-Date -Format 'yyyy-MM-dd HH:mm')")
    [void]$sm.AppendLine("")
    [void]$sm.AppendLine("| Test Case | Config | Title | Verdict | Device | Report | Note |")
    [void]$sm.AppendLine("|---|---|---|---|---|---|---|")
    foreach ($c in $cases) {
        $tc = if ($c.tcId) { "#$($c.tcId)" } else { '' }
        $cfgDisp = ((@($c.build, $c.config) | Where-Object { $_ }) -join ' — ') -replace '\|', '\|'
        $ti = ([string]$c.title) -replace '\|', '\|'
        $nt = ([string]$c.note) -replace '\|', '\|'
        $rp = if ($c.report) { "[report]($(($c.report) -replace '\\','/'))" } else { '' }
        [void]$sm.AppendLine("| $tc | $cfgDisp | $ti | $($c.verdict) | $($c.device) | $rp | $nt |")
    }
    [void]$sm.AppendLine("")
    $smPath = Join-Path $OutDirFull 'SUMMARY.md'
    $sm.ToString() | Out-File -FilePath $smPath -Encoding utf8

    # ---- HTML ----
    $srows = ''
    foreach ($c in $cases) {
        $rc = $vColor[$c.verdict]; if (-not $rc) { $rc = '#605e5c' }
        $tc = if ($c.tcId) { "#$(He ([string]$c.tcId))" } else { '' }
        $cfgDisp = (@($c.build, $c.config) | Where-Object { $_ }) -join ' — '
        $rp = if ($c.report) { "<a href='$(He (($c.report) -replace '\\','/'))'>report</a>" } else { '' }
        $srows += "<tr><td>$tc</td><td>$(He $cfgDisp)</td><td>$(He $c.title)</td><td><b style='color:$rc'>$(He $c.verdict)</b></td><td>$(He $c.device)</td><td>$rp</td><td>$(He $c.note)</td></tr>"
    }
    $chips = ''
    foreach ($k in $counts.Keys) { if ($counts[$k] -gt 0) { $cc = $vColor[$k]; $chips += "<span style='display:inline-block;background:$cc;color:#fff;border-radius:12px;padding:3px 10px;margin-right:6px;font-size:13px'>$k $($counts[$k])</span>" } }

    $shtml = @"
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>$(He $Title)</title>
<style>
 body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#faf9f8;color:#201f1e}
 .wrap{max-width:1100px;margin:0 auto;padding:24px}
 h1{font-size:22px;margin:0 0 8px}
 .verdict{display:inline-block;color:#fff;background:$oc;font-weight:700;padding:6px 14px;border-radius:14px;font-size:14px;margin-right:10px}
 table{border-collapse:collapse;width:100%;font-size:13px;margin-top:12px;background:#fff}
 th,td{border:1px solid #edebe9;padding:7px 10px;text-align:left;vertical-align:top}
 th{background:#f3f2f1}
 tbody tr:nth-child(even){background:#faf9f8}
 a{color:#0067b8}
 .foot{color:#a19f9d;font-size:12px;margin-top:28px}
</style></head><body><div class="wrap">
<h1>$(He $Title)</h1>
<div><span class="verdict">$overall</span>$chips</div>
<p style="color:#605e5c">$total test point$plural · generated $(Get-Date -Format 'yyyy-MM-dd HH:mm')</p>
<table><thead><tr><th>Test Case</th><th>Config</th><th>Title</th><th>Verdict</th><th>Device</th><th>Report</th><th>Note</th></tr></thead>
<tbody>$srows</tbody></table>
<p class="foot">Generated by the android-e2e-tester skill · $(Get-Date -Format 'yyyy-MM-dd HH:mm')</p>
</div></body></html>
"@
    $shtmlPath = Join-Path $OutDirFull 'SUMMARY.html'
    $shtml | Out-File -FilePath $shtmlPath -Encoding utf8

    Write-Host "Wrote:"
    Write-Host "  $shtmlPath"
    Write-Host "  $smPath"
    Write-Host "Overall: $overall ($countsLine)"
    return
}

# ======================= render: single test-case report (multi-test-point aware) =======================
if (-not (Test-Path $In)) { throw "Run JSON not found: $In" }
$run = Get-Content $In -Raw | ConvertFrom-Json
if (-not $OutDir) { $OutDir = Split-Path (Resolve-Path $In) -Parent }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

$vColor = @{ PASS = '#107c10'; FAIL = '#d13438'; BLOCKED = '#c19c00'; PARTIAL = '#c19c00'; UNKNOWN = '#605e5c' }
function VColor($v) { $c = $vColor[$v]; if (-not $c) { $c = '#605e5c' }; return $c }
function Vup($o) { $v = ([string](Val $o 'verdict')).ToUpper(); if (-not $v) { $v = 'UNKNOWN' }; return $v }
function HtmlList($items) { if (-not $items) { return '' } $li = ($items | ForEach-Object { "<li>$(He $_)</li>" }) -join ''; return "<ul>$li</ul>" }
function MetaRow($label, $val) { if ($val) { return "<tr><th>$(He $label)</th><td>$(He $val)</td></tr>" } return '' }

# A test case may carry multiple test points. If run.testPoints exists, render one consolidated report with a
# section per point + a SINGLE shared proposed-steps section at the end. Otherwise the run itself is the point.
$tps = Val $run 'testPoints'
$multi = [bool]$tps
$points = if ($multi) { @($tps) } else { @($run) }

# overall verdict: explicit top-level verdict wins; otherwise derive from the points.
if (Val $run 'verdict') { $verdict = Vup $run }
elseif ($multi) {
    $vs = @($points | ForEach-Object { Vup $_ })
    $verdict = if ($vs -contains 'FAIL') { 'FAIL' } elseif ($vs -contains 'BLOCKED') { 'BLOCKED' } elseif ($vs -contains 'PARTIAL' -or $vs -contains 'UNKNOWN') { 'PARTIAL' } else { 'PASS' }
}
else { $verdict = 'UNKNOWN' }
$vc = VColor $verdict

function Get-PointLabel($pt) {
    if (Val $pt 'label') { return [string](Val $pt 'label') }
    $a = Val $pt 'ado'; $bits = @()
    if ($a) { if (Val $a 'buildSource') { $bits += [string](Val $a 'buildSource') }; if (Val $a 'configuration') { $bits += [string](Val $a 'configuration') } }
    if ($bits.Count) { return ($bits -join ' — ') }
    if ($a -and (Val $a 'testPointId')) { return "Test point $(Val $a 'testPointId')" }
    return 'Test point'
}
function AttachMd($att) {
    if (-not $att) { return '' }
    $p = ([string]$att) -replace '\\', '/'
    $leaf = if ($att -match '^[a-zA-Z]+://') { 'screenshot' } else { try { Split-Path $att -Leaf } catch { $att } }
    return "[$leaf]($p)"
}
function AttachHtml($att) {
    if (-not $att) { return '' }
    $p = ([string]$att) -replace '\\', '/'
    $leaf = if ($att -match '^[a-zA-Z]+://') { 'screenshot' } else { try { Split-Path $att -Leaf } catch { $att } }
    return "<a href='$(He $p)'>$(He $leaf)</a>"
}

# ---- Proposed test steps (case-level, rendered ONCE; recommendation only — NOT applied to the ADO test case) ----
function ProposedMd($case) {
    $scope = Val $case 'proposedScope'; $steps = Val $case 'proposedSteps'; $min = Val $case 'proposedMinimalEdits'
    if (-not ($scope -or $steps)) { return '' }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine("## Proposed test steps (suggested rewrite — not applied to the ADO test case)")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("_Written in the ADO **Steps** format (# / Action / Expected result / Attachments) and fully self-contained — every prerequisite (account creation, app install, clean state) is a numbered step at the beginning, so a tester unfamiliar with the feature can complete it end-to-end. The steps are **generic across every test point** of this case (they don't mention specific builds/configurations). This is a **recommendation only** — the ADO test case was not modified._")
    [void]$sb.AppendLine("")
    if ($scope) { [void]$sb.AppendLine("### Scope of change"); [void]$sb.AppendLine(""); [void]$sb.AppendLine([string]$scope); [void]$sb.AppendLine("") }
    if ($steps) {
        [void]$sb.AppendLine("### Steps")
        [void]$sb.AppendLine("")
        [void]$sb.AppendLine("| # | Action | Expected result | Attachments |")
        [void]$sb.AppendLine("|---|--------|-----------------|-------------|")
        foreach ($s in $steps) {
            $n = Val $s 'n'; $a = (Val $s 'action') -replace '\|', '\|'; $e = (Val $s 'expected') -replace '\|', '\|'
            $att = AttachMd (Val $s 'attachment')
            [void]$sb.AppendLine("| $n | $a | $e | $att |")
        }
        [void]$sb.AppendLine("")
    }
    if ($min) {
        [void]$sb.AppendLine("### Minimal high-value edits (if you prefer not to rewrite the whole case)")
        [void]$sb.AppendLine("")
        foreach ($i in $min) { [void]$sb.AppendLine("- $i") }
        [void]$sb.AppendLine("")
    }
    $autoLines = @()
    if ($steps) { foreach ($s in $steps) { $au = Val $s 'automation'; if ($au) { $autoLines += "- **Step $(Val $s 'n'):** $au" } } }
    if ($autoLines.Count) {
        [void]$sb.AppendLine("### Automation notes (for the e2e-tester skill — not part of the ADO steps)")
        [void]$sb.AppendLine("")
        foreach ($l in $autoLines) { [void]$sb.AppendLine($l) }
        [void]$sb.AppendLine("")
    }
    return $sb.ToString()
}
function ProposedHtml($case) {
    $scope = Val $case 'proposedScope'; $steps = Val $case 'proposedSteps'; $min = Val $case 'proposedMinimalEdits'
    if (-not ($scope -or $steps)) { return '' }
    $h = "<h2>Proposed test steps (suggested rewrite — not applied to the ADO test case)</h2>"
    $h += "<p class='note'>Written in the ADO <b>Steps</b> format (# / Action / Expected result / Attachments) and fully self-contained — every prerequisite (account creation, app install, clean state) is a numbered step at the beginning, so a tester unfamiliar with the feature can complete it end-to-end. The steps are <b>generic across every test point</b> of this case. <b>Recommendation only</b> — the ADO test case was not modified.</p>"
    if ($scope) { $h += "<h3>Scope of change</h3><p>$(He $scope)</p>" }
    if ($steps) {
        $pr = ''
        foreach ($s in $steps) {
            $pr += "<tr><td>$(He (Val $s 'n'))</td><td>$(He (Val $s 'action'))</td><td>$(He (Val $s 'expected'))</td><td>$(AttachHtml (Val $s 'attachment'))</td></tr>"
        }
        $h += "<h3>Steps</h3><table><thead><tr><th>#</th><th>Action</th><th>Expected result</th><th>Attachments</th></tr></thead><tbody>$pr</tbody></table>"
    }
    if ($min) { $h += "<h3>Minimal high-value edits (if you prefer not to rewrite the whole case)</h3>$(HtmlList $min)" }
    $autoItems = @()
    if ($steps) { foreach ($s in $steps) { $au = Val $s 'automation'; if ($au) { $autoItems += "Step $(Val $s 'n'): $au" } } }
    if ($autoItems.Count) { $h += "<h3>Automation notes (for the e2e-tester skill — not part of the ADO steps)</h3>$(HtmlList $autoItems)" }
    return $h
}

# ---- Per-test-point section renderers (shared by the single- and multi-point paths) ----
function AddPointMd($sb, $pt, $isMulti) {
    $sub = if ($isMulti) { '###' } else { '##' }
    if ($isMulti) {
        [void]$sb.AppendLine("## Test point: $(Get-PointLabel $pt) — $(Vup $pt)")
        [void]$sb.AppendLine("")
        $note = Val $pt 'verdictNote'
        if ($note) { [void]$sb.AppendLine("_${note}_"); [void]$sb.AppendLine("") }
    }
    if (-not $isMulti) { $feat = Val $run 'feature'; if ($feat) { [void]$sb.AppendLine("- **Feature:** $feat") } }
    $a = Val $pt 'ado'
    if ($a) {
        if (Val $a 'configuration') { [void]$sb.AppendLine("- **Config:** $(Val $a 'configuration')") }
        if (Val $a 'buildSource') { [void]$sb.AppendLine("- **Build:** $(Val $a 'buildSource')") }
        if (Val $a 'testPointId') { [void]$sb.AppendLine("- **Test point:** $(Val $a 'testPointId')") }
    }
    $d = Val $pt 'device'
    if ($d) {
        $dtxt = (@((Val $d 'model'), (Val $d 'os'), (Val $d 'type'), (Val $d 'resolution') | Where-Object { $_ }) -join ' · ')
        if ($dtxt) { [void]$sb.AppendLine("- **Device:** $dtxt") }
        if (Val $d 'serial') { [void]$sb.AppendLine("- **Serial:** $(Val $d 'serial')") }
    }
    $ap = Val $pt 'app'
    if ($ap) { $t = (@((Val $ap 'package'), (Val $ap 'version') | Where-Object { $_ }) -join ' v'); if ($t) { [void]$sb.AppendLine("- **App:** $t") } }
    $ac = Val $pt 'account'
    if ($ac) { $t = (@((Val $ac 'upn'), (Val $ac 'usertype') | Where-Object { $_ }) -join '  ·  '); if ($t) { [void]$sb.AppendLine("- **Account:** $t") } }
    if (Val $pt 'started') { [void]$sb.AppendLine("- **Started:** $(Val $pt 'started')") }
    if (Val $pt 'finished') { [void]$sb.AppendLine("- **Finished:** $(Val $pt 'finished')") }
    if (Val $pt 'iterations') { [void]$sb.AppendLine("- **Iterations:** $(Val $pt 'iterations')") }
    [void]$sb.AppendLine("")
    $steps = Val $pt 'steps'
    if ($steps) {
        [void]$sb.AppendLine("$sub Steps")
        [void]$sb.AppendLine("")
        [void]$sb.AppendLine("| # | Action | Expected | Result | Notes |")
        [void]$sb.AppendLine("|---|--------|----------|--------|-------|")
        foreach ($s in $steps) {
            $n = Val $s 'n'; $ax = (Val $s 'action') -replace '\|', '\|'; $e = (Val $s 'expected') -replace '\|', '\|'
            $r = Val $s 'result'; $no = (Val $s 'notes') -replace '\|', '\|'
            [void]$sb.AppendLine("| $n | $ax | $e | $r | $no |")
        }
        [void]$sb.AppendLine("")
    }
    foreach ($pair in @(, @('Evidence', (Val $pt 'evidence'))) + @(, @('Blockers', (Val $pt 'blockers'))) + @(, @('Artifacts', (Val $pt 'artifacts')))) {
        $lbl = $pair[0]; $items = $pair[1]
        if ($items) { [void]$sb.AppendLine("$sub $lbl"); [void]$sb.AppendLine(""); foreach ($i in $items) { [void]$sb.AppendLine("- $i") }; [void]$sb.AppendLine("") }
    }
}
function PointHtml($pt, $isMulti) {
    $sub = if ($isMulti) { 'h3' } else { 'h2' }
    $frag = ''
    if ($isMulti) {
        $pv = Vup $pt
        $frag += "<h2>Test point: $(He (Get-PointLabel $pt)) <span class='chip' style='background:$(VColor $pv)'>$(He $pv)</span></h2>"
        $note = Val $pt 'verdictNote'; if ($note) { $frag += "<p class='note'>$(He $note)</p>" }
    }
    else { $frag += "<h2>Run details</h2>" }
    $a = Val $pt 'ado'; $d = Val $pt 'device'; $ap = Val $pt 'app'; $ac = Val $pt 'account'
    $rows = ''
    if (-not $isMulti) { $rows += MetaRow 'Feature' (Val $run 'feature') }
    if ($a) { $rows += MetaRow 'Config' (Val $a 'configuration'); $rows += MetaRow 'Build' (Val $a 'buildSource') }
    $devTxt = if ($d) { (@((Val $d 'model'), (Val $d 'os'), (Val $d 'type'), (Val $d 'resolution') | Where-Object { $_ }) -join ' · ') } else { '' }
    $rows += MetaRow 'Device' $devTxt
    $rows += MetaRow 'Serial' $(if ($d) { Val $d 'serial' })
    $appTxt = if ($ap) { (@((Val $ap 'package'), (Val $ap 'version') | Where-Object { $_ }) -join ' v') } else { '' }
    $rows += MetaRow 'App' $appTxt
    $acctTxt = if ($ac) { (@((Val $ac 'upn'), (Val $ac 'usertype'), (Val $ac 'tenant') | Where-Object { $_ }) -join '  ·  ') } else { '' }
    $rows += MetaRow 'Account' $acctTxt
    $rows += MetaRow 'Started' (Val $pt 'started')
    $rows += MetaRow 'Finished' (Val $pt 'finished')
    $rows += MetaRow 'Iterations' (Val $pt 'iterations')
    $frag += "<table class='meta'>$rows</table>"
    $steps = Val $pt 'steps'
    if ($steps) {
        $sr = ''
        foreach ($s in $steps) {
            $rc = VColor (([string](Val $s 'result')).ToUpper())
            $shot = Val $s 'screenshot'
            $shotCell = if ($shot) { $sp = ([string]$shot) -replace '\\', '/'; "<a href='$(He $sp)'>$(He (Split-Path $shot -Leaf))</a>" } else { '' }
            $sr += "<tr><td>$(He (Val $s 'n'))</td><td>$(He (Val $s 'action'))</td><td>$(He (Val $s 'expected'))</td><td><b style='color:$rc'>$(He (Val $s 'result'))</b></td><td>$(He (Val $s 'notes'))</td><td>$shotCell</td></tr>"
        }
        $frag += "<$sub>Steps</$sub><table><thead><tr><th>#</th><th>Action</th><th>Expected</th><th>Result</th><th>Notes</th><th>Screenshot</th></tr></thead><tbody>$sr</tbody></table>"
    }
    if (Val $pt 'evidence') { $frag += "<$sub>Evidence</$sub>$(HtmlList (Val $pt 'evidence'))" }
    if (Val $pt 'blockers') { $frag += "<$sub>Blockers</$sub>$(HtmlList (Val $pt 'blockers'))" }
    if (Val $pt 'artifacts') { $frag += "<$sub>Artifacts</$sub>$(HtmlList (Val $pt 'artifacts'))" }
    return $frag
}

# ---------- Markdown ----------
$md = New-Object System.Text.StringBuilder
[void]$md.AppendLine("# E2E Test Report — $(Val $run 'title')")
[void]$md.AppendLine("")
[void]$md.AppendLine("**Verdict: $verdict**" + $(if (Val $run 'verdictNote') { " — $(Val $run 'verdictNote')" } else { '' }))
[void]$md.AppendLine("")
$ado = Val $run 'ado'
if ($ado) {
    $adoLine = @()
    if (Val $ado 'testCaseId') { $adoLine += "Test Case #$(Val $ado 'testCaseId')" }
    if (Val $ado 'planId') { $adoLine += "Plan $(Val $ado 'planId')" }
    if (Val $ado 'suiteId') { $adoLine += "Suite $(Val $ado 'suiteId')" }
    if ($adoLine.Count) { [void]$md.AppendLine("**ADO:** " + ($adoLine -join ' · ') + $(if (Val $ado 'url') { "  <$(Val $ado 'url')>" } else { '' })); [void]$md.AppendLine("") }
}
if ($multi -and (Val $run 'feature')) { [void]$md.AppendLine("- **Feature:** $(Val $run 'feature')"); [void]$md.AppendLine("") }
if ($multi) {
    $tpl = @($points | ForEach-Object { "$(Get-PointLabel $_) ➜ $(Vup $_)" }) -join ' · '
    [void]$md.AppendLine("**Test points ($($points.Count)):** $tpl")
    [void]$md.AppendLine("")
}
foreach ($pt in $points) { AddPointMd $md $pt $multi }
$pmd = ProposedMd $run
if ($pmd) { [void]$md.Append($pmd) }
$sn = Val $run 'skillNotes'
if ($sn) { [void]$md.AppendLine("## Notes for the e2e-tester skill"); [void]$md.AppendLine(""); foreach ($i in $sn) { [void]$md.AppendLine("- $i") }; [void]$md.AppendLine("") }
$mdPath = Join-Path $OutDir 'TestReport.md'
$md.ToString() | Out-File -FilePath $mdPath -Encoding utf8

# ---------- HTML ----------
$adoTxt = ''
if ($ado) {
    $bits = @()
    if (Val $ado 'testCaseId') { $bits += "Test Case #$(Val $ado 'testCaseId')" }
    if (Val $ado 'planId') { $bits += "Plan $(Val $ado 'planId')" }
    if (Val $ado 'suiteId') { $bits += "Suite $(Val $ado 'suiteId')" }
    $t = $bits -join ' · '
    if (Val $ado 'url') { $adoTxt = "<a href='$(He (Val $ado 'url'))'>$(He $t)</a>" } else { $adoTxt = He $t }
}
$tpSummary = ''
if ($multi) {
    $chips = ''
    foreach ($pt in $points) { $pv = Vup $pt; $chips += "<span class='chip' style='background:$(VColor $pv);margin:0 6px 4px 0'>$(He (Get-PointLabel $pt)): $(He $pv)</span>" }
    $tpSummary = "<p>$chips</p>"
}
$caseMetaRows = ''
if ($adoTxt) { $caseMetaRows += "<tr><th>ADO</th><td>$adoTxt</td></tr>" }
if ($multi -and (Val $run 'feature')) { $caseMetaRows += MetaRow 'Feature' (Val $run 'feature') }
$caseMeta = if ($caseMetaRows) { "<h2>Run details</h2><table class='meta'>$caseMetaRows</table>" } else { '' }
$pointsHtml = ''
foreach ($pt in $points) { $pointsHtml += (PointHtml $pt $multi) }
$proposedHtml = ProposedHtml $run
$skillNotesHtml = if (Val $run 'skillNotes') { "<h2>Notes for the e2e-tester skill</h2>$(HtmlList (Val $run 'skillNotes'))" } else { '' }

$html = @"
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>E2E Test Report — $(He (Val $run 'title'))</title>
<style>
 body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#faf9f8;color:#201f1e}
 .wrap{max-width:1000px;margin:0 auto;padding:24px}
 h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:24px 0 8px;border-bottom:1px solid #edebe9;padding-bottom:4px}
 h3{font-size:14px;margin:16px 0 6px;color:#323130}
 .verdict{display:inline-block;color:#fff;background:$vc;font-weight:700;padding:6px 14px;border-radius:14px;font-size:14px}
 .chip{display:inline-block;color:#fff;border-radius:12px;padding:2px 9px;font-size:12px;font-weight:700;margin-left:6px}
 .note{color:#605e5c;margin:8px 0 0}
 table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px;background:#fff}
 th,td{border:1px solid #edebe9;padding:7px 10px;text-align:left;vertical-align:top}
 .meta th{width:120px;background:#f3f2f1}
 tbody tr:nth-child(even){background:#faf9f8}
 ul{margin:6px 0 0 18px} a{color:#0067b8}
 .foot{color:#a19f9d;font-size:12px;margin-top:28px}
</style></head><body><div class="wrap">
<h1>E2E Test Report — $(He (Val $run 'title'))</h1>
<div><span class="verdict">$verdict</span></div>
$(if (Val $run 'verdictNote'){"<p class='note'>$(He (Val $run 'verdictNote'))</p>"})
$tpSummary
$caseMeta
$pointsHtml
$proposedHtml
$skillNotesHtml
<p class="foot">Generated by the android-e2e-tester skill · $(Get-Date -Format 'yyyy-MM-dd HH:mm')</p>
</div></body></html>
"@
$htmlPath = Join-Path $OutDir 'TestReport.html'
$html | Out-File -FilePath $htmlPath -Encoding utf8

Write-Host "Wrote:"
Write-Host "  $htmlPath"
Write-Host "  $mdPath"
Write-Host "Verdict: $verdict"
