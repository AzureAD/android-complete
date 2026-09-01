# Release-monitoring query catalog

Templates for **version-over-version** release monitoring of the Android **Broker** and the
**Authenticator** app. Every `.kql` here is a placeholder template — substitute the
`<TOKENS>` before running. All were validated against live Kusto.

## Clusters

| App | Cluster | Database | Version dimension | Time column |
|-----|---------|----------|-------------------|-------------|
| Broker | `https://idsharedeus2.kusto.windows.net` | `ad-accounts-android-otel` | `broker_version` (e.g. `16.1.0`) | `EventInfo_Time` |
| Authenticator | `https://idsharedeus2.eastus2.kusto.windows.net` | `d496be22d62a46b0a3cf67ea2e736fd8` | `AppVersion` (e.g. `6.2606.3817`) | `EventDate` (MVs) / `EventInfo_Time` (raw `union *`) |

`run-kql.ps1` defaults to the Broker cluster/db. For Authenticator pass
`-Cluster https://idsharedeus2.eastus2.kusto.windows.net -Database d496be22d62a46b0a3cf67ea2e736fd8`.

## Shared token convention

`<FIRST>` = version **rolling out** · `<SECOND>` = **previous / baseline** version ·
`<START>` `<END>` = `yyyy-mm-dd` window bounds · `<DCOUNT>` = `true` → distinct-device
(`…DCount`) columns, `false` → raw event counts · `<VERSIONS>` = a `dynamic([...])` list
used by the Broker templates that filter many versions at once.

## Broker queries

| File | Purpose | Key tokens |
|------|---------|-----------|
| `broker-adoption.kql` | Distinct devices per `broker_version`. **Run first** to resolve exact version strings + pick `<FIRST>`/`<SECOND>` by volume. | `<START> <END>` |
| `broker-error-rate-by-version.kql` | Headline overall **device error rate** per version (devices hitting any non-success error ÷ total devices). | `<VERSIONS> <START> <END>` |
| `broker-reliability-by-version.kql` | Silent + Interactive reliability (request and device) per version from the canonical `*AllRequestsMetrics` / `*RequestsWithoutExpectedErrorMetrics` MVs. | `<VERSIONS> <START> <END>` |
| `broker-top-errors-by-version.kql` | **The "why".** Per-`error_code` device + request counts on `<FIRST>` vs `<SECOND>` with device-share delta (pp). Top regressions/improvements. | `<FIRST> <SECOND> <START> <END>` |
| `broker-latency-by-version.kql` | P50/P75/P90/P95/P99 of `responseTime` per version (optionally one `span_name`). | `<VERSIONS> <START> <END>` |
| `broker-by-host-app.kql` | **Broker scoped to ONE host app**, compared by that app's version. Headline device error rate + silent/interactive reliability for `active_broker_package_name == <PACKAGE>`, keyed on `AppInfo_Version` (= the host app's version). | `<PACKAGE> <FIRST> <SECOND> <START> <END>` |
| `broker-top-errors-by-host-app.kql` | The "why" for the host-scoped view: per-`error_code` device-share delta for one host app's two versions. | `<PACKAGE> <FIRST> <SECOND> <START> <END>` |
| `broker-errors-by-host-app-span.kql` | **Span drill-down — the complement of the device-share movers.** Per-`span_name` **request-level** rate (errored ÷ total in that span) for a specific code list, one host app, two versions. Surfaces a per-span spike that device-share dedup hides. | `<PACKAGE> <FIRST> <SECOND> <CODES> <START> <END>` |

**Broker attributed to a host app (e.g. Authenticator):** the Broker runs *inside* a host app,
and the Broker MVs also carry `active_broker_package_name` (the host) and `AppInfo_Version`
(which, for a given host package, **is that host app's version** — e.g. for
`com.azure.authenticator`, `AppInfo_Version == 6.2606.3817` is the Authenticator AppVersion, not
`broker_version`). Use the two `*-by-host-app.kql` templates to answer *"did the Authenticator
rollout move the broker?"* without contamination from other hosts. This matters because
fleet-wide `broker_version` deltas can be dominated by a host you are **not** shipping — e.g. Link
to Windows (`com.microsoft.appmanager`, ≈122 M devices) can swing an aggregate `io_error` figure
that has nothing to do with the Authenticator release. Top hosts by volume: `com.microsoft.appmanager`,
`com.azure.authenticator`, `com.microsoft.windowsintune.companyportal`.

