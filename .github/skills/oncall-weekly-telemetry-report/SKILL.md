---
name: oncall-weekly-telemetry-report
description: Generate the weekly Android Broker on-call (OCE) WoW + 60-day trend telemetry report as a polished self-contained HTML file. Use this skill for the weekly OCE rotation when asked to "produce the OCE report", "weekly on-call report", "WoW telemetry report", "weekly broker health report", or "generate this week's on-call summary". Pulls from `android_spans` materialized views, attributes regressions/improvements to PRs in `broker/` and `common/`, and writes to `oncall-wow-report-vN.html` at repo root.
---

# OCE Weekly Report

Produce the weekly Android Broker on-call (OCE) telemetry report as a self-contained HTML file at `$env:USERPROFILE\android-oce-reports\oncall-wow-report-v{N+1}.html` (i.e. `~/android-oce-reports/`, outside the workspace so reports never accidentally get committed).

The output mirrors the structure of the canonical template at [`assets/report-template.html`](assets/report-template.html) — copy it to `oncall-wow-report-v{N+1}.html` at repo root and edit in place. Do **not** redesign the layout each week.

**Before writing any KQL, read [`assets/kusto-cheatsheet.md`](assets/kusto-cheatsheet.md).** It captures the canonical view names, helper functions, the HLL device-count gotcha, week-alignment rules, and ready-to-paste query templates — distilled from the production Android Broker Dashboard.

Reusable helpers in [`assets/`](assets/):

| File | Purpose |
|---|---|
| [`report-template.html`](assets/report-template.html) | Canonical layout — copy and replace data only, never restructure CSS |
| [`kusto-cheatsheet.md`](assets/kusto-cheatsheet.md) | Schemas, helper funcs, gotchas, ready-to-paste KQL templates |
| [`code-attribution-template.md`](assets/code-attribution-template.md) | Per-card checklist for the deep code-attribution block (Originator / Top throw site / Wrapper / Caller hot-spots / Underlying cause / Top error_messages / Likely PRs / Next step) |
| [`bucket-trends.js`](assets/bucket-trends.js) | Bucket all error codes into 60-day regression / spike / improvement / flat. Run with `--metric=devs` AND `--metric=reqs` |
| [`summarize-attribution.js`](assets/summarize-attribution.js) | Roll up 7-dim attribution slices for spike-attribution cards |

---

## Inputs to confirm with the user

1. **Reporting week** — defaults to the most recent complete week (Sun → Sat ending yesterday or today). **Confirm explicit dates with the user.** Note that Kusto's `startofweek()` is **Sunday-aligned**, so a user-spoken "week of May 3 → May 9" maps to the bucket `startofweek == 2026-05-03`. Off-by-one-week is the #1 silent error — verify by printing the distinct `startofweek` buckets from your first query and confirming the label matches the user's intent.
2. **Comparison baseline** — defaults to the prior complete week.
3. **60-day window** — last 8 complete weeks (drop the partial start week when computing trend deltas).
4. **Output filename** — `$env:USERPROFILE\android-oce-reports\oncall-wow-report-YYYY-MM-DD.html`, where `YYYY-MM-DD` is the **Sunday `startofweek` bucket** of the reporting week (e.g. the report for the week of May 3 → May 9, 2026 is `oncall-wow-report-2026-05-03.html`). User-scoped, outside the workspace; the date matches the Kusto bucket label used throughout the report.

If any of these are unstated, ask once, then proceed.

---

## Required sections (in order)

1. **Top-line health KPIs** — total requests, total devices, silent-auth reliability %, interactive reliability %, p95 latency on the hot spans. WoW delta on each. Inline SVG sparklines.
2. **Things that need attention this week** — three callouts:
   - **Denominator caveat** — explain any large total-spans device-count shift caused by span-emission changes (e.g. `goAsync()` refactors). Always state which denominator the report uses (auth-only: `SilentAuthStats` ∪ `InteractiveAuthStats`).
   - **Real WoW regressions** worth investigation, with PR links.
   - **Slow-burn 60-day regressions** (rising on 60d even when WoW looks flat). Link to the 60-Day Trend section.
   - **Real wins this week**, with PR links.
   - **Traffic shape** — flat / surge / collapse summary.
