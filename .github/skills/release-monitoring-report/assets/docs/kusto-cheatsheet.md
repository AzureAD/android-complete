# Kusto operational runbook (release monitoring)

How to actually *run* the queries and feed the results into the report. The per-query
purpose + scenario→MV→column catalog lives in [`../queries/README.md`](../queries/README.md);
this file is the mechanics.

## Table of contents
- [Auth + prerequisites](#auth--prerequisites)
- [Running a query (run-kql.ps1)](#running-a-query-run-kqlps1)
- [The output JSON shape](#the-output-json-shape)
- [Filling tokens in a .kql before running](#filling-tokens-in-a-kql-before-running)
- [End-to-end loop](#end-to-end-loop)
- [Version resolution recipes](#version-resolution-recipes)
- [Hard gotchas](#hard-gotchas)

## Auth + prerequisites
- `az login` must be current (`az account show`). Kusto access is via the caller's Entra token.
- Node (for `compare-versions.js`) — `node -v`. Python is NOT assumed present.
- Both clusters are read with the **same** `run-kql.ps1`; only `-Cluster`/`-Database` differ.

## Running a query (run-kql.ps1)
`run-kql.ps1` defaults to the **Broker** cluster/db. Pipe or pass a query; capture JSON.

```powershell
$S = ".github/skills/release-monitoring-report/assets/scripts"

# Broker (defaults)
$q = Get-Content "$Q\broker-adoption.kql" -Raw
& "$S\run-kql.ps1" -Query $q -Out "$DATA\broker-adoption.json"

# Authenticator (override cluster + db)
& "$S\run-kql.ps1" -Query $q -Out "$DATA\auth-mfa-pn.json" `
    -Cluster https://idsharedeus2.eastus2.kusto.windows.net `
    -Database d496be22d62a46b0a3cf67ea2e736fd8
```

`run-kql.ps1` writes the JSON to the **mandatory `-Out` path** (it does not stream to stdout).
`$DATA` = the `_data/<slug>-<date>` folder `bootstrap-report.ps1` created. Keep raw payloads
there so the report is reproducible.

## The output JSON shape
`run-kql.ps1` emits **array-form**, first row = column names:

```json
{ "results": { "items": [ ["broker_version","devices"], ["16.1.0", 76400000], ["16.0.1", 585300000] ] } }
```

`compare-versions.js` reads exactly this. Do not reshape it.

## Filling tokens in a .kql before running
Templates carry `<TOKENS>` (see queries/README → token convention). Substitute in PowerShell:

```powershell
$q = (Get-Content "$Q\broker-top-errors-by-version.kql" -Raw).
       Replace('<FIRST>','16.1.0').Replace('<SECOND>','16.0.1').
       Replace('<START>','2026-06-01').Replace('<END>','2026-06-15')
```

For Broker `<VERSIONS>` (multi-version filter) substitute a dynamic literal:
`.Replace('<VERSIONS>','dynamic(["16.1.0","16.0.1","16.2.0"])')`.
For `<DCOUNT>` use `true` (distinct-device columns) or `false` (raw counts).

## End-to-end loop
1. **Resolve versions** — run `broker-adoption.kql` and the cheap Authenticator resolver
   ([recipe below](#version-resolution-recipes)). Pick `<FIRST>` (rolling out) and `<SECOND>`
   (previous, by volume) unless the user named them. "Baseline = all versions" → omit the
   version filter / pass every version in `<VERSIONS>`.
2. **Pull** each query for both apps into `$DATA\*.json`.
3. **Compare** — feed the version-per-row payloads to `compare-versions.js rows`, and the
   error-movers payload to `compare-versions.js movers --lower-is-better true` (error-share
   growth is bad). See script header for flags.
4. **Fill** the bootstrapped HTML in place with the real numbers + the verdict the deltas imply.
5. **Validate** — `validate-report.ps1 -Path <file> -BrokerVersion <bv> -AuthVersion <av>`.

## Version resolution recipes
**Authenticator (cheap, validated)** — avoid `union *`; read a high-volume MV:
```kusto
Entra_MFA_Push_Notification_And_CheckForAuth_MV_V1
| where EventDate >= datetime(<START>) and EventDate <= datetime(<END>)
| where isnotempty(AppVersion)
| summarize Devices = sum(NotificationInitiatedDCount) by AppVersion
| order by Devices desc
```
Newest `6.YYMM.BUILD` (highest `YYMM`) = current train. The two highest-volume recent
versions are usually `<FIRST>`/`<SECOND>`.

**Broker** — `broker-adoption.kql` already returns `dcount` devices by `broker_version`;
sort desc and read off the top two.

## Hard gotchas
- **Distinct devices (Broker):** `dcount_hll(hll_merge(countDevicesHll))`. Never `sum(countDevices)`.
- **Percentiles (Broker):** `percentiles_array_tdigest(tdigest_merge(responseTimeTDigest), …)`.
  Never average/sum percentiles across rows.
- **Raw dims (Broker):** wrap with `MergeAccountType()` / `MergeIsSharedDevice()` if you add
  account-type / shared-device filters.
- **Broker per host app:** Broker MVs (`ErrorStatsMetrics`, `*AuthStats*Metrics`,
  `BrokerAdoptionStatsUpdated`) carry `active_broker_package_name` (the host app acting as broker)
  and `AppInfo_Version`. For a given host package, `AppInfo_Version` **is that host app's version**
  — e.g. for `com.azure.authenticator` it equals the Authenticator `AppVersion` (`6.2606.3817`),
  NOT `broker_version`. To isolate "the broker as it runs inside one app's release", filter
  `active_broker_package_name == "<pkg>"` and compare by `AppInfo_Version`. Don't attribute a
  fleet-wide `broker_version` delta to an app — it can be dominated by another host (Link to
  Windows `com.microsoft.appmanager` ≈122 M devices).
- **Device-share masks per-span spikes:** `broker-top-errors-by-host-app.kql` is a device-share
  (devices hitting code X anywhere ÷ devices on that version) — it dedups a device across all spans.
  A code can read flat/down there while its **per-request** rate climbs inside one `span_name` (seen:
  `invalid_grant` on `AcquireTokenSilent` rose +1.19 pp while its device-share fell). Re-slice with
  `broker-errors-by-host-app-span.kql` (request-level rate per span) before writing "no regression",
  and separate an early-rollout spike-that-decays from a steady gap with a daily trend (rate by
  version by day). For eSTS-returned codes (`invalid_grant`/`interaction_required`) correlate the
  trigger to a PR in the bundled broker version range: `git log v<PREV>..v<NEW>` in `broker/`+`common/`,
  then `find-suspect-prs.ps1 -Range`; weight device-PoP/PRT/cache changes.
- **Authenticator outcomes:** Registration/Auth MVs have only `Initiated/Succeeded/Failed`
  (+`…DCount`) — no `Cancelled`/`PartiallySucceeded`. PN completion needs the two-table join
  (init MV ⋈ `_Results_MV_V1`).
- **MSA NGC vs SA:** both the MSA PN init MV and its results MV carry `IsNGC`
  (`"true"`=NGC, `"false"`=SA) — filter both join sides.
- **Volume guard:** treat scenarios with < ~1K initiates as noise, not a regression
  (`compare-versions.js` `--volume-floor`). Always pull initiate volume alongside rates.
- **A moved metric is a question, not a verdict:** before calling any version-over-version delta a
  regression, run the diagnostic ladder in [`investigation-patterns.md`](investigation-patterns.md) —
  normalize count→rate, compare new-build vs old-build rate (substitution), the **code-frozen control**
  (did the previous version's rate move too → environmental), dimensional decomposition via the
  `*_Errors_MV_V1` companions, benign-vs-defect classification, raw `passkeyoperations` sub-code drill,
  and the `git diff <prevTag>..<newTag>` gate-logic check.
- **Know what an MV counts before drilling:** `.show materialized-view <Name> | project Query` prints
  its source table + `OperationName`/`RequestType`/`PasskeyFlow` filters — so you drill the right raw
  request family. Every Authenticator scenario also has a `*_Errors_MV_V1` companion (reason × OsLevel
  × AppVersion × DeviceInfoMake) for the "why".
- **UTF-8 trap:** never write report HTML through a PowerShell `@'…'@` heredoc (strips
  emoji/arrows). Use `node fs.writeFileSync` or
  `[IO.File]::WriteAllText($p,$t,[System.Text.UTF8Encoding]::new($false))`.