**Broker gotchas:** distinct devices = `dcount_hll(hll_merge(countDevicesHll))` — never
`sum(countDevices)`. Never sum percentiles — `percentiles_array_tdigest(tdigest_merge(...))`.
`MergeAccountType()` / `MergeIsSharedDevice()` normalize the raw dimensions if you add filters.
**Device-share masks per-span request spikes:** `broker-top-errors-by-host-app.kql` dedups a device
across all spans, so a code can read flat/down there while its per-request rate climbs inside one
span (e.g. `invalid_grant` on `AcquireTokenSilent`). When an eSTS code is suspected, re-slice with
`broker-errors-by-host-app-span.kql` and separate early-rollout decay from a steady gap via a daily
trend before concluding. The trigger PR lives in the bundled broker version range — correlate with
`assets/scripts/find-suspect-prs.ps1 -Range v<PREV>..v<NEW>`.

## Authenticator queries

| File | Purpose | Applies to |
|------|---------|-----------|
| `auth-version-resolve.kql` | Resolve candidate `AppVersion`s (newest `yymm` = current train). Auto-detect `<FIRST>`/`<SECOND>`. Uses `union *` (heavy) — prefer the cheap fallback below if it is slow. | all |
| `auth-scenario-success-rate.kql` | Per-version Initiated/Succeeded/Failed + SuccessRate. The headline per scenario. | single-MV **Registration / Authentication** scenarios |
| `auth-scenario-initiates.kql` | Per-version initiate volume (guards against reading noise as a regression). | any scenario (swap `<INIT_COL>`) |
| `auth-pn-checkforauth-completion.kql` | Two-table join: notifications initiated vs results reaching a terminal `FinalResult`. CompletionRate / DropRate. | **PN + CheckForAuth** families (MFA / PSI / MSA) |
| `auth-reacted-notification-split.kql` | Approved / Denied / Error split of reacted notifications. | **PN + CheckForAuth Results** families |
| `auth-stats.kql` | Fleet/adoption stats: total devices, adoption-over-time, DAU, version share, OEM/OS/Country. Raw `union *`. | app-wide |
| `authenticator-crash-denominator.kql` | Active devices for `<FIRST>` **and** `<SECOND>` in one query — the denominator for crashes-per-1k-active-devices (numerator from App Center). | crash/stability layer |

### Crash / stability (Authenticator)

Crash clusters are **not** in Kusto — pull them from **App Center** with
`assets/scripts/fetch-appcenter-crashes.js`, then divide by the device counts from
`authenticator-crash-denominator.kql` for an honest crashes-per-1k rate. Read
`assets/docs/crash-sources.md` first (auth/token, the `errorGroupId`-is-version-scoped and
share-vs-rate gotchas, App Center Analytics is retired, secret handling, Play Console Phase 2).

### Cheap version-resolution fallback

`union *` in `auth-version-resolve.kql` scans every table. If it is slow, resolve versions
from a high-volume MV instead (validated):

```kusto
Entra_MFA_Push_Notification_And_CheckForAuth_MV_V1
| where EventDate >= datetime(<START>) and EventDate <= datetime(<END>)
| where isnotempty(AppVersion)
| summarize Devices = sum(NotificationInitiatedDCount) by AppVersion
| order by Devices desc
```

### Authenticator scenario → MV → column catalog

Outcome columns each have a `…DCount` distinct-device twin. **Registration / Authentication
MVs expose only `Initiated / Succeeded / Failed (+DCount)` and `TotalUniqueDevices` — there is
NO `Cancelled` / `PartiallySucceeded` column.** PN MVs carry only an initiated counter; the
terminal outcome lives in the paired `_Results_MV_V1`.

