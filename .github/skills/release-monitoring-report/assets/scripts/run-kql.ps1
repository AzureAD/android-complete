<#
.SYNOPSIS
    Direct-REST Kusto query helper. Drop-in fallback for the Azure Kusto MCP server
    when the MCP times out (the MCP has a 240 s budget and frequently exceeds it on
    the per-error-code queries this skill needs).

.DESCRIPTION
    Acquires an Entra token via the local `az` CLI for the Kusto cluster, POSTs the
    query to /v2/rest/query, and writes a JSON file whose schema matches what the
    other helpers in this skill (compare-versions.js) expect:

        { "results": { "items": [
            [colName0, colName1, ...],     // first row = column-name list
            [row0col0, row0col1, ...],
            [row1col0, row1col1, ...],
            ...
        ] } }

    `compare-versions.js` reads this array-form schema directly — no transformer step needed.

.PARAMETER Query
    KQL query text. Pass via single-quoted PowerShell here-string for safety.

.PARAMETER Out
    Output JSON file path.

.PARAMETER Cluster
    Kusto cluster URI (default: idsharedeus2 — the production Android Broker cluster).

.PARAMETER Database
    Database name (default: ad-accounts-android-otel).

.PARAMETER TimeoutSec
    HTTP timeout (default 300 s — Kusto itself has a 5-minute server-side query budget).

.EXAMPLE
    # Sanity check
    .\run-kql.ps1 -Query 'print x=1' -Out test.json

.EXAMPLE
    # Pull the 60-day per-error-code trend
    $q = @"
materialized_view('ErrorStatsMetrics')
| where EventInfo_Time between (datetime(2026-04-12) .. datetime(2026-06-07))
| where isnotempty(error_code) and error_code != 'success'
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time), error_code
| where week < datetime(2026-06-07)
| order by error_code asc, week asc
"@
    .\run-kql.ps1 -Query $q -Out 60d-codes.json

.NOTES
    * Requires `az login` to have been run beforehand and the caller to have read
      access to the cluster (Android Auth Client SDK security group).
    * Runs queries in parallel from PowerShell jobs — see SKILL.md for the
      "pull-many-in-parallel" pattern.
    * If your query payload is large (>50 KB returned), the JSON file may itself
      be large — pass it to compare-versions.js rather than viewing in-band.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Query,
  [Parameter(Mandatory=$true)][string]$Out,
  [string]$Cluster = 'https://idsharedeus2.kusto.windows.net',
  [string]$Database = 'ad-accounts-android-otel',
  [int]$TimeoutSec = 300
)
$ErrorActionPreference = 'Stop'

# Acquire token via az CLI (works for users + managed identity)
$tok = az account get-access-token --resource $Cluster --query accessToken -o tsv 2>$null
if (-not $tok) {
  throw "Failed to acquire token for $Cluster. Run 'az login' first and verify membership in the Android Auth Client SDK security group."
}

$body = @{ csl = $Query; db = $Database } | ConvertTo-Json -Compress
$resp = Invoke-RestMethod -Uri "$Cluster/v2/rest/query" -Method Post `
  -Headers @{ Authorization = "Bearer $tok"; 'Content-Type' = 'application/json' } `
  -Body $body -TimeoutSec $TimeoutSec

# Find the PrimaryResult table (Kusto returns multiple frame types; we want the data)
$primary = $resp | Where-Object { $_.FrameType -eq 'DataTable' -and $_.TableKind -eq 'PrimaryResult' } | Select-Object -First 1
if (-not $primary) {
  # Surface any error frames so the caller can see what went wrong
  $err = $resp | Where-Object { $_.FrameType -eq 'DataSetCompletion' -and $_.HasErrors } | Select-Object -First 1
  if ($err) { throw "Kusto query failed with errors. Full response:`n$($resp | ConvertTo-Json -Depth 6)" }
  throw 'No PrimaryResult table in response'
}

# Convert to the canonical schema the JS helpers expect
$colNames = @($primary.Columns | ForEach-Object { $_.ColumnName })
$items = New-Object System.Collections.ArrayList
[void]$items.Add($colNames)
foreach ($r in $primary.Rows) { [void]$items.Add($r) }

$obj = @{ results = @{ items = $items } }
# UTF-8 without BOM — keeps emoji/diacritic data clean for downstream consumption
[IO.File]::WriteAllText($Out, ($obj | ConvertTo-Json -Depth 12 -Compress), [System.Text.UTF8Encoding]::new($false))
Write-Host ("Saved {0} rows -> {1}" -f ($primary.Rows.Count), $Out)