3. **📈 60-Day Trend Analysis** — built from the `ErrorStatsMetrics` materialized view over the last 8 complete weeks. **Run the bucketing pipeline FOUR times — the cross-product of `{error_code, error_type} × {devs, reqs}`** — and union the regression sets. An entry (code OR type) is flagged if it regresses on either metric.

   - **% of devices** affected (`devsHit / authActiveDevs`) — catches errors hitting more users.
   - **% of requests** affected (`errReqs / authTotalReqs`) — catches per-device retry storms (fewer users, more traffic per user). The previous report would have missed `kdfv2_key_derivation_error` (262 → 5,374 reqs on ~57 devices) without this dim.

   Categories: True 60d regression / Ephemeral 60d spike (peak-then-recover) / True 60d improvement / Flat. Every rising entry — whether `error_code` or `error_type` — gets the same Spike Attribution + Code Attribution treatment (Step 4 / Step 5).

   Always apply `MergeUiRequiredExceptions(error_type)` before bucketing on type; otherwise the 6+ string variants of `UiRequiredException` will each be tracked separately and skew the buckets.
4. **🔎 Spike Attribution** — one card per WoW regression AND per 60-day regression, **for both `error_code` and `error_type` regressions**. Each card slices on **all 7 dimensions** (broker version, span, active broker pkg, calling app, account type AAD/MSA, shared-device mode, client SKU). Each card ends with a **deep Code Attribution block** (see Step 4 for the required fields) and a Traffic Attribution verdict.
5. **🚚 Traffic Attribution** — top-level section listing every error whose spike is fully or partly explained by traffic volume from a specific calling app, rather than a code regression. If none qualify this week, render the section with an explicit "None this week" note.
6. **Error codes — WoW with stable denominator** — full table with `Δ reqs %` and `Δ devs %` columns and the 60d sparkline.
7. **Error types — WoW with stable denominator** — full table, **same columns and rigor as the error-codes table** (`Δ reqs %`, `Δ devs %`, 60d sparkline, status pill). Any regressing type also gets a spike-attribution card in Section 4. For composite types (e.g. `ClientException` is the umbrella for many sub-codes), include a **decomposition card** that breaks the WoW Δ down into the top 3 contributing sub-codes — so a `ClientException` −5 pp drop is explicitly attributed to e.g. `−8.5 pp timed_out_execution` + `−3.4 pp unknown_authority` + `−0.15 pp illegal_argument_exception`.
8. **📊 Traffic analysis** — total requests/devices (WoW + 60d), top calling apps, top spans, **requests-per-device ratio** per error and overall (a rising ratio = retry storm; a falling ratio = caching gain), sampling-rate change indicator.
9. **Latency** — p50/p95/p99 by hot span.
10. **Broker version adoption** — week-over-week version share.
11. **Appendix** — query list and methodology.

---

## Step-by-step workflow

### Step 1 — Bootstrap the new report file from the template

