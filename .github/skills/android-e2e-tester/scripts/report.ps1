# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Render a standard E2E test report (TestReport.html + TestReport.md) from a run-metadata JSON file, or
    (with `summary`) an overall multi-case run report (SUMMARY.html + SUMMARY.md) across a folder of
    per-case runs. For ADO test cases the per-case report is MANDATORY (Phase 7) — generate it on every
    outcome (PASS / FAIL / BLOCKED / PARTIAL), not only on success. When a batch of test cases is run,
    also generate the `summary` report over the batch's run folder.

.DESCRIPTION
    Feed it a JSON file describing the run; it writes TestReport.html and TestReport.md next to it (or to
    -OutDir). The renderer is defensive: any missing field is simply omitted, so a partial run still
    produces a report. NEVER put passwords/tokens in the JSON — include the account UPN only.

    JSON schema (all fields optional except title + verdict):
    {
      "title":       "Register AAD MFA cloud account via Sign in flow",
      "verdict":     "PASS",                 // PASS | FAIL | BLOCKED | PARTIAL
      "verdictNote": "core objective met; browser number-match blocked by App Lock (env constraint)",
      "feature":     "AAD MFA sign-in + first-time MFA setup",
      "ado": { "testCaseId": 1579381, "planId": 714514, "suiteId": 3503165, "url": "https://...",
               "testPointId": 3150404, "configuration": "RC MSAL - RC Broker", "buildSource": "ECS" },
      "device": { "model": "Samsung SM-F741U1", "serial": "R5CX...", "os": "Android 16 (SDK 36)",
                  "resolution": "1080x2640", "type": "physical" },
      "app":     { "package": "com.azure.authenticator", "version": "6.2607.4584" },
      "account": { "upn": "Locked_xxx@ID4SLab2.onmicrosoft.com", "usertype": "GlobalMFA", "tenant": "ID4SLab2.onmicrosoft.com" },
      "started": "2026-07-21 18:45", "finished": "2026-07-21 19:05", "iterations": 1,
      "steps": [ { "n":1, "action":"Launch Authenticator", "expected":"First-run screen",
                   "result":"PASS", "notes":"...", "screenshot":"iter1/01_firstrun.png" } ],
      "evidence":  [ "Account appears in Authenticator list (06_authenticator_accountlist.xml)" ],
      "blockers":  [ "App Lock gates browser approval behind device PIN/biometric" ],
      "artifacts": [ "iter1/logcat_scan.txt", "iter1/01_firstrun.png" ]
    }

