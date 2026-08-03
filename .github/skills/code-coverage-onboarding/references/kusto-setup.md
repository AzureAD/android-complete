# Kusto Ingestion & Long-Term Tracking

Store normalized coverage rows in a Kusto (Azure Data Explorer) table so coverage can be
tracked week-over-week. This is **on by default** in the recommended setup, but is optional —
a team that only wants the per-run summary artifact can skip it.

## Table of contents
- [Table schema](#table-schema)
- [Creating the table](#creating-the-table)
- [Authentication (WIF / Managed Identity)](#authentication-wif--managed-identity)
- [Granting ingestor permission](#granting-ingestor-permission)
- [Ingesting rows](#ingesting-rows)
- [Cost control: only ingest on a schedule](#cost-control-only-ingest-on-a-schedule)
- [Querying the data](#querying-the-data)

## Table schema
One row per module + metric + run. Matches the NDJSON emitted by `coverage_report.py parse`:

```kusto
.create table CodeCoverageData (
    Date: datetime,        // run date (UTC)
    Repo: string,          // owning repository
    Module: string,        // module / project name
    Metric: string,        // LINE or BRANCH
    Covered: long,         // covered units
    Missed: long,          // missed units
    Percentage: real,      // Covered / (Covered + Missed) * 100
    CommitId: string,
    BuildId: string,
    Branch: string
)
```

Keep the column names exactly as above — the `wow` KQL and the ingest CSV ordering depend on them.

## Creating the table
Run the `.create table` command above in the target database (via the Kusto MCP
`kusto_command`, the Azure Data Explorer web UI, or `az kusto`). Pick a database your team
already owns; a shared telemetry cluster is fine.

## Authentication (WIF / Managed Identity)
CI should authenticate with a **workload-identity federation (WIF) service connection** or a
managed identity — never a stored secret. In Azure DevOps this is an `AzureCLI@2` task with an
`azureSubscription` that points at a WIF service connection; `az account get-access-token`
then yields a bearer token scoped to Kusto.

```bash
token=$(az account get-access-token --resource "https://kusto.kusto.windows.net" \
        --query accessToken -o tsv)
```

In GitHub Actions, use `azure/login@v2` with OIDC (`permissions: id-token: write`) and the
same `az account get-access-token` call.

## Granting ingestor permission
The identity behind the service connection needs **Database Ingestor** (and **Viewer** to
query) on the target database. Run once, as a database admin:

```kusto
.add database <DatabaseName> ingestors ('aadapp=<app-or-mi-client-id>;<tenant-id>') 'Code coverage ingest'
.add database <DatabaseName> viewers   ('aadapp=<app-or-mi-client-id>;<tenant-id>') 'Code coverage read'
```

If ingestion returns 403/Forbidden, this grant is almost always the missing step.

## Ingesting rows
Convert NDJSON → CSV (fixed column order) and POST an inline `.ingest` management command:

```bash
csv=$(python3 -c 'import json,sys; cols=["Date","Repo","Module","Metric","Covered","Missed","Percentage","CommitId","BuildId","Branch"]; print("\n".join(",".join(str(json.loads(l)[c]) for c in cols) for l in open(sys.argv[1]) if l.strip()))' coverage-all.ndjson)
token=$(az account get-access-token --resource "https://kusto.kusto.windows.net" --query accessToken -o tsv)
body=$(python3 -c 'import json,sys; print(json.dumps({"db":sys.argv[1],"csl":".ingest inline into table "+sys.argv[2]+" <|\n"+sys.argv[3]}))' "<DatabaseName>" "CodeCoverageData" "$csv")
curl -sS -X POST "https://<cluster>.kusto.windows.net/v1/rest/mgmt" \
  -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d "$body"
```

Inline ingest is fine for the small row counts here (a handful of modules per run). For large
volumes use queued ingestion instead.

**Fail the stage on ingest failure** if the data matters for tracking — don't mark the run
green when nothing was ingested. (Reporting/rendering steps, by contrast, should be non-fatal
so the email still sends.)

## Cost control: only ingest on a schedule
Ingesting on every CI run wastes storage and money. Gate ingestion so it only runs on the
**scheduled** reporting cadence (e.g. weekly on Sunday), with an operator override for manual
validation. Since a cron may fire daily, check the day at runtime:

```bash
FORCE="${FORCE_INGEST:-false}"          # pipeline parameter, default false
if [ "$FORCE" != "true" ]; then
  if [ "$BUILD_REASON" != "Schedule" ] || [ "$(date -u +%u)" != "7" ]; then
    echo "Skipping Kusto ingestion (not a Sunday scheduled run). Set FORCE_INGEST=true to override."
    exit 0
  fi
fi
# ... proceed with ingest ...
```
`date -u +%u` returns 1=Mon … 7=Sun (UTC). Adjust to your reporting day. The parse/publish
steps can still run every build (they're cheap and give per-PR visibility); only the **ingest**
is gated.

## Querying the data
Latest coverage per module:
```kusto
CodeCoverageData
| where Metric == "LINE"
| summarize arg_max(Date, Percentage, Covered, Missed) by Repo, Module
| order by Repo asc, Module asc
```
The `wow` subcommand runs a richer version of this (see `scripts/coverage_report.py`
`_wow_kql`) that also computes the week-over-week delta with calendar (start-of-week) semantics.