| Scenario | Registration/Auth MV (success-rate) | Initiate column | PN init MV | PN init column | PN results MV (`FinalResult`) | results init column |
|----------|-------------------------------------|-----------------|-----------|----------------|------------------------------|---------------------|
| Passkey WebAuthN Reg | `Passkey_WebAuthN_Registration_MV_V1` | `Initiated` | — | — | — | — |
| Passkey InApp Reg | `Passkey_InApp_Registration_MV_V1` | `Initiated` | — | — | — | — |
| Passkey WebAuthN Auth | `Passkey_WebAuthN_Authentication_MV_V1` | `Initiated` | — | — | — | — |
| Entra MFA Reg (QR) | `Entra_MFA_Registration_QR_Code_Flow_MV_V1` | `Initiated` | — | — | — | — |
| Entra MFA Reg (Manual/Non-QR) | `Entra_MFA_Registration_Manual_Flow_MV_V1` + `Entra_MFA_Registration_Non_QR_Code_Flow_MV_V1` | `Initiated` | — | — | — | — |
| Entra MFA PN+CFA | — | — | `Entra_MFA_Push_Notification_And_CheckForAuth_MV_V1` | `NotificationInitiated` | `Entra_MFA_Push_Notification_And_CheckForAuth_Results_MV_V1` | `RequestTimeInitiated` |
| Entra PSI Reg | `Entra_PSI_Registration_MV_V1` | `Initiated` | — | — | — | — |
| Entra PSI PN-Reg | `Entra_PSI_Push_Notification_Registration_MV_V1` | `RegistrationStarted` | — | — | — | — |
| Entra PSI PN+CFA | — | — | `Entra_PSI_Push_Notification_And_CheckForAuth_MV_V1` | `NotificationInitiated` | `Entra_PSI_Push_Notification_And_CheckForAuth_Results_MV_V1` | `RequestTimeInitiated` |
| MSA NGC Reg | `Entra_MSA_NGC_Registration_MV_V1` | `Initiated` | — | — | — | — |
| MSA SA Reg | `Entra_MSA_SA_Registration_MV_V1` | `Initiated` | — | — | — | — |
| MSA NGC/SA PN+CFA | — | — | `Entra_MSA_Push_Notification_And_CheckForAuth_MV_V1` | `NotificationReceivedInitiated` | `Entra_MSA_Push_Notification_And_CheckForAuth_Results_MV_V1` | `SessionTimeInitiated` |

**MSA NGC vs SA split:** the MSA PN init MV **and** its results MV both carry `IsNGC`
(`"true"` → NGC, `"false"` → SA). Apply the same `| where IsNGC == "..."` filter on both
sides of the join.

`FinalResult` ∈ {`Approved`, `Denied`, `Error`}. Completion = Approved+Denied ÷ initiated.

### Drilling below the outcome MVs (the "why" behind a moved metric)

The outcome MVs answer *what* (rate up/down); they do **not** explain *why*. Two layers sit beneath
them — climb down per [`../docs/investigation-patterns.md`](../docs/investigation-patterns.md):

1. **`*_Errors_MV_V1` companion (reason + dimension).** Essentially every scenario has one, named by
   inserting `Errors` into the outcome MV name — e.g. `Passkey_WebAuthN_Registration_MV_V1` →
   `Passkey_WebAuthN_Registration_Errors_MV_V1`, `Passkey_WebAuthN_Authentication_MV_V1` →
   `Passkey_WebAuthN_Authentication_Errors_MV_V1` (PN families use `…_And_CheckForAuth_Errors_MV_V1`).
   Schema is uniform: `EventDate, Error, OsLevel, AppVersion, DeviceInfoMake, ErrorCount, ErrorDCount,
   TotalUniqueDevices`. This is the **reason breakdown of `Failed`**, already sliced by OS major and
   OEM — exactly the dimensional decomposition (P6) and benign-vs-real classification (P5) the patterns
   need. It carries **counts only**, so always pair it with the outcome MV's `Initiated` for the rate.

2. **Raw `passkeyoperations` (structured sub-code).** When `Error` is a coarse bucket, the raw table
   has the finer code. Key fields: `OperationName` (`PasskeyCredentialRequest{Initiated,Succeeded,
   Failed}`, plus sub-operations like `PasskeyBeginGetCredential*`), `AppInfo_Version`,
   `DeviceInfo_OsVersion` (`osLevel = tostring(split(DeviceInfo_OsVersion," ")[0])`), `DeviceInfo_Make`,
   `DeviceInfo_Id`, `EventInfo_Time`, and `AllProperties` (JSON string — `todynamic()` it). Useful
   `AllProperties` keys: `RequestType` (`CreatePasskeyCredentialRequest` = registration,
   `GetPasskeyCredentialRequest` = authentication), `PasskeyFlow`
   (`WEB_AUTH_N_REGISTRATION`/`WEB_AUTH_N_AUTHENTICATION`/`IN_APP_REGISTRATION`), `Error`,
   `ErrorSource`, `IsCrossDevice`, `DeviceUnauthenticatedErrorCode` (Android `BiometricPrompt` code —
   5/10/13/14 = abandonment, 1/7/9 = device/hard), `DeviceUnauthenticatedErrorMessage`, `Source`.

3. **Know what a metric counts before you drill (P9):** `.show materialized-view <Name> | project Query`
   reveals the source table and the `OperationName`/`RequestType`/`PasskeyFlow` filters — e.g.
   Registration MVs count only `CreatePasskeyCredentialRequest`, Authentication only
   `GetPasskeyCredentialRequest` — so you query the right request family in the raw table.
