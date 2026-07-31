# Authenticator Kusto cheatsheet

Everything needed to write correct Authenticator KQL for the weekly OCE report.
Companion to [`kusto-cheatsheet.md`](kusto-cheatsheet.md), which covers the **Broker** side.

> ## ⚠️ Read this first: Broker conventions do not transfer
>
> The two apps share a report skill, a report look, and nothing else in their data model.
> Every one of the Broker's headline hard rules is **wrong** here:
>
> | Broker rule | Authenticator reality |
> |---|---|
> | Never `sum(countDevices)` — always `dcount_hll(hll_merge(countDevicesHll))` | There are no HLL columns. Distinct devices are **pre-computed**: `sum(SucceededDCount)` is correct and is what the dashboard does. |
> | Never sum percentiles — use `percentile_tdigest(tdigest_merge(...))` | There are no TDigest sketches. The only latency source is the raw `brokeroperations` table, where plain `percentile()` is correct. |
> | Always apply `MergeAccountType` / `MergeIsSharedDevice` / `MergeUiRequiredExceptions` | These helper functions do not exist in this database. |
> | Time column is `EventInfo_Time` | Materialized views use **`EventDate`**. Raw tables use `EventInfo_Time` — except `brokeroperations`, which the dashboard filters on **`PipelineInfo_IngestionTime`**. |
> | Wrap views in `materialized_view('Xxx')` | Authenticator MVs are referenced by **bare name**: `Passkey_WebAuthN_Registration_MV_V1`. |
> | Unit of analysis is a flat `error_code` / `error_type` table | Unit of analysis is a **scenario funnel**: Initiated → Succeeded / Failed / Unknown. |
> | 7 slicing dimensions | **3** dimensions: `AppVersion`, `OsLevel`, `DeviceInfoMake`. |
>
> If you find yourself pattern-matching a Broker query into this database, stop and re-read.

---

## 1. Connection

| | |
|---|---|
| Cluster | `https://idsharedeus2.eastus2.kusto.windows.net` |
| Database | `d496be22d62a46b0a3cf67ea2e736fd8` |
| Auth | `az login` (same tenant as Broker) |

Via the fallback REST helper:

```pwsh
.\assets\scripts\run-kql.ps1 -App authapp -QueryFile .\assets\queries\authapp\scenario-outcomes-wow.kql -OutFile $data\scenarios.json
# equivalent to:
# -Cluster https://idsharedeus2.eastus2.kusto.windows.net -Database d496be22d62a46b0a3cf67ea2e736fd8
```

Output shape is the array form: `{ "results": { "items": [ [colNames…], [row…], … ] } }` — the
same shape `bucket-trends.js` and `agg.js` already parse.

---

## 2. The scenario → MV → column catalog

Outcome columns each have a `…DCount` distinct-device twin.
**Registration / Authentication MVs expose only `Initiated / Succeeded / Failed` (+`DCount`) and
`TotalUniqueDevices`. There is NO `Cancelled` and NO `PartiallySucceeded` column** — do not
invent one. PN MVs carry only an initiated counter; the terminal outcome lives in the paired
`_Results_MV_V1`.

| Scenario | Outcome MV | Initiate column |
|---|---|---|
| Passkey WebAuthN Registration | `Passkey_WebAuthN_Registration_MV_V1` | `Initiated` |
| Passkey InApp Registration | `Passkey_InApp_Registration_MV_V1` | `Initiated` |
| Passkey WebAuthN Authentication | `Passkey_WebAuthN_Authentication_MV_V1` | `Initiated` |
| Entra MFA Registration (QR) | `Entra_MFA_Registration_QR_Code_Flow_MV_V1` | `Initiated` |
| Entra MFA Registration (No-QR) | `Entra_MFA_Registration_Manual_Flow_MV_V1` **∪** `Entra_MFA_Registration_Non_QR_Code_Flow_MV_V1` | `Initiated` |
| Entra PSI Registration | `Entra_PSI_Registration_MV_V1` | `Initiated` |
| Entra PSI PN Registration | `Entra_PSI_Push_Notification_Registration_MV_V1` | **`RegistrationStarted`** |
| MSA NGC Registration | `Entra_MSA_NGC_Registration_MV_V1` | `Initiated` |
| MSA SA Registration | `Entra_MSA_SA_Registration_MV_V1` | `Initiated` |