This skill ships with a canonical template at [`assets/report-template.html`](assets/report-template.html) (a real prior week's report kept as the reference layout). **Always start from this template** — never assume a prior week's report exists on the file system.

```pwsh
# Reports live OUTSIDE the workspace, in the user's home folder, so they never
# accidentally get committed and don't pollute the repo root.
$reportDir = Join-Path $env:USERPROFILE 'android-oce-reports'
New-Item -ItemType Directory -Force $reportDir | Out-Null

# Filename uses the Sunday startofweek bucket of the reporting week (matches the
# Kusto bucket label used throughout the report). For "week of May 3 -> May 9, 2026"
# this evaluates to 2026-05-03.
$reportingSunday = '2026-05-03'   # <-- replace with the confirmed reporting-week Sunday
$next = Join-Path $reportDir "oncall-wow-report-$reportingSunday.html"

if (Test-Path $next) {
  Write-Warning "$next already exists — confirm with the user before overwriting."
}

Copy-Item c:\Users\shjameel\Repos\android-complete\.github\skills\oncall-weekly-telemetry-report\assets\report-template.html $next -Force
Write-Host "Bootstrapped $next from skill template."
```

Edit `$next` only. The template defines the layout, CSS, sparkline structure, attribution-card markup, and section ordering — **do not redesign these per week**. Replace the data inside each section with the current week's content; keep the structure verbatim.

If the template ever needs structural improvements (new section, new card style, etc.), update `assets/report-template.html` in the skill folder and commit it so future weeks inherit the change.

### Step 2 — Pull WoW reliability data

Use the Kusto MCP tool against:
- **Cluster:** `https://idsharedeus2.kusto.windows.net`
- **Database:** `ad-accounts-android-otel`

**Always prefer the canonical `materialized_view('XxxMetrics' or 'XxxUpdated')` variants** — these are what the production dashboard uses, are pre-aggregated and HLL-bucketed, and avoid the 240 s MCP timeout that plain `android_spans` queries hit. Full schema, gotchas, and query templates: [`assets/kusto-cheatsheet.md`](assets/kusto-cheatsheet.md).

| Need | View |
|------|------|
| Per-error-code / per-error-type / per-span counts | `materialized_view('ErrorStatsMetrics')` |
| Total broker reqs / devices | `materialized_view('BrokerAdoptionStatsUpdated')` |
| Silent auth reliability | `SilentAuthStatsAllRequestsMetrics` + `SilentAuthStatsRequestsWithoutExpectedErrorMetrics` |
| Interactive auth reliability | `InteractiveAuthStatsAllRequestsMetrics` + `InteractiveAuthStatsRequestsWithoutExpectedErrorMetrics` |
| Latency (p50/p95/p99) | `materialized_view('PerfStatsUpdated')` — use `percentile_tdigest(tdigest_merge(responseTimeTDigest), N, typeof(long))` |
| Broker version share | `BrokerAdoptionStatsUpdated` |
| Calling app share | `AppStatsUpdated` |
| SKU share | `SkuStatsUpdated` |
| Spike-by-flight slicing | `Operations_ByFlight`, `ErrorCodeBySpan_ByFlight`, `ErrorType_ByFlight` |

Time filter: always use `EventInfo_Time` on materialized views. Use `PipelineInfo_IngestionTime` only on raw `android_spans`.

**Three rules that will silently corrupt your data if violated** (full detail in the cheatsheet):

1. **Distinct devices are HLL-encoded.** Use `dcount_hll(hll_merge(countDevicesHll))`, never `sum(countDevices)`. Summing double-counts every device that appears in more than one row.
2. **Apply the dashboard helper functions** so this report agrees with the dashboard: `MergeAccountType(account_type)`, `MergeIsSharedDevice(is_shared_device)`, `MergeUiRequiredExceptions(error_type)`.
3. **Auth-only denominator for reliability %s:** sum `countRequests` from `SilentAuthStatsAllRequestsMetrics` ∪ `InteractiveAuthStatsAllRequestsMetrics` — not total broker spans. Total span counts are sensitive to `goAsync()` / receiver refactors and will give false WoW reliability swings.

### Step 3 — Pull 60-day trend

Don't pre-filter to a hand-picked top-N list — small-but-rising errors (e.g. `null_pointer_error` at ~67K devices) will fall off and never show up in the trend section. Instead pull every error code **and every error type** with a meaningful baseline across the window, then bucket each.

#### 3a. Per-error-code trend

```kql
materialized_view('ErrorStatsMetrics')
| where EventInfo_Time > ago(70d)
| where isnotempty(error_code) and error_code != 'success'
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time), error_code
| order by error_code asc, week asc
```

#### 3b. Per-error-type trend (same rigor)

```kql
materialized_view('ErrorStatsMetrics')
| extend unified_error_type = MergeUiRequiredExceptions(error_type)
| where EventInfo_Time > ago(70d)
| where isnotempty(unified_error_type)
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time), unified_error_type
| order by unified_error_type asc, week asc
```

`MergeUiRequiredExceptions` is mandatory — without it the 6+ string variants of `UiRequiredException` (raw, fully-qualified, com.microsoft.identity.common.exception.*) each show as separate rows and skew the buckets.

#### 3c. Run the bucketer 4 times (cross-product of `{code, type} × {devs, reqs}`)

```pwsh
# Error codes — by devices, then by requests
node .github\skills\oncall-weekly-telemetry-report\assets\bucket-trends.js <codes.json> --start=2026-03-08
node .github\skills\oncall-weekly-telemetry-report\assets\bucket-trends.js <codes.json> --start=2026-03-08 --metric=reqs

# Error types — by devices, then by requests
node .github\skills\oncall-weekly-telemetry-report\assets\bucket-trends.js <types.json> --start=2026-03-08
node .github\skills\oncall-weekly-telemetry-report\assets\bucket-trends.js <types.json> --start=2026-03-08 --metric=reqs
```

Take the **union** of all four regression sets. Both `error_code` and `error_type` regressions get a spike-attribution card in Step 5.

It will print regression / spike / improvement / flat buckets, sorted by peak. The thresholds (in case you need to tune):

- **True 60d regression:** `delta > +15%` and trajectory is monotonic-ish (no single-week spike dominating).
- **Ephemeral 60d spike:** peak week is ≥3× the mean of the surrounding weeks (peak-then-recover shape).
- **True 60d improvement:** `delta < −15%`.
- **Flat:** otherwise.
- Codes/types with peak weekly devs `< 10K` (or peak weekly reqs `< 100K` when `--metric=reqs`) are filtered out (`--peak-floor=N` to override).

**Why both axes matter:**
- *codes × reqs:* in v5, `kdfv2_key_derivation_error` spiked +1,951% on requests across only ~57 devices — a per-device retry storm device-only bucketing would have missed.
- *types × either:* `error_type` is the umbrella (e.g. `ClientException`, `ServiceException`, `UiRequiredException`) — a moving type that doesn't map cleanly to one moving code is a strong signal of a *new* sub-code being introduced or an existing one being reclassified (the v5 `ClientException` −10% drop was driven by `timed_out_execution` reclassification under PR #141, which would have been invisible from the codes table alone).

**Always present side-by-side WoW tables for BOTH error_code AND error_type** with `Δ reqs %` and `Δ devs %` columns; flag any row where either crosses threshold.

### Step 4 — Code attribution (deep PR correlation)

For every regression card, the Code Attribution block **must** populate the following fields. Shallow PR-citation only is not acceptable. Use [`assets/code-attribution-template.md`](assets/code-attribution-template.md) as the per-card checklist.

| Field | What goes in it | How to find it |
|---|---|---|
| **Originator** | Where the error physically originates: broker code / common / Android system (WebView / Conscrypt / Keystore) / 3rd-party lib (Nimbus JWT, okhttp) / eSTS server / environmental (enterprise TLS interception). Use the colour-coded `origin-tag` spans (`origin-broker`, `origin-android`, `origin-thirdparty`, `origin-env`). | Grep the error string across `broker/`, `common/`, `msal/`. If no match, it's not our code — search the Android SDK or call out as eSTS-returned. |
| **Top throw site** | Fully-qualified file:line where the exception is constructed, plus the % of cases that throw from this single site. | Pull `error_location` / stack-prefix from `android_spans` for the spiking error code (one targeted query, narrow time window). Cite the dominant site. |
| **Wrapper** | Broker/common code that catches the originator's exception and re-throws it as the user-visible error code. Often `IDToken.parseJWT()`, `ServiceException(...)`, `ExceptionAdapter.exceptionFromAuthorizationResult()`. | Walk up the stack from the throw site — check for `try { ... } catch (X e) { throw new Y(...); }` patterns in broker/common. |
| **Caller hot-spots** | Top 1–3 callers of the wrapper, with device counts. Helps identify the specific code path the regression flows through. | `android_spans` slice by `error_location` (or `error.stack_trace` first frame inside our code). |
| **Underlying cause** | The proximate cause one level deeper (e.g. "99% `CertificateException` from `TrustManagerImpl.verifyChain`", "84% `no_such_algorithm` from `ProviderFactory.getMessageDigest`"). | `android_spans` slice by `error.cause` or `error_message` first 80 chars. |
| **Top error_messages** | Top 3–5 distinct `error_message` strings with counts. Often reveals the 3rd-party library or environmental signal (e.g. `net::ERR_SSL_PROTOCOL_ERROR`, Zscaler-issued cert names). | `summarize count() by tostring(error_message)` on raw `android_spans` filtered to the spike. |
| **Likely PRs** | 1–3 PRs with confidence rating (high / medium / low / none), full GitHub URL, commit SHA, author, AB#, and a 1-sentence **why-it's-the-suspect** justification (not just the title). Use the `pr-card` markup. | See PR-grep below. **Cite confidence honestly** — "none" is a valid verdict for environmental errors. |
| **Next step** | Concrete action with a named owner: who runs the next slice, who files the bug, what flight to flip, what correlation IDs to pull. | Pulled from PR authors / CODEOWNERS for the affected file. |

#### PR-grep workflow

```pwsh
cd c:\Users\shjameel\Repos\android-complete\broker
git log --since='<windowStart>' --until='<windowEnd>' --oneline `
    --grep='<error_code>|<related symbol>|<related class>' -i

cd ..\common
git log --since='<windowStart>' --until='<windowEnd>' --oneline `
    --grep='<error_code>|<related symbol>|<related class>' -i
```

When the error name doesn't directly grep (e.g. `timed_out_execution`), grep for related concepts: `timeout`, `coroutine`, `executor`, `cancellation`, `thread pool`, `cache`, `authority`, etc. Then for each candidate PR, **read the diff at the throw site** to confirm it actually touches the failing code path — don't cite a PR just because it grep-matched.

#### Repo URL patterns for citations

| Repo | URL pattern |
|------|-------------|
| `common/` | `https://github.com/AzureAD/microsoft-authentication-library-common-for-android/pull/<num>` |
| `broker/` | `https://github.com/identity-authnz-teams/ad-accounts-for-android/pull/<num>` |
| `msal/` | `https://github.com/AzureAD/microsoft-authentication-library-for-android/pull/<num>` |
| `adal/` | `https://github.com/AzureAD/azure-activedirectory-library-for-android/pull/<num>` |

#### Non-broker errors

For errors with no broker code in the stack (Android system errors like `Code:-10`/`Code:-11`, OEM-specific keystore failures, eSTS-returned codes, environmental TLS interception), explicitly cite **"⚪ None — not in scope"** with confidence `none`, and explain *why* in the why-it's-the-suspect line. Do not invent broker PRs to fill the slot. Tag these errors as `environmental` or `non-broker` so they're tracked but don't page.

### Step 5 — Spike attribution dimensions

**Coverage rule: every `error_code` AND every `error_type` that lands in either the WoW regression list OR the 60-day regression list MUST get a spike-attribution card.** No silent skips.

**`ErrorStatsMetrics` already carries `account_type` and `is_shared_device`** (use the `MergeAccountType` / `MergeIsSharedDevice` helpers to normalize) — so you do **not** need a fallback to raw `android_spans` for these dims. Earlier versions of this skill claimed otherwise; that was wrong. The only dim that requires `android_spans` is `DeviceInfo_OsVersion` (OEM/version slicing).

Slice on **all 7 dimensions** for each spike. Run **one query per dimension** (multi-dim cartesians from MCP can return >500 KB of JSON and risk truncation). For `error_type` cards, swap `error_code in (codes)` for `unified_error_type in (types)` and aggregate by the `MergeUiRequiredExceptions(error_type)` extension — otherwise everything else is identical.

| # | Dimension | Source | Cross-check |
|---|-----------|--------|-------------|
| 1 | Broker version | `ErrorStatsMetrics` group by `broker_version` | Cross-reference `BrokerAdoptionStatsUpdated` to see if the version's request share *also* moved that week — if yes, the spike is rollout-driven, not code-driven |
| 2 | Span name | `ErrorStatsMetrics` group by `span_name` | A single span hosting >60% of the error → strong code-path signal |
| 3 | Active broker package | `ErrorStatsMetrics` group by `active_broker_package_name` | E.g. CompanyPortal vs Authenticator vs LTW |
| 4 | Calling package | `ErrorStatsMetrics` group by `calling_package_name` | If 1–2 callers dominate, this is likely a traffic-attribution case (see Step 6) |
| 5 | Account type (AAD vs MSA) | `ErrorStatsMetrics`, `extend t = MergeAccountType(account_type)` group by `t` | If the split deviates significantly from fleet (~85% AAD / 15% MSA), call it out |
| 6 | Shared device mode | `ErrorStatsMetrics`, `extend s = MergeIsSharedDevice(is_shared_device)` group by `s` | Shared-device fleets have very different error profiles |
| 7 | OS version | `android_spans` filtered by `error_code in (codes)` (or `error_type in (types)`) and a tight time window, group by `DeviceInfo_OsVersion` | OEM-specific Android quirks, especially for `io_error`, `unknown_crypto_error`, `null_pointer_error` |

#### Type cards have one extra required dimension: sub-code decomposition

Because `error_type` is an umbrella over many `error_code` values, every `error_type` regression card MUST also include an **8th dimension: sub-code breakdown** showing the top 3–5 `error_code`s rolled up under that type, with their device counts and Δ vs prior week. This lets the reader see whether the type-level move is driven by one sub-code or many — and routes the deep Code Attribution work to the right sub-code.

```kql
let target_types = dynamic(['ClientException', 'ServiceException']);
materialized_view('ErrorStatsMetrics')
| extend unified_error_type = MergeUiRequiredExceptions(error_type)
| where EventInfo_Time > ago(14d)
| where unified_error_type in (target_types)
| extend wk = startofweek(EventInfo_Time)
| summarize devs = dcount_hll(hll_merge(countDevicesHll)),
            errs = sum(countOverall)
     by wk, unified_error_type, error_code
| order by unified_error_type asc, wk asc, devs desc
```

Cite the dominant sub-codes inline in the type card's verdict (e.g. *"`ClientException` −10.2% drop is dominated by −8.5 pp `timed_out_execution` + −3.4 pp `unknown_authority`"*) and link to those sub-codes' own attribution cards. The deep Code Attribution block (Step 4) for the type card itself focuses on the **wrapper / catch-and-rethrow** path that defines the type (e.g. `BaseException.java`, `ServiceException.java` constructors), not on each sub-code.

Feed the seven JSON outputs into the helper to roll up dim shares per (error_code, week):

```pwsh
node .github\skills\oncall-weekly-telemetry-report\assets\summarize-attribution.js `
  --label=span span.json `
  --label=calling_app app.json `
  --label=active_broker ab.json `
  --label=broker_version ver.json `
  --label=account_type acct.json `
  --label=shared_device shared.json `
  --label=os_version os.json
```

Ready-to-paste KQL for the per-dimension query is in [`assets/kusto-cheatsheet.md` § 8c](assets/kusto-cheatsheet.md).

**Concentration thresholds** (paint the dim bar red):
- > 80% in a single value → strong attribution (one root cause)
- 60–80% → medium attribution
- < 60% → broad / cross-cutting → say so explicitly, don't fabricate a single cause

### Step 6 — Traffic analysis + traffic attribution

Do this section in three parts. Traffic changes (up *or* down) need the same level of root-cause reasoning as error spikes — a uniform "−9% requests across all top apps with flat devices" is **not** a satisfactory verdict on its own; explain *why*.

**6a. Top-line traffic shape.** Compare WoW *and* 60d for both totals and per-segment:

```kql
materialized_view('BrokerAdoptionStatsUpdated')
| where EventInfo_Time > ago(70d)
| summarize totalReq = sum(countRequests),
            totalDev = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time)
