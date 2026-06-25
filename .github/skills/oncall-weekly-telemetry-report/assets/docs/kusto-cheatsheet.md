# Kusto Cheatsheet for the OCE Weekly Report

Distilled from the **production Android Broker Dashboard** (374 queries) plus lessons learned running the skill end-to-end. **Read this before writing any KQL for this report** — it will save you from the most common silent-data-quality bugs.

---

## 1. Connection

| | |
|---|---|
| **Cluster** | `https://idsharedeus2.kusto.windows.net` |
| **Database** | `ad-accounts-android-otel` |
| **MCP tool** | `mcp_azure-mcp-ser_kusto` (command `query`) |
| **MCP timeout** | ~240 s — raw `android_spans` queries usually exceed this; **always prefer materialized views** |

---

## 2. Use the canonical *materialized views*, not the bare names

The dashboard never queries `ErrorStats` directly. It uses the `Metrics` / `Updated` variants, which are pre-aggregated and HLL-bucketed. Use these:

| Use case | Canonical view |
|---|---|
| Per-error-code counts (devs, reqs) | `materialized_view('ErrorStatsMetrics')` |
| Total broker requests / devices | `materialized_view('BrokerAdoptionStatsUpdated')` |
| Silent auth — all requests | `materialized_view('SilentAuthStatsAllRequestsMetrics')` |
| Silent auth — successes (without expected error) | `materialized_view('SilentAuthStatsRequestsWithoutExpectedErrorMetrics')` |
| Interactive auth — all / success | `materialized_view('InteractiveAuthStatsAllRequestsMetrics')` / `…WithoutExpectedErrorMetrics` |
| FIDO requests | `materialized_view('FidoAllRequestsMetrics')` |
| Calling-app share | `materialized_view('AppStatsUpdated')` |
| SKU share | `materialized_view('SkuStatsUpdated')` |
| Latency (TDigest) | `materialized_view('PerfStatsUpdated')` |
| Per-flight slicing | `Operations_ByFlight`, `ErrorCodeBySpan_ByFlight`, `ErrorType_ByFlight` |

Always wrap in `materialized_view(...)` — referencing the table name directly may pick up the raw, much slower base table.

Time filter on materialized views is always **`EventInfo_Time`**. Use `PipelineInfo_IngestionTime` only when querying raw `android_spans`.

---

## 3. THE distinct-device-count gotcha (most important rule)

`countDevices` on `ErrorStats*` is a **per-row distinct count, not additive**. If you sum it across multiple rows you will double-count any device that appeared in more than one slice. **The dashboard never does this.** Every dashboard query computes devices via:

```kql
| summarize countDevices = dcount_hll(hll_merge(countDevicesHll))
```

`countDevicesHll` is the **HLL sketch** stored alongside the row. Merging HLLs across rows and then `dcount_hll`-ing gives the correct distinct count.

**Symptom of the bug:** device counts that sum to more than the fleet size; WoW deltas that look enormous when the underlying user impact is small.