Push-notification families (two-stage, no Succeeded/Failed on the init MV):

| Family | PN init MV | init column | PN results MV (`FinalResult`) | reacted column |
|---|---|---|---|---|
| Entra MFA PN+CFA | `Entra_MFA_Push_Notification_And_CheckForAuth_MV_V1` | `NotificationInitiated` | `Entra_MFA_Push_Notification_And_CheckForAuth_Results_MV_V1` | `RequestTimeInitiated` |
| Entra PSI PN+CFA | `Entra_PSI_Push_Notification_And_CheckForAuth_MV_V1` | `NotificationInitiated` | `Entra_PSI_Push_Notification_And_CheckForAuth_Results_MV_V1` | `RequestTimeInitiated` |
| MSA NGC PN+CFA | `Entra_MSA_Push_Notification_And_CheckForAuth_MV_V1` | `NotificationReceivedInitiated` | `Entra_MSA_Push_Notification_And_CheckForAuth_Results_MV_V1` | `SessionTimeInitiated` |
| MSA SA PN+CFA | same as NGC | same | same | same |

**MSA NGC vs SA split:** both the init MV and the results MV carry `IsNGC`
(`"true"` → NGC, `"false"` → SA). **Apply the filter on BOTH sides of the join** — filtering only
one side silently mixes the two populations and the funnel stops reconciling.

`FinalResult ∈ {Approved, Denied, Error}`. Completion = (Approved + Denied) ÷ initiated.
Approved / Denied / Error percentages are shares of the **reacted** total, not of initiated.

---

## 3. "Unknown" is a real metric, not a rounding error

```
Unknown = max(0, Initiated − (Succeeded + Failed))
```

A session that started and never produced a terminal result in the window. Causes range from
genuine user abandonment (walked away from the biometric prompt) to the app being killed, to a
result landing after the window closed.

This is the **single most Authenticator-specific signal in the report** and it has no Broker
analogue. Report it as its own rate. A scenario whose success rate is flat while Unknown climbs
is degrading — the failures just are not being recorded as failures.

Caveat to state in the report: a small Unknown floor is expected from window-edge truncation.
Only a *change* in the Unknown rate is a finding.

---

## 4. The Errors companion views

Named by inserting `Errors` before `_MV_V1`:

```
Passkey_WebAuthN_Registration_MV_V1               -> Passkey_WebAuthN_Registration_Errors_MV_V1
Entra_MFA_Push_Notification_And_CheckForAuth_MV_V1
                                                  -> Entra_MFA_Push_Notification_And_CheckForAuth_Errors_MV_V1
```

Uniform schema:

```
EventDate, Error, OsLevel, AppVersion, DeviceInfoMake, ErrorCount, ErrorDCount, TotalUniqueDevices
```

Some views also expose a pre-formatted `ErrorBeautified` — prefer it for display when present,
but group on `Error` so the grouping is stable across views.

**⚠️ Counts only — no denominator.** Always pair with the outcome MV's `Initiated`. An error
count that rose 30% alongside a 30% rise in initiates is traffic growth, not a regression. This
is the most common false positive on the Authenticator side.

---

## 5. Drilling below the Errors views

When `Error` is a coarse bucket, the raw `passkeyoperations` table has the finer code.

- `OperationName` — `PasskeyCredentialRequest{Initiated,Succeeded,Failed}` plus sub-operations
  like `PasskeyBeginGetCredential*`
- `AppInfo_Version`, `DeviceInfo_Make`, `DeviceInfo_Id`, `EventInfo_Time`
- `osLevel = tostring(split(DeviceInfo_OsVersion, " ")[0])`
- `AllProperties` is a **JSON string** — `todynamic()` it before indexing.

Useful `AllProperties` keys:

| Key | Notes |
|---|---|
| `RequestType` | `CreatePasskeyCredentialRequest` = registration · `GetPasskeyCredentialRequest` = authentication |
| `PasskeyFlow` | `WEB_AUTH_N_REGISTRATION` / `WEB_AUTH_N_AUTHENTICATION` / `IN_APP_REGISTRATION` |
| `Error`, `ErrorSource` | finer than the Errors MV bucket |
| `IsCrossDevice` | cross-device passkey flows behave differently — separate them |
| `DeviceUnauthenticatedErrorCode` | Android `BiometricPrompt` code. **5 / 10 / 13 / 14 = user abandonment** (cancel, timeout, negative button). **1 / 7 / 9 = device/hard error** (hw unavailable, lockout). Misreading abandonment as failure is the classic Passkey false alarm. |
| `DeviceUnauthenticatedErrorMessage`, `Source` | |

