# Report Integration

How to surface the coverage trend to the team. Three cases, in priority order:
1. The team already has a recurring report → **inject** the coverage section into it.
2. The team has a dashboard tool → point it at the Kusto table.
3. The team has nothing → **suggest the email approach** below.

## Table of contents
- [The `wow` fragment](#the-wow-fragment)
- [Case 1: Inject into an existing report](#case-1-inject-into-an-existing-report)
- [Case 2: Dashboard on Kusto](#case-2-dashboard-on-kusto)
- [Case 3: Email via Azure Communication Services](#case-3-email-via-azure-communication-services)
- [Splitting into grouped tables](#splitting-into-grouped-tables)

## The `wow` fragment
`coverage_report.py wow` queries the Kusto table and prints an **HTML fragment** (a heading +
one or more `<table>`s, each with an Overall row and week-over-week deltas). It writes the same
HTML to `--out-html` when given. It is intentionally self-contained HTML so it can be appended
to almost any report body.

```bash
export KUSTO_TOKEN=$(az account get-access-token --resource https://kusto.kusto.windows.net --query accessToken -o tsv)
python3 scripts/coverage_report.py wow \
  --cluster "https://<cluster>.kusto.windows.net" \
  --db "<DatabaseName>" \
  --table CodeCoverageData \
  --out-html coverage-wow.html
```

The command is **non-fatal**: if the token is missing or the query fails, it emits a short
"coverage unavailable" note instead of failing, so the surrounding report still ships.

## Case 1: Inject into an existing report
If a scheduled job already builds a report body (text/HTML file), append the fragment before
the send step:

```bash
python3 scripts/coverage_report.py wow --cluster ... --db ... --out-html cov.html
if [ -f cov.html ] && [ -f report.html ]; then
  cat cov.html >> report.html
fi
```
This is exactly how the Android Auth weekly report does it — the coverage section is generated
after the main report is assembled and concatenated onto the report body that gets emailed.

## Case 2: Dashboard on Kusto
Once rows are in Kusto, any of these can render the trend without the email path:
- **Azure Data Explorer dashboards** (native, fastest to stand up).
- **Grafana** with the ADX data source.
- **Power BI** with the Kusto/ADX connector.

Pin a tile using the "latest per module" or the `_wow_kql` query. This is the lowest-
maintenance option for teams that live in dashboards.

## Case 3: Email via Azure Communication Services
For teams with no existing report, recommend **Azure Communication Services (ACS) Email** — it
sends from a verified domain with just a REST call and an access key, no mail server.

Setup:
1. Create an **ACS resource** and an **Email Communication Service**; verify a sender domain
   (Azure-managed subdomain is fastest for internal use).
2. Connect the domain to the ACS resource; note the **ACS endpoint** and an **access key**
   (store the key in a pipeline secret / Key Vault — never in YAML).
3. Send the assembled HTML body (with the coverage fragment appended) via the ACS Email REST
   API `POST {endpoint}/emails:send`, or the `Azure.Communication.Email` SDK.

Keep the send step separate from coverage generation so a coverage failure never blocks the
email. Recipients should be a team distribution list.

## Splitting into grouped tables
By default `wow` renders one table ("All Modules"). To split modules into labeled tables
(each with its own Overall row) — e.g. separating two product areas — pass a `--group-file`
JSON mapping table title → module list:

```json
{
  "Android Broker Modules": ["msal", "common", "common4j", "broker4j", "AADAuthenticator"],
  "Authenticator Modules": ["AuthApp", "MfaLibrary"]
}
```
```bash
python3 scripts/coverage_report.py wow --cluster ... --db ... \
  --group-file groups.json --out-html coverage-wow.html
```
Modules not listed in any group fall into a trailing catch-all table (rename via
`--other-title`, default "Other Modules"). Each table's **Overall** row sums its own
Covered/Total and shows an aggregate WoW delta computed from summed prior Covered/Missed
(not an average of per-module percentages).