| order by week asc
```

For each of the following, report direction + magnitude:
- Total requests (WoW %, 60d %)
- Total devices (WoW %, 60d %)
- Requests-per-device ratio (a drop often means a benign caching improvement; a spike often means a retry storm)
- Top 10 calling apps (`AppStatsUpdated`) — which apps drove the change?
- Top spans by request volume — did one span explode or collapse?
- Sampling-rate change indicator: if total spans moved >20% but auth-only device count moved <5%, suspect a sampling/instrumentation change.

**6b. Reasoning for material traffic shifts (>10% on any segment).** For every span/app/active-broker that moved meaningfully WoW *or* 60d, run this slicing-and-correlation pass:

| # | Question | How to check |
|---|---|---|
| 1 | **Is the move concentrated in one span?** | Slice top-10 spans by `Δreq` absolute and `Δreq %`. A >50% move on a single span almost always points to a code change (span added / removed / sampled / `goAsync()`-ed). |
| 2 | **Is the move concentrated in one calling app?** | Slice `AppStatsUpdated` WoW. A single app moving >20% in requests with flat devices = client-side caching/retry change in that app — escalate to that app's owners, not broker. |
| 3 | **Is the move concentrated in one active broker pkg?** | Slice `BrokerAdoptionStatsUpdated` by `active_broker_package_name`. AppManager (LTW) vs Authenticator vs Intune CP often diverge during a rollout. |
| 4 | **Is the move concentrated in one broker version?** | Cross-check against rollout share. If a span dropped −80% on `16.0.1` but is flat on `15.1.0`, the cause is in the 16.0.1 diff. |
| 5 | **Did anything else co-move?** | A span dropping while `OnUpgradeReceiver`-style downstream spans also drop (`SecretKeyWrapping`, `WrappedKeyAlgorithmIdentifier` in v5) confirms a single upstream change. |

For every meaningful shift, **search for a causal PR** in the repos likely to affect telemetry shape:

```pwsh
# Broker (span add/remove, goAsync, scope changes, sampling/exporter config)
cd c:\Users\shjameel\Repos\android-complete\broker
git log --since='<last8wks>' --oneline -i `
  --grep='span|goAsync|receiver|telemetr|otel|trace|metric|sampl|exporter'

# Common (instrumentation surfaces)
cd ..\common
git log --since='<last8wks>' --oneline -i `
  --grep='span|telemetr|otel|trace|sampl|instrument'
