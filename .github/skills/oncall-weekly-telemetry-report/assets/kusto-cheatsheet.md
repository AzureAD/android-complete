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

---

## 9. MCP output handling

- Most queries with multi-week × per-error-code grain return **>50 KB** and are written to a side file by the tool. Read the side file with the `read_file` tool, or pipe through `bucket-trends.js` / `summarize-attribution.js`.
- The first row of `results.items` is the **schema object**, not data. The helper scripts know this.
- If a query times out or returns `BadRequest`, check **column name typos first** (the error message names the missing column).

---

## 10. Helper scripts

| Script | Purpose |
|---|---|
| [`bucket-trends.js`](bucket-trends.js) | Bucket every error code into regression / spike / improvement / flat across an N-week window |
| [`summarize-attribution.js`](summarize-attribution.js) | Roll up 7-dim attribution slices per (error_code, week) — feeds the spike-attribution cards |
| [`report-template.html`](report-template.html) | Canonical layout. Copy to `oncall-wow-report-v{N+1}.html` and replace data only — never restructure CSS |
