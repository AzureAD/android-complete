# Copyright (c) Microsoft Corporation. All rights reserved.
<#
.SYNOPSIS
    Render a standard E2E test report (TestReport.html + TestReport.md) from a run-metadata JSON file.
    For ADO test cases this report is MANDATORY (Phase 7) — generate it on every outcome (PASS / FAIL /
    BLOCKED / PARTIAL), not only on success.

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
      "ado": { "testCaseId": 1579381, "planId": 714514, "suiteId": 3503165, "url": "https://..." },
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
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][ValidateSet('render')][string]$Command = 'render',
    [Parameter(Mandatory)][string]$In,
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path $In)) { throw "Run JSON not found: $In" }
$run = Get-Content $In -Raw | ConvertFrom-Json
if (-not $OutDir) { $OutDir = Split-Path (Resolve-Path $In) -Parent }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

function Val($o, $name) { if ($o -and ($o.PSObject.Properties.Name -contains $name)) { return $o.$name } return $null }
function He([string]$s) { if ($null -eq $s) { return '' } [System.Net.WebUtility]::HtmlEncode([string]$s) }
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