**Know what a metric counts before you drill:**

```kql
.show materialized-view Passkey_WebAuthN_Registration_MV_V1 | project Query
```

reveals the source table and the `OperationName` / `RequestType` / `PasskeyFlow` filters — e.g.
Registration MVs count only `CreatePasskeyCredentialRequest`, Authentication only
`GetPasskeyCredentialRequest`. Query the wrong request family in the raw table and the numbers
will not reconcile with the MV.

---

## 6. `brokeroperations` — the one raw table in the main path

The Broker-API responsiveness section reads it directly. Three traps:

1. **Time column is `PipelineInfo_IngestionTime`**, not `EventDate` and not `EventInfo_Time`.
   The wrong column returns an empty or badly skewed window with no error.
2. `BrokerApiName` and `BrokerApiElapsedTimeMs` are **inside** `AdditionalProperties` and must be
   extracted, not projected:
   ```kql
   | extend ApiName   = extract("BrokerApiName=([^,}]+)", 1, tostring(AdditionalProperties))
   | extend ElapsedMs = toint(extract("BrokerApiElapsedTimeMs=([0-9]+)", 1, tostring(AdditionalProperties)))
   ```
3. It is the **slowest query in the run**. Keep the window at 14 days and the projection narrow.
   If the Kusto MCP times out, fall back to `run-kql.ps1`.

Operation names: `BrokerApiCallInitiated` / `BrokerApiCallCompleted` / `BrokerApiCallFailed`.

**Cross-report rule:** a regression here is a *shared* finding. Check the companion Broker report
for the same window before attributing it to Authenticator, and say in the write-up which report
the evidence came from.

---

## 7. Cheap version resolution

Do **not** `union *` to find the live versions — it is enormous. Read the highest-volume MV:

```kql
Entra_MFA_Push_Notification_And_CheckForAuth_MV_V1
| where EventDate >= datetime(<CUR_START>) and EventDate < datetime(<CUR_END>)
| where isnotempty(AppVersion)
| summarize Devices = sum(NotificationInitiatedDCount) by AppVersion
| order by Devices desc
```

`AppVersion` looks like `6.2606.3817` — not the Broker's `16.1.0` shape.

**This is also the report's active-device proxy.** It counts devices that emitted at least one
Entra MFA push-notification event. Label it as a telemetry-active-device count. It is **not** a
product DAU and must not be printed as one.

---

## 8. Volume floor

Treat any scenario with **< ~1,000 initiates** in the window as noise. Rate swings on tiny
denominators are the leading source of false regressions in this report. Tag such rows
`low-volume` in the scoreboard and exclude them from the regression callout — but never delete
them from the scoreboard, because a scenario *dropping* into low-volume is itself a signal.

---

## 9. Crash and stability data is NOT in Kusto

Crash clusters live in **App Center only**. Use
[`../../release-monitoring-report/assets/scripts/fetch-appcenter-crashes.js`](../../../release-monitoring-report/assets/scripts/fetch-appcenter-crashes.js).
The crash section is **gated on the App Center token being available** — when it is not, render
the section's "Not collected this run" empty state rather than omitting the section or, worse,
estimating a crash rate from Kusto. There is no Kusto proxy for crash rate; do not invent one.

---

## 10. Weekly bucketing

`startofweek()` is **Sunday-aligned**, same as on the Broker side:
`startofweek('2026-05-09') == 2026-05-03T00:00:00Z`. Print the distinct week values from the
first weekly query of the run and eyeball them. Off-by-one-week is the most common silent error
in weekly-bucketed KQL and it survives every other check in the pipeline.

The 60-day trend deliberately **includes** the partial current week (it is the chart's final bar)
and excludes it from delta classification via
`bucket-trends.js --end=<startofweek(curEnd)> --include-partial-end`.
The 8-week sparkline series deliberately **excludes** it at the source. Both behaviours are
intentional and are not the same thing.