.EXAMPLE
    ./report.ps1 render -In C:\runs\aad-mfa\run.json
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
# per-case verdict table (linked to each TestReport.html) and overall counts. Used whenever more than
# one ADO test case is run in a single session (see SKILL Phase 7 / "Running multiple test cases").
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
        $adoObj = Val $r 'ado'; $devObj = Val $r 'device'
        $cases += [pscustomobject]@{
            tcId     = (Val $adoObj 'testCaseId')
            title    = [string](Val $r 'title')
            verdict  = $(if (Val $r 'verdict') { ([string](Val $r 'verdict')).ToUpper() } else { 'UNKNOWN' })
            note     = [string](Val $r 'verdictNote')
            device   = $(if ($devObj) { [string](Val $devObj 'serial') } else { '' })
            provider = [string](Val $r 'provider')
            config   = [string](Val $adoObj 'configuration')
            build    = [string](Val $adoObj 'buildSource')
            report   = $rel
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
    [void]$sm.AppendLine("**Overall: $overall** — $total case$plural · $countsLine")
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
<p style="color:#605e5c">$total case$plural · generated $(Get-Date -Format 'yyyy-MM-dd HH:mm')</p>
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

# ======================= render: single test-case report =======================
if (-not (Test-Path $In)) { throw "Run JSON not found: $In" }
$run = Get-Content $In -Raw | ConvertFrom-Json
if (-not $OutDir) { $OutDir = Split-Path (Resolve-Path $In) -Parent }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

$verdict = ([string](Val $run 'verdict')).ToUpper(); if (-not $verdict) { $verdict = 'UNKNOWN' }
$vColor = @{ PASS = '#107c10'; FAIL = '#d13438'; BLOCKED = '#c19c00'; PARTIAL = '#c19c00'; UNKNOWN = '#605e5c' }
$vc = $vColor[$verdict]; if (-not $vc) { $vc = '#605e5c' }

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
    if (Val $ado 'configuration') { $adoLine += "Config: $(Val $ado 'configuration')" }
    if (Val $ado 'buildSource') { $adoLine += "Build: $(Val $ado 'buildSource')" }
    if ($adoLine.Count) { [void]$md.AppendLine("**ADO:** " + ($adoLine -join ' · ') + $(if (Val $ado 'url') { "  <$(Val $ado 'url')>" } else { '' })) }
    [void]$md.AppendLine("")
}
function MdMeta($label, $val) { if ($val) { [void]$md.AppendLine("- **${label}:** $val") } }
MdMeta 'Feature' (Val $run 'feature')
$dev = Val $run 'device'
if ($dev) { MdMeta 'Device' (@((Val $dev 'model'), (Val $dev 'os'), (Val $dev 'type'), (Val $dev 'resolution') | Where-Object { $_ }) -join ' · ') ; MdMeta 'Serial' (Val $dev 'serial') }
$app = Val $run 'app'
if ($app) { MdMeta 'App' (@((Val $app 'package'), (Val $app 'version') | Where-Object { $_ }) -join ' v') }
$acct = Val $run 'account'
if ($acct) { MdMeta 'Account' (@((Val $acct 'upn'), (Val $acct 'usertype') | Where-Object { $_ }) -join '  ·  ') }
MdMeta 'Started' (Val $run 'started'); MdMeta 'Finished' (Val $run 'finished'); MdMeta 'Iterations' (Val $run 'iterations')
[void]$md.AppendLine("")
$steps = Val $run 'steps'
if ($steps) {
    [void]$md.AppendLine("## Steps")
    [void]$md.AppendLine("")
    [void]$md.AppendLine("| # | Action | Expected | Result | Notes |")
    [void]$md.AppendLine("|---|--------|----------|--------|-------|")
    foreach ($s in $steps) {
        $n = Val $s 'n'; $a = (Val $s 'action') -replace '\|', '\|'; $e = (Val $s 'expected') -replace '\|', '\|'
        $r = Val $s 'result'; $no = (Val $s 'notes') -replace '\|', '\|'
        [void]$md.AppendLine("| $n | $a | $e | $r | $no |")
    }
    [void]$md.AppendLine("")
}
function MdList($label, $items) {
    if ($items) { [void]$md.AppendLine("## $label"); [void]$md.AppendLine(""); foreach ($i in $items) { [void]$md.AppendLine("- $i") }; [void]$md.AppendLine("") }
}
MdList 'Evidence' (Val $run 'evidence')
MdList 'Blockers' (Val $run 'blockers')
MdList 'Artifacts' (Val $run 'artifacts')
$mdPath = Join-Path $OutDir 'TestReport.md'
$md.ToString() | Out-File -FilePath $mdPath -Encoding utf8

# ---------- HTML ----------
$rows = ''
if ($steps) {
    foreach ($s in $steps) {
        $rc = @{ PASS = '#107c10'; FAIL = '#d13438'; BLOCKED = '#c19c00'; PARTIAL = '#c19c00' }[([string](Val $s 'result')).ToUpper()]
        if (-not $rc) { $rc = '#605e5c' }
        $shot = Val $s 'screenshot'
        $shotCell = if ($shot) { "<a href='$(He $shot)'>$(He (Split-Path $shot -Leaf))</a>" } else { '' }
        $rows += "<tr><td>$(He (Val $s 'n'))</td><td>$(He (Val $s 'action'))</td><td>$(He (Val $s 'expected'))</td>" +
        "<td><b style='color:$rc'>$(He (Val $s 'result'))</b></td><td>$(He (Val $s 'notes'))</td><td>$shotCell</td></tr>"
    }
}
function HtmlList($items) { if (-not $items) { return '' } $li = ($items | ForEach-Object { "<li>$(He $_)</li>" }) -join ''; return "<ul>$li</ul>" }
function MetaRow($label, $val) { if ($val) { return "<tr><th>$(He $label)</th><td>$(He $val)</td></tr>" } return '' }