```

**Causal PR categories that meaningfully shift traffic counts** (flag any of these):

- **Span removed / renamed / scope-narrowed** → drops the span's count to zero or partial
- **`goAsync()` / `BroadcastReceiver` refactor** → broadcast may complete before async work flushes the span (this is the v5 PR #88 / `OnUpgradeReceiver` story — call it out as a precedent)
- **Sampling-rate change** in broker `Otel*` / `Telemetry*` exporter config or `common/` instrumentation → uniformly scales counts up or down across many spans
- **New span added** in a hot path → request counts for that span jump from ~0 to material
- **Caller-side SDK change** (MSAL/MSAL_CPP/OneAuth release) that batches or caches requests → uniform per-app request drop with flat devices
- **Flight rollout** (ECS) that gates a code path on/off → bursty changes in a specific span on specific dates

Cite the suspect PR(s) with the same confidence ratings used in Code Attribution (high / medium / low / none) and the same `pr-card` markup. If you can't pin one down, say so explicitly — *"uniform 5–22% per-app request drop with flat devices, no telemetry-platform PR identified, suspect caller-side SDK change in MSAL release X.Y"* is acceptable; "traffic is flat" without checking is not.

**6c. Per-error traffic attribution (is the *error* spike traffic-driven?).** For every error code flagged in Step 5 as a regression, additionally check whether the spike is *traffic-driven* rather than *failure-rate-driven*:

```kql
let target_code = "<error_code>";
materialized_view('ErrorStatsMetrics')
| where EventInfo_Time > ago(14d) and error_code == target_code
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time), calling_package_name
| order by week asc, devs desc
```

If the spike is concentrated in a single calling app whose **overall** request volume also rose that week (cross-check `AppStatsUpdated`), and the **per-request failure rate is essentially flat**, classify the spike as a **traffic-attribution case** rather than a code regression:

> Example: "`no_account_found` +60% devices this week is fully explained by Outlook's request volume rising 65% — the per-Outlook-request failure rate is unchanged. No broker code change is implicated."

Add a top-level **🚚 Traffic Attribution** section that lists every error matched to a traffic-driven origin, mirroring the Code Attribution section. **Each card must include**: the dominant calling app(s) with their WoW request-volume delta, the per-app per-request failure rate (now vs prior — show it's flat), and the recommended owner to route to (typically the calling app's team, not broker). If no errors qualify in a given week, render the section with an explicit "None this week" note rather than omitting it.

### Step 7 — Validate & write

- Run `get_errors` on the HTML file (no errors expected — pure HTML/CSS).
- Verify no stale phrases from prior weeks remain (`Select-String` for retracted hypotheses, prior week's PR numbers).
- Verify every PR link in the new file is reachable (the file paths just before the link should match what `git log` returned).

---

## Hard rules

- **Never `sum(countDevices)`.** Always `dcount_hll(hll_merge(countDevicesHll))`. Summing the per-row distinct count double-counts.
- **Always wrap view names in `materialized_view('Xxx')`** and use the canonical `Metrics`/`Updated` variants (see cheatsheet § 2).
- **Never sum percentiles.** Latency is a TDigest sketch — `percentile_tdigest(tdigest_merge(responseTimeTDigest), N, typeof(long))` only.
- **Always apply `MergeAccountType` / `MergeIsSharedDevice` / `MergeUiRequiredExceptions`** so this report agrees with the dashboard.
- **Confirm the week bucket label matches the user's intent** before writing the rest of the queries (Sunday-aligned).
- **Never claim "auxiliary spans" or denominator artifacts** without verifying the diff between broker versions in the actual commits.
- **Never report WoW-only verdicts** for errors that are flat-or-down WoW but rising on 60d — always cross-check both windows.
- **Never page** based on a regression that turns out to be a downstream of a denominator shift; always include the auth-only-denominator number alongside the all-spans number.
- **Always cite PRs** with full GitHub URLs (the repo URL patterns above), not bare commit SHAs.
- **Do not create a separate Markdown summary** of the report — the HTML *is* the deliverable.
- **Do not commit** the report file. It lives in `$env:USERPROFILE\android-oce-reports\` (outside the workspace) precisely so it can't be staged accidentally.

---

## Output checklist

- [ ] New `oncall-wow-report-YYYY-MM-DD.html` (where `YYYY-MM-DD` is the reporting-week Sunday) exists at `$env:USERPROFILE\android-oce-reports\` (NOT at repo root).
- [ ] All sections present and populated (incl. 🚚 Traffic Attribution — even if “None this week”)
- [ ] **60-day trend bucketing run on the full cross-product** — `{error_code, error_type} × {devs, reqs}` = 4 runs — union of regressions reported. Per-request retry storms (e.g. small device pool, exploding request count) are flagged on both axes.
- [ ] **Both error-codes AND error-types WoW tables have `Δ reqs %` and `Δ devs %` columns**, the 60d sparkline, and a status pill. Any row crossing threshold on either metric is in the regression list.
- [ ] Every WoW regression AND every 60d regression — **for both `error_code` and `error_type`** — has its own spike-attribution card with all 7 dimensions sliced.
- [ ] **Every `error_type` regression card includes the 8th-dimension sub-code decomposition** showing the top 3–5 contributing `error_code`s with their Δ vs prior week, and links to those sub-codes' own attribution cards.
- [ ] **Every regression card's Code Attribution block populates Originator + Top throw site + Wrapper + Caller hot-spots + Underlying cause + Top error_messages + Likely PRs (with confidence/why-it's-the-suspect) + Next step (with named owner)** — per [`assets/code-attribution-template.md`](assets/code-attribution-template.md). For type cards, the wrapper field focuses on the type's catch-and-rethrow site (e.g. `BaseException`, `ServiceException` constructor). Shallow PR-only attribution is not acceptable.
- [ ] Non-broker errors are explicitly tagged `environmental` / `non-broker` with confidence `none` — not invented broker PRs.
- [ ] Traffic analysis covers totals, per-app, per-span, requests-per-device ratio (per error AND overall), and a sampling-change check.
- [ ] **Every material traffic shift (>10% on any segment, up or down) has a reasoning paragraph** that names the dominant span/app/active-broker/broker-version, and either cites a causal PR (with confidence) — span removed/added, `goAsync()` refactor, sampling change, caller-side SDK release, ECS flight ramp — or explicitly says "no PR identified, suspect X" rather than leaving it unexplained.
- [ ] Auth-only denominator used for all reliability %s, denominator caveat called out at top.
- [ ] No stale text from previous weeks.
- [ ] `get_errors` clean on the HTML file.
