<#
.SYNOPSIS
    Direct-REST Kusto query helper. Drop-in fallback for the Azure Kusto MCP server
    when the MCP times out (the MCP has a 240 s budget and frequently exceeds it on
    the per-error-code queries this skill needs).

.DESCRIPTION
    Acquires an Entra token via the local `az` CLI for the Kusto cluster, POSTs the
    query to /v2/rest/query, and writes a JSON file whose schema matches what the
    other helpers in this skill (bucket-trends.js, summarize-attribution.js) expect:

        { "results": { "items": [
            [colName0, colName1, ...],     // first row = column-name list
            [row0col0, row0col1, ...],
            [row1col0, row1col1, ...],
            ...
        ] } }

    The `summarize-attribution.js --union` loader will auto-detect this array-form
    schema (since the v8 update) — no transformer step needed.

.PARAMETER Query
    KQL query text. Pass via single-quoted PowerShell here-string for safety.

.PARAMETER Out
    Output JSON file path.

.PARAMETER App
    Convenience selector for the cluster/database pair: 'broker' (default) or
    'authapp'. The two reports read completely different clusters AND different
    databases; getting one right and the other wrong returns an empty result set
    rather than an error, so prefer -App over passing -Cluster/-Database by hand.
    Explicit -Cluster / -Database always win if supplied.

      broker  -> https://idsharedeus2.kusto.windows.net / ad-accounts-android-otel
      authapp -> https://idsharedeus2.eastus2.kusto.windows.net / d496be22d62a46b0a3cf67ea2e736fd8

.PARAMETER Cluster
    Kusto cluster URI. Overrides the -App default.

.PARAMETER Database
    Database name. Overrides the -App default.

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

.EXAMPLE
    # Authenticator: scenario outcomes WoW
    .\run-kql.ps1 -App authapp -Query (Get-Content ..\queries\authapp\scenario-outcomes-wow.kql -Raw) -Out outcomes.json

.NOTES
    * Requires `az login` to have been run beforehand and the caller to have read
      access to the cluster (Android Auth Client SDK security group).
    * Runs queries in parallel from PowerShell jobs — see SKILL.md Step 2 for the
      "5-queries-in-parallel" pattern.
    * If your query payload is large (>50 KB returned), the JSON file may itself
      be large — pipe to bucket-trends.js / summarize-attribution.js directly
      rather than viewing in-band.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Query,
  [Parameter(Mandatory=$true)][string]$Out,
  [ValidateSet('broker','authapp')]
  [string]$App = 'broker',
  [string]$Cluster,
  [string]$Database,
  [int]$TimeoutSec = 300
)
$ErrorActionPreference = 'Stop'

# Per-app cluster/database defaults. Explicit -Cluster/-Database win.
$endpoints = @{
  broker  = @{ Cluster = 'https://idsharedeus2.kusto.windows.net';         Database = 'ad-accounts-android-otel' }
  authapp = @{ Cluster = 'https://idsharedeus2.eastus2.kusto.windows.net'; Database = 'd496be22d62a46b0a3cf67ea2e736fd8' }
}
if (-not $Cluster)  { $Cluster  = $endpoints[$App].Cluster }
if (-not $Database) { $Database = $endpoints[$App].Database }
Write-Verbose "Querying $Cluster / $Database (-App $App)"

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
if ($primary.Rows.Count -eq 0) {
  # Zero rows is the signature of a wrong time column, wrong database, or a
  # window that lands outside retention -- Kusto returns success either way.
  Write-Warning "Query returned 0 rows from $Cluster / $Database. Verify the time column (broker: EventInfo_Time; authapp MVs: EventDate; brokeroperations: PipelineInfo_IngestionTime) and that -App matches the views you referenced."
}