$adoRow = ''
if ($ado) {
    $bits = @()
    if (Val $ado 'testCaseId') { $bits += "Test Case #$(Val $ado 'testCaseId')" }
    if (Val $ado 'planId') { $bits += "Plan $(Val $ado 'planId')" }
    if (Val $ado 'suiteId') { $bits += "Suite $(Val $ado 'suiteId')" }
    $txt = $bits -join ' · '
    if (Val $ado 'url') { $txt = "<a href='$(He (Val $ado 'url'))'>$(He $txt)</a>" } else { $txt = He $txt }
    $cfgBits = @()
    if (Val $ado 'configuration') { $cfgBits += "Config: $(Val $ado 'configuration')" }
    if (Val $ado 'buildSource') { $cfgBits += "Build: $(Val $ado 'buildSource')" }
    if ($cfgBits) { $txt = (@($txt, (He ($cfgBits -join ' · '))) | Where-Object { $_ }) -join ' · ' }
    $adoRow = "<tr><th>ADO</th><td>$txt</td></tr>"
}
$devTxt = if ($dev) { (@((Val $dev 'model'), (Val $dev 'os'), (Val $dev 'type'), (Val $dev 'resolution') | Where-Object { $_ }) -join ' · ') } else { '' }
$appTxt = if ($app) { (@((Val $app 'package'), (Val $app 'version') | Where-Object { $_ }) -join ' v') } else { '' }
$acctTxt = if ($acct) { (@((Val $acct 'upn'), (Val $acct 'usertype'), (Val $acct 'tenant') | Where-Object { $_ }) -join '  ·  ') } else { '' }

$html = @"
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>E2E Test Report — $(He (Val $run 'title'))</title>
<style>
 body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#faf9f8;color:#201f1e}
 .wrap{max-width:1000px;margin:0 auto;padding:24px}
 h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:24px 0 8px;border-bottom:1px solid #edebe9;padding-bottom:4px}
 .verdict{display:inline-block;color:#fff;background:$vc;font-weight:700;padding:6px 14px;border-radius:14px;font-size:14px}
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
<h2>Run details</h2>
<table class="meta">
$adoRow
$(MetaRow 'Feature' (Val $run 'feature'))
$(MetaRow 'Device' $devTxt)
$(MetaRow 'Serial' $(if($dev){Val $dev 'serial'}))
$(MetaRow 'App' $appTxt)
$(MetaRow 'Account' $acctTxt)
$(MetaRow 'Started' (Val $run 'started'))
$(MetaRow 'Finished' (Val $run 'finished'))
$(MetaRow 'Iterations' (Val $run 'iterations'))
</table>
$(if($rows){"<h2>Steps</h2><table><thead><tr><th>#</th><th>Action</th><th>Expected</th><th>Result</th><th>Notes</th><th>Screenshot</th></tr></thead><tbody>$rows</tbody></table>"})
$(if(Val $run 'evidence'){"<h2>Evidence</h2>$(HtmlList (Val $run 'evidence'))"})
$(if(Val $run 'blockers'){"<h2>Blockers</h2>$(HtmlList (Val $run 'blockers'))"})
$(if(Val $run 'artifacts'){"<h2>Artifacts</h2>$(HtmlList (Val $run 'artifacts'))"})
<p class="foot">Generated by the android-e2e-tester skill · $(Get-Date -Format 'yyyy-MM-dd HH:mm')</p>
</div></body></html>
"@
$htmlPath = Join-Path $OutDir 'TestReport.html'
$html | Out-File -FilePath $htmlPath -Encoding utf8

Write-Host "Wrote:"
Write-Host "  $htmlPath"
Write-Host "  $mdPath"
Write-Host "Verdict: $verdict"