For request counts, `sum(countRequests)` and `sum(countOverall)` are correct (they're additive).

---

## 4. Helper functions used by the dashboard

Reuse these so this report agrees with the dashboard:

| Function | Purpose | Used on |
|---|---|---|
| `MergeAccountType(account_type)` | Collapse AAD variants together and MSA variants together | every error/perf query |
| `MergeIsSharedDevice(is_shared_device)` | Normalize null → "personal", true → "shared", false → "personal" | every error/perf query |
| `MergeUiRequiredExceptions(error_type)` | Collapse the 6+ string variants of `UiRequiredException` into one | error-type aggregation |
| `prettyFormatNumber(n)` | "1.2 M" / "856 k" formatting in tile output | display-only tiles |

The 7-dimension attribution slicing is **fully achievable from `ErrorStatsMetrics`** — it has `account_type`, `is_shared_device`, `broker_version`, `active_broker_package_name`, `AppInfo_Version`, `client_sku`, `calling_package_name`, `span_name`. **You do NOT need a fallback to raw `android_spans` for these dimensions** (this skill previously claimed you did — that was wrong).

---

## 5. Latency — never sum percentiles

Latency is stored as a TDigest sketch. **Percentiles are not additive** — averaging p95 across rows is meaningless. Always merge first:

```kql
materialized_view('PerfStatsUpdated')
| where EventInfo_Time between ((_startTime) .. (_endTime))
| where span_name in ('AcquireTokenSilent','GetAccounts','RemoveAccount','ProcessWebsiteRequest')
| where span_status == 'OK'
| summarize p50 = percentile_tdigest(tdigest_merge(responseTimeTDigest), 50, typeof(long)),
            p95 = percentile_tdigest(tdigest_merge(responseTimeTDigest), 95, typeof(long)),
            p99 = percentile_tdigest(tdigest_merge(responseTimeTDigest), 99, typeof(long))
        by week=startofweek(EventInfo_Time), span_name
```

**Note:** there is also a `PerfStatsMetrics` view, but it does **not** expose per-percentile columns directly — it has the merged TDigest. Use `PerfStatsUpdated` (preferred by the dashboard) and `percentile_tdigest(tdigest_merge(...), N, typeof(long))`.

---

## 6. Column-name reference (so you don't burn a query on a typo)

| View | Has column | Doesn't have |
|---|---|---|
| `ErrorStatsMetrics` | `error_code`, `error_type`, `span_name`, `broker_version`, `active_broker_package_name`, `AppInfo_Version`, `client_sku`, `calling_package_name`, `account_type`, `is_shared_device`, `EventInfo_Time`, `countOverall`, `countDevicesHll` | `calling_package` (no — it's `calling_package_name`), `countDevices` (no — use the HLL) |
| `BrokerAdoptionStatsUpdated` | `broker_version`, `EventInfo_Time`, `countRequests`, `countDevicesHll` | per-error breakdown (use ErrorStatsMetrics) |
| `PerfStatsUpdated` | `span_name`, `span_status`, `broker_version`, `active_broker_package_name`, `account_type`, `is_shared_device`, `client_sku`, `calling_package_name`, `responseTimeTDigest`, `countRequests` | `p50_ms` / `p95_ms` (no — use `percentile_tdigest`) |
| `AppStatsUpdated` | `calling_package_name`, `EventInfo_Time`, `countRequests`, `countDevicesHll` | error breakdown |

---

## 7. Week alignment — Kusto `startofweek()` is **Sunday-aligned**

If a user says "the week of May 2 → May 9", Kusto buckets it as `startofweek('2026-05-09') == 2026-05-03T00:00:00Z`. **Always confirm**: print the distinct `startofweek(EventInfo_Time)` values from your first query and verify the bucket label matches the user's intent. Off-by-one-week is the #1 silent error.

For an 8-complete-week 60-day window ending Sat May 9, the buckets are:
`2026-03-08, 03-15, 03-22, 03-29, 04-05, 04-12, 04-19, 04-26, 05-03` — that's 9 buckets, one of which (the first) was a partial start. Drop the first; keep 8 complete weeks.

---

## 8. Canonical query templates

### 8a. Reliability (auth-only denominator)

```kql
let all = materialized_view('SilentAuthStatsAllRequestsMetrics')
  | where EventInfo_Time > ago(70d)
  | summarize allReq = sum(countRequests),
              allDev = dcount_hll(hll_merge(countDevicesHll))
       by week = startofweek(EventInfo_Time);
let ok  = materialized_view('SilentAuthStatsRequestsWithoutExpectedErrorMetrics')
  | where EventInfo_Time > ago(70d)
  | summarize okReq = sum(countRequests),
              okDev = dcount_hll(hll_merge(countDevicesHll))
       by week = startofweek(EventInfo_Time);
all | join kind=inner ok on week
    | project week,
              reqRel = round(100.0 * okReq / allReq, 3),
              devRel = round(100.0 * okDev / allDev, 3)
    | order by week asc
```

**Auth-only device union** (Silent ∪ Interactive — what the report uses for the "real fleet" KPI). The natural reach for `hll_merge_array` to combine two pre-merged HLL sketches **does not exist in Kusto** (`SEM0260: Unknown function`). Instead, project the raw `countDevicesHll` rows from both views, `union` them, and `hll_merge` once at the end:

```kql
let s = materialized_view('SilentAuthStatsAllRequestsMetrics')
  | where EventInfo_Time between (datetime(<START>) .. datetime(<END>))
  | project EventInfo_Time, countDevicesHll;
let i = materialized_view('InteractiveAuthStatsAllRequestsMetrics')
  | where EventInfo_Time between (datetime(<START>) .. datetime(<END>))
  | project EventInfo_Time, countDevicesHll;
union s, i
| summarize authDev = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time)
| where week < datetime(<END>)
| order by week asc
```

### 8b. 60-day error trend (feeds `bucket-trends.js`)

```kql
materialized_view('ErrorStatsMetrics')
| where EventInfo_Time > ago(70d)
| where isnotempty(error_code) and error_code != 'success'
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time), error_code
| order by error_code asc, week asc
```

### 8c. Spike attribution — one slicing dim at a time

The MCP tool can return ~50–700 KB of JSON; multi-dim cartesians blow this out. **Slice one dimension per query**, then post-process with `summarize-attribution.js`:

```kql
let codes = dynamic(['no_tokens_found','unauthorized_client','Code:-6',
                     'unknown_crypto_error','null_pointer_error','timed_out_execution']);
materialized_view('ErrorStatsMetrics')
| extend unified_account_type = MergeAccountType(account_type)
| extend unified_is_shared_device = MergeIsSharedDevice(is_shared_device)
| where EventInfo_Time > ago(14d)
| where error_code in (codes)
| extend wk = startofweek(EventInfo_Time)
| summarize devs = dcount_hll(hll_merge(countDevicesHll))
     by wk, error_code, span_name           // <-- swap this dim per query
| order by error_code asc, wk asc, devs desc
```

Run once each with the trailing dim set to: `span_name`, `calling_package_name`, `active_broker_package_name`, `broker_version`, `unified_account_type`, `unified_is_shared_device`, `client_sku`. That's the full 7.

### 8d. Latency — see Section 5 above.

### 8e. Broker version share

```kql
materialized_view('BrokerAdoptionStatsUpdated')
| where EventInfo_Time > ago(21d)
| summarize req = sum(countRequests),
            dev = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time), broker_version
| order by week asc, req desc
```

> **⚠️ Share/snapshot views are window-parameterized — don't assume 60-day coverage.** The share queries — `BrokerAdoptionStatsUpdated` (version share, 8e), `AppStatsUpdated` (calling-app share), `SkuStatsUpdated` (SKU share), and the [`broker-version-share-wow.kql`](../queries/broker-version-share-wow.kql) / [`app-share.kql`](../queries/app-share.kql) templates — all take an explicit `<START>..<END>` (or `ago(Nd)`) window. They return **exactly the weeks you ask for, nothing more.** The adoption / app-share sections of the report typically only need a short **2–3 week** WoW window, so that's what these templates default to (`ago(21d)` above).
>
> The trap: if you then try to draw a **9-week sparkline** for version/app/SKU adoption from that same short pull, you'll only have 2–3 real points and the rest will look flat or fabricated (the validator's low-peak `data-trend` heuristic may flag it). If you genuinely need a multi-week adoption sparkline, **re-run the share query with the full 60-day window** (`<START>` = reporting-Sunday − 56d) — don't pad a short result. If you don't need the sparkline, don't build one from a 2–3 week pull and pretend it's a trend.

---

## 10. Helper scripts

| Script | Purpose |
|---|---|
| [`bucket-trends.js`](bucket-trends.js) | Bucket every error code into regression / spike / improvement / flat across an N-week window. Pass `--end=YYYY-MM-DD` (Sunday after the reporting week, exclusive) to drop partial in-progress buckets. |
| [`agg.js`](agg.js) | Per-error per-dim top-N rollup with WoW deltas. Feeds spike-attribution dim blocks. |
| [`summarize-attribution.js`](summarize-attribution.js) | Roll up 7-dim attribution slices per (error_code, week) — feeds the spike-attribution cards |
| [`queries/`](queries/) | Canonical KQL templates, one per query — see [`queries/README.md`](queries/README.md) |
| [`templates/`](templates/) | Copy-paste HTML snippets for cards / footer JS |
| [`report-template.html`](../templates/report-template.html) | Canonical layout. Copy to `~/android-oce-reports/oncall-wow-report-<sunday>.html` and replace `{{TOKENS}}` only — never restructure CSS |

---

## 11. The `error_location` JSON shape (read this before slicing stack-traces)

`error_location` on `android_spans` is a **serialized JSON string**, not a dynamic object. Naively writing `error_location.MethodName` returns null in KQL. Use `tostring()` to project it raw, then `parse_json()` if you need to drill in:

```kql
android_spans
| where error_code == 'null_pointer_error'
| extend loc = tostring(error_location)         // {"ClassName":"...","MethodName":"...","LineNumber":N}
| extend method = tostring(parse_json(loc).MethodName)
| extend lineNo = toint(parse_json(loc).LineNumber)
| summarize devices = dcount(DeviceInfo_Id) by method, lineNo
| top 20 by devices desc
```

For the report's **mandatory Originator pre-check** (Step 4 of SKILL.md), use [`queries/error-message-and-location.kql`](queries/error-message-and-location.kql) — it returns the raw `loc` blob alongside the first 100 chars of `error_message`, which is enough to identify the throw site (file + method + line) and the dominant message string.

The single most informative attribution query for a regressing code:

```kql
android_spans
| where PipelineInfo_IngestionTime between (datetime(<START>) .. datetime(<END>))
| where error_code in (<CODES_LIST>)
| extend loc = tostring(error_location),
         msg = substring(tostring(error_message), 0, 100)
| summarize cnt = count(),
            devices = dcount(DeviceInfo_Id)
     by error_code, loc, msg
| top 60 by devices desc
```

---

## 12. AADSTS reference — common eSTS responses bridged into broker errors

When `error_message` starts with `AADSTS<digits>`, the originator is **eSTS, not broker**, regardless of which broker exception class was constructed. Broker (specifically `common/ExceptionAdapter.{getExceptionFromTokenErrorResponse, exceptionFromAuthorizationResult}`) translates the AAD response into a broker exception code as a courtesy — it is not the cause.

| AADSTS code | Meaning | Broker exception code (typical) | Originator | Owner |
|---|---|---|---|---|
| `AADSTS500011` | Resource principal not found in tenant | `invalid_resource` | eSTS / tenant config | Resource owner team |
| `AADSTS500014` | Service principal disabled in tenant | `invalid_resource` | eSTS / tenant config | Resource owner team |
| `AADSTS50158` | External claims challenge / CA enforcement | `interaction_required` | eSTS / Conditional Access | Identity CA team |
| `AADSTS50173` | Fresh token needed (CA / FR) | `interaction_required` / `invalid_grant` | eSTS / CA | Identity CA team |
| `AADSTS65001` | User / admin has not consented | `unauthorized_client` | eSTS / app registration | App owner team |
| `AADSTS70008` | Authorization code expired | `invalid_grant` | eSTS (timing) | Investigate caller latency |
| `AADSTS70011` | Invalid scope | `invalid_scope` | eSTS / app registration | App owner team |
| `AADSTS90072` | User account from external tenant doesn't exist locally | `unauthorized_client` | eSTS / B2B config | Tenant admin |
| `AADSTS900971` | No reply address | `invalid_request` | eSTS / app registration | App owner team |

**Rule of thumb:** if the throw site is an `ExceptionAdapter.*` method AND the message begins with `AADSTS`, tag the card `<span class="origin-tag origin-thirdparty">eSTS</span>` and route to the resource / app owner team. Do not invent a broker PR to "fix" it.

---

## 13. MCP output handling

- Most queries with multi-week × per-error-code grain return **>50 KB** and are written to a side file by the tool. Read the side file with the `read_file` tool, or pipe through `bucket-trends.js` / `summarize-attribution.js`.
- The first row of `results.items` is the **schema object**, not data. The helper scripts know this.
- If a query times out or returns `BadRequest`, check **column name typos first** (the error message names the missing column).
