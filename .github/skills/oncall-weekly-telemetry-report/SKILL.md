---
name: oncall-weekly-telemetry-report
description: Generate the weekly Android Broker on-call (OCE) WoW + 60-day trend telemetry report as a polished self-contained HTML file. Use this skill for the weekly OCE rotation when asked to "produce the OCE report", "weekly on-call report", "WoW telemetry report", "weekly broker health report", or "generate this week's on-call summary". Pulls from `android_spans` materialized views, attributes regressions/improvements to PRs in `broker/` and `common/`, and writes to `$env:USERPROFILE\android-oce-reports\oncall-wow-report-YYYY-MM-DD.html` (outside the workspace so reports are never committed).
---

# OCE Weekly Report

Produce the weekly Android Broker on-call (OCE) telemetry report as a self-contained HTML file at `$env:USERPROFILE\android-oce-reports\oncall-wow-report-YYYY-MM-DD.html` (where `YYYY-MM-DD` is the reporting-week Sunday — see "Inputs to confirm" §4). Writes to the user's home folder, **outside the workspace**, so reports never accidentally get committed.

The output mirrors the structure of the canonical template at [`assets/templates/report-template.html`](assets/templates/report-template.html). The Step 1 bootstrap script copies the template into `~/android-oce-reports/oncall-wow-report-<sunday>.html` and you edit it in place from there. Do **not** redesign the layout each week.

**Before writing any KQL, read [`assets/docs/kusto-cheatsheet.md`](assets/docs/kusto-cheatsheet.md).** It captures the canonical view names, helper functions, the HLL device-count gotcha, week-alignment rules, and ready-to-paste query templates — distilled from the production Android Broker Dashboard.

Reusable helpers in [`assets/`](assets/):

| File | Purpose |
|---|---|
| [`report-template.html`](assets/templates/report-template.html) | Canonical layout — a real prior-week report kept verbatim. **Edit in place** (replace dates / values / verdicts / PR links); do not restyle. See [`template-readme.md`](assets/templates/template-readme.md) for what to change vs leave alone. |
| [`template-readme.md`](assets/templates/template-readme.md) | Author guide for `report-template.html` — what to change per week, color palette, CSS class quick-reference |
| [`kusto-cheatsheet.md`](assets/docs/kusto-cheatsheet.md) | Schemas, helper funcs, gotchas, ready-to-paste KQL templates, AADSTS reference |
| [`code-attribution-template.md`](assets/docs/code-attribution-template.md) | Per-card checklist for the deep code-attribution block (Originator / Top throw site / Wrapper / Caller hot-spots / Underlying cause / Top error_messages / Likely PRs / Next step) |
| [`queries/`](assets/queries/) | Canonical KQL templates, one file per query — see [`queries/README.md`](assets/queries/README.md). Highlights: [`attr-union-by-dim.kql`](assets/queries/attr-union-by-dim.kql) (NEW — all 7 dims in one round-trip), [`error-message-and-location.kql`](assets/queries/error-message-and-location.kql) (now accepts BOTH `<CODES_LIST>` and `<TYPES_LIST>` in one call) |
| [`templates/`](assets/templates/) | Copy-paste HTML snippets (`spike-card.html`, `traffic-attr-card.html`, `sparkline-footer.html`) |
| [`bucket-trends.js`](assets/scripts/bucket-trends.js) | Bucket all error codes into 60-day regression / spike / improvement / flat. Run with `--metric=devs` AND `--metric=reqs`. Pass `--end=YYYY-MM-DD` (Sunday after the reporting week, exclusive) to drop the partial in-progress bucket. **`--summary` suppresses the verbose header; `--json=<path>` emits a structured sidecar for programmatic consumption.** |
| [`agg.js`](assets/scripts/agg.js) | Per-error per-dim top-N rollup with WoW deltas. Workhorse for filling spike-attribution dim blocks. |
| [`summarize-attribution.js`](assets/scripts/summarize-attribution.js) | Roll up 7-dim attribution slices for spike-attribution cards. Supports BOTH `--union <file.json>` (preferred for 2-week WoW; pairs with `attr-union-by-dim.kql`) AND legacy `--label=<dim> file.json` per-dim mode. **Auto-detects the array-form schema produced by `assets/scripts/run-kql.ps1` — no schema-transformer step needed.** |
| [`find-suspect-prs.ps1`](assets/scripts/find-suspect-prs.ps1) | Parallel `git log -S` + `--grep` across broker/ + common/ for a class/method symbol, with PR numbers + URLs. Run *only after* the Originator pre-check has identified a specific throw-site class — the unscoped 4-week PR window is small enough (<30 PRs) to scan with plain `git log` first. |
| [`validate-report.ps1`](assets/scripts/validate-report.ps1) | Pre-publish validator. Catches stale tokens, devs/reqs leaks, mojibake (U+FFFD), unbalanced `<div>` depth in Section 2 (the nested-callout bug), KPI/trend sparkline coverage, code-attribution depth, layout-guard CSS presence, and suspicious low-peak fabricated `data-trend` arrays. Run as part of Step 7. |
| [`scripts/run-kql.ps1`](assets/scripts/run-kql.ps1) | **Direct-REST Kusto helper — drop-in fallback for the Azure Kusto MCP server when the MCP times out** (frequent on per-error-code queries). Acquires a token via `az`, POSTs to `/v2/rest/query`, writes a JSON file the JS helpers can consume directly. |
| [`scripts/bootstrap-report.ps1`](assets/scripts/bootstrap-report.ps1) | Bootstrap a new week's report from the canonical template. Auto-computes the reporting Sunday, creates `_data/<sunday>/`, prunes `_data` folders older than 60 days, and detects "unfilled template stub" vs "real prior report" collisions using a multi-marker fingerprint (title + meta date + first KPI value + size ratio). |
| [`scripts/visual-smoke.ps1`](assets/scripts/visual-smoke.ps1) | Optional Playwright-based layout smoke test. Renders the report at 1400 px viewport, captures a full-page screenshot under `~/android-oce-reports/_visual/`, and runs DOM-based overflow + adjacent-card-gap detection. Catches the rendered-layout bugs (text bleed, cards touching) that pure HTML/CSS validation can't see. |

---

## Inputs to confirm with the user

1. **Reporting week** — **first compute the most recent complete Sun→Sat week** (Sunday bucket = the most recent Sunday strictly before today, or today itself if today is a Sunday and the week's data is at least 6h old). Default to that and proceed without asking *unless*:
   - today is itself a Sat or Sun **and** the user phrasing suggests they want "this week" (e.g. "current report", "latest data"). Then ASK explicitly between the in-progress and most-recent-complete options.
   - today is a Mon–Fri — just default to the most recent complete week and proceed; do not ask.

   If the user picks the in-progress week:
   - Add the badge text *"Live data — current bucket may still be filling"* to the report header.
   - The `bucket-trends.js` `--end` flag + the `| where week < datetime(<END>)` source filter both still apply (use the Sunday AFTER the reporting week as `<END>`); they will drop the partial-end-bucket warning.

   Note that Kusto's `startofweek()` is **Sunday-aligned**, so a user-spoken "week of May 3 → May 9" maps to the bucket `startofweek == 2026-05-03`. Off-by-one-week is the #1 silent error — verify by printing the distinct `startofweek` buckets from your first query and confirming the label matches the user's intent.
2. **Comparison baseline** — defaults to the prior complete week.
3. **60-day window** — last 8 complete weeks (drop the partial start week when computing trend deltas).
4. **Output filename** — `$env:USERPROFILE\android-oce-reports\oncall-wow-report-YYYY-MM-DD.html`, where `YYYY-MM-DD` is the **Sunday `startofweek` bucket** of the reporting week (e.g. the report for the week of May 3 → May 9, 2026 is `oncall-wow-report-2026-05-03.html`). User-scoped, outside the workspace; the date matches the Kusto bucket label used throughout the report.

If any of these are unstated, ask once, then proceed.

---

## Required sections (in order)

1. **Top-line health KPIs** — total requests, total devices, silent-auth reliability %, interactive reliability %, p95 latency on the hot spans. WoW delta on each. Inline SVG sparklines.
2. **Things that need attention this week** — callouts:
   - **Denominator caveat** — explain any large total-spans device-count shift caused by span-emission changes (e.g. `goAsync()` refactors). Always state which denominator the report uses (auth-only: `SilentAuthStats` ∪ `InteractiveAuthStats`).
   - **🔴 WoW regressions (last 7 days)** — *one* callout listing every code/type that moved sharply WoW, **sorted by current-week device count descending**. Built from the union of (a) the standard WoW table and (b) [`assets/queries/wow-movers.kql`](assets/queries/wow-movers.kql) so small-but-recent spikes appear in the same list as the high-volume ones. Each row uses the `.item` flat-row pattern (see `assets/templates/template-readme.md` § "Section 2 callouts"): name + inline metric chips + tags pushed right + one-line body + optional foot with `Attribution card →` link. **Section 2 rows are at-a-glance only** — do not duplicate the dim slicing / PR analysis / detailed verdict here; that belongs in the Section 4 spike-attribution card. Each row carries tags: `NEW` (first appeared this week or last), `60d↑` (also rising on 60d), and an originator chip (`broker` / `eSTS` / `Android` / `env`). Reader's eye prioritizes naturally by row order and tag combination — broker-tagged rows at the top demand the most attention.
   - **Slow-burn 60-day regressions** — codes/types climbing on the 60d window that are flat WoW. Anything that *also* moved WoW belongs in the red callout above (with `60d↑`), not here. Link to the 60-Day Trend section.
   - **Real wins this week**, with PR links.
   - **Traffic shape** — flat / surge / collapse summary.
3. **📈 60-Day Trend Analysis** — built from the `ErrorStatsMetrics` materialized view over the last 8 complete weeks. **Run the bucketing pipeline FOUR times — the cross-product of `{error_code, error_type} × {devices, requests}`** — and union the regression sets. An entry (code OR type) is flagged if it regresses on either metric.

   - **% of devices** affected (`devicesHit / authActiveDevices`) — catches errors hitting more users.
   - **% of requests** affected (`errRequests / authTotalRequests`) — catches per-device retry storms (fewer users, more traffic per user). The previous report would have missed `kdfv2_key_derivation_error` (262 → 5,374 requests on ~57 devices) without this dim.

   Categories: True 60d regression / Ephemeral 60d spike (peak-then-recover) / True 60d improvement / Flat. Every rising entry — whether `error_code` or `error_type` — gets the same Spike Attribution + Code Attribution treatment (Step 4 / Step 5).

   Always apply `MergeUiRequiredExceptions(error_type)` before bucketing on type; otherwise the 6+ string variants of `UiRequiredException` will each be tracked separately and skew the buckets.
4. **🔎 Spike Attribution** — one card per WoW regression AND per 60-day regression, **for both `error_code` and `error_type` regressions**. Each card slices on **all 7 dimensions** (broker version, span, active broker pkg, calling app, account type AAD/MSA, shared-device mode, client SKU). Each card ends with a **deep Code Attribution block** (see Step 4 for the required fields) and a Traffic Attribution verdict.
5. **🚚 Traffic Attribution** — top-level section listing every error whose spike is fully or partly explained by traffic volume from a specific calling app, rather than a code regression. If none qualify this week, render the section with an explicit "None this week" note.
6. **Error codes — WoW with stable denominator** — full table with `Δ requests %` and `Δ devices %` columns and the 60d sparkline.
7. **Error types — WoW with stable denominator** — full table, **same columns and rigor as the error-codes table** (`Δ requests %`, `Δ devices %`, 60d sparkline, status pill). Any regressing type also gets a spike-attribution card in Section 4. For composite types (e.g. `ClientException` is the umbrella for many sub-codes), include a **decomposition card** that breaks the WoW Δ down into the top 3 contributing sub-codes — so a `ClientException` −5 pp drop is explicitly attributed to e.g. `−8.5 pp timed_out_execution` + `−3.4 pp unknown_authority` + `−0.15 pp illegal_argument_exception`.
8. **📊 Traffic analysis** — total requests/devices (WoW + 60d), top calling apps, top spans, **requests-per-device ratio** per error and overall (a rising ratio = retry storm; a falling ratio = caching gain), sampling-rate change indicator.
9. **Latency** — p50/p95/p99 by hot span.
10. **Broker version adoption** — week-over-week version share.
11. **Appendix** — query list and methodology.

---

## Step-by-step workflow

### Step 1 — Bootstrap the new report file from the template

This skill ships with a canonical template at [`assets/templates/report-template.html`](assets/templates/report-template.html) (a real prior week's report kept as the reference layout). **Use [`assets/scripts/bootstrap-report.ps1`](assets/scripts/bootstrap-report.ps1)** to handle all the boilerplate (Sunday-date computation, `_data/<sunday>/` directory, retention-pruning, collision detection):

```pwsh
.\.github\skills\oncall-weekly-telemetry-report\assets\scripts\bootstrap-report.ps1
# Optional: explicit reporting Sunday + force overwrite
# .\bootstrap-report.ps1 -ReportingSunday 2026-05-31 -Force
```

What it does:
* Computes the reporting-Sunday from the system clock (most recent complete Sun-Sat week).
* Creates `~/android-oce-reports/oncall-wow-report-<sunday>.html` from the canonical template.
* Creates `~/android-oce-reports/_data/<sunday>/` for raw KQL JSON payloads.
* Prunes `_data/<old-sunday>/` folders older than 60 days so the cache doesn't accumulate.
* **Collision detection (the v8-hardened version):** uses a multi-marker fingerprint (title + meta-line dates + first-KPI value + size ratio) to distinguish an "unfilled template stub" (silently re-bootstrap) from a "real populated report" (HARD HALT, exit 2, require `-Force` to overwrite). The earlier single-marker (title only) version misclassified populated reports as stubs and overwrote real work.

Edit the bootstrapped file in place — the template ships as a real prior-week report (not a tokenized skeleton). **Walk top-to-bottom and replace every prior-week date / KPI value / table row / verdict / PR citation with current-week data.** The CSS, sparkline JS, section ordering, and attribution-card markup are canonical — do not redesign them. See [`assets/templates/template-readme.md`](assets/templates/template-readme.md) for the full guide on what to change vs leave alone, the sparkline color palette, the CSS class reference, and the two v8 layout traps.

> **⚠️ UTF-8 trap — DO NOT use PowerShell `@'...'@` heredocs to compose HTML content containing emojis, em-dashes, arrows, or middle dots.** PowerShell silently strips multi-byte UTF-8 characters when piping heredocs to `Set-Content` / `Out-File`. Use Node.js (`fs.writeFileSync`), `[IO.File]::WriteAllText($path, $text, [System.Text.UTF8Encoding]::new($false))`, or explicit Unicode-pair literals (`[char]0xD83D + [char]0xDCCA` for 📊) instead. This trap cost ~30 min in v8 and required a full emoji-restoration pass — every callout icon, every section header emoji, every arrow link had to be re-injected. The validator's `U+FFFD` check catches the worst case (mojibake replacement char) but cannot detect characters that were silently stripped to nothing.

Mark any unfinished card or table cell with the literal sentinel `EXAMPLE CONTENT BELOW` inside an HTML comment — the final-pass validator (Step 7) greps for it.

If the template ever needs structural improvements (new section, new card style, etc.), update `assets/templates/report-template.html` in the skill folder and commit it so future weeks inherit the change.

### Step 2 — Pull WoW reliability data

Use the Kusto MCP tool against:
- **Cluster:** `https://idsharedeus2.kusto.windows.net`
- **Database:** `ad-accounts-android-otel`

**Always prefer the canonical `materialized_view('XxxMetrics' or 'XxxUpdated')` variants** — these are what the production dashboard uses, are pre-aggregated and HLL-bucketed, and avoid the 240 s MCP timeout that plain `android_spans` queries hit. Full schema, gotchas, and query templates: [`assets/docs/kusto-cheatsheet.md`](assets/docs/kusto-cheatsheet.md).

> **Fallback when the Kusto MCP times out:** use [`assets/scripts/run-kql.ps1`](assets/scripts/run-kql.ps1). It acquires a token via `az account get-access-token`, POSTs directly to `/v2/rest/query`, and writes the result as a JSON file the JS helpers (`bucket-trends.js`, `summarize-attribution.js`) can consume directly. The skill's MCP-vs-REST switch is roughly: try the MCP once; if it returns `McpError -32001 (timeout)`, switch to the REST helper for the rest of the run. Run multiple queries in parallel via PowerShell `Start-Job`:
>
> ```pwsh
> $queries = @{ 'reliability.json' = $reliabilityKql; '60d-codes.json' = $codesKql; ... }
> $jobs = @()
> foreach ($f in $queries.Keys) {
>   $q = $queries[$f]
>   $jobs += Start-Job -ScriptBlock {
>     param($Q, $O) & "$using:skillRoot\assets\scripts\run-kql.ps1" -Query $Q -Out $O
>   } -ArgumentList $q, $f
> }
> $jobs | Wait-Job | Receive-Job; $jobs | Remove-Job
> ```

| Need | View |
|------|------|
| Per-error-code / per-error-type / per-span counts | `materialized_view('ErrorStatsMetrics')` |
| Total broker requests / devices | `materialized_view('BrokerAdoptionStatsUpdated')` |
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

Use [`assets/queries/60d-trend-codes.kql`](assets/queries/60d-trend-codes.kql) (template; replace `<START>` and `<END>` tokens — `<END>` is **exclusive** and equals the Sunday AFTER the reporting week, e.g. for a 2026-05-03 report use `2026-05-10`):

```kql
materialized_view('ErrorStatsMetrics')
| where EventInfo_Time between (datetime(<START>) .. datetime(<END>))
| where isnotempty(error_code) and error_code != 'success'
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time), error_code
| where week < datetime(<END>)   // drop partial in-progress week at the source
| order by error_code asc, week asc
```

**The `| where week < datetime(<END>)` line is mandatory.** Without it, if Kusto has crossed midnight UTC into the next Sunday, a tiny partial bucket lands as `last` and turns every code into a fake −99% improvement. `bucket-trends.js` will also auto-detect and warn about this, but filtering at the source is preferred.

#### 3b. Per-error-type trend (same rigor)

```kql
materialized_view('ErrorStatsMetrics')
| extend unified_error_type = MergeUiRequiredExceptions(error_type)
| where EventInfo_Time between (datetime(<START>) .. datetime(<END>))
| where isnotempty(unified_error_type)
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time), unified_error_type
| where week < datetime(<END>)
| order by unified_error_type asc, week asc
```

`MergeUiRequiredExceptions` is mandatory — without it the 6+ string variants of `UiRequiredException` (raw, fully-qualified, com.microsoft.identity.common.exception.*) each show as separate rows and skew the buckets.

#### 3c. Run the bucketer 4 times (cross-product of `{code, type} × {devices, requests}`)

`bucket-trends.js` defaults to grouping by `error_code`. For the type runs you MUST pass `--key=unified_error_type` so it picks up the right column from the type-trend JSON.

```pwsh
# Error codes — by devices, then by requests
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <codes.json> --start=2026-03-08 --end=2026-05-10
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <codes.json> --start=2026-03-08 --end=2026-05-10 --metric=reqs

# Error types — by devices, then by requests (note --key)
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <types.json> --start=2026-03-08 --end=2026-05-10 --key=unified_error_type
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <types.json> --start=2026-03-08 --end=2026-05-10 --key=unified_error_type --metric=reqs
```

`--end` is the Sunday AFTER the reporting week (exclusive). The script also auto-detects partial end-buckets and warns, but passing `--end` explicitly is safer.

Take the **union** of all four regression sets. Both `error_code` and `error_type` regressions get a spike-attribution card in Step 5.

It will print regression / spike / improvement / flat buckets, sorted by peak. The thresholds (in case you need to tune):

- **True 60d regression:** `delta > +15%` and trajectory is monotonic-ish (no single-week spike dominating).
- **Ephemeral 60d spike:** peak week is ≥3× the mean of the surrounding weeks (peak-then-recover shape).
- **True 60d improvement:** `delta < −15%`.
- **Flat:** otherwise.
- Codes/types with peak weekly devices `< 10K` (or peak weekly requests `< 100K` when `--metric=reqs`) are filtered out (`--peak-floor=N` to override).

**Why both axes matter:**
- *codes × requests:* in v5, `kdfv2_key_derivation_error` spiked +1,951% on requests across only ~57 devices — a per-device retry storm device-only bucketing would have missed.
- *types × either:* `error_type` is the umbrella (e.g. `ClientException`, `ServiceException`, `UiRequiredException`) — a moving type that doesn't map cleanly to one moving code is a strong signal of a *new* sub-code being introduced or an existing one being reclassified (the v5 `ClientException` −10% drop was driven by `timed_out_execution` reclassification under PR #141, which would have been invisible from the codes table alone).

**Always present side-by-side WoW tables for BOTH error_code AND error_type** with `Δ requests %` and `Δ devices %` columns; flag any row where either crosses threshold.

#### 3d. WoW movers query — MANDATORY pass to catch small-base movers

The 60d bucketer's `--peak-floor=10000` exists for good reason (otherwise the 60d regression list would be 200+ tiny noise codes), but it **silently drops every code whose absolute weekly volume stays under 10K** — even if that code is brand-new or just spiked 5× WoW. Real examples this skill has missed in the past:

- `Failed to parse JWT` — went `7 → 32 → 54 → 46 → 55 → 892 → 3,461` over 7 weeks (2-week-old NEW spike, real broker code in `IDToken.parseJWT:38`). Never crossed the 10K floor.
- `Code:-11` — sat at ~1,030 devs/wk for 7 weeks then jumped to 2,433 (+165% WoW). Sub-floor.
- `SSLHandshakeException` — devices flat at 260 but requests +186% WoW (per-device retry storm). The bucketer's reqs-axis floor (100K) just barely captures it but the device floor doesn't.

To catch these, **always** run [`assets/queries/wow-movers.kql`](assets/queries/wow-movers.kql) **as a separate pass after the 60d bucketing**:

```kql
// inputs: <CURR_START> = reporting-week Sunday, <CURR_END> = next Sunday (excl),
//         <PRIOR_START> = baseline-week Sunday
// floor: cDev>=500 OR cReq>=5000   move: |Δd|>=25% OR |Δr|>=50% OR new-this-week
```

Run it **twice — once for `error_code`, once for `error_type`**. **Merge its output rows into the same 🔴 WoW regressions callout as the standard WoW table** (sorted by current-week device count descending). Tag rows that came in via this pass with `NEW` if they were absent or near-zero in the prior week. Do *not* render this as a separate "emerging" callout — the size split is implementation detail; readers prioritize naturally by absolute device count + originator chip.

For each WoW mover (regardless of size), you still owe the full Code Attribution treatment (Step 4). The dim-slicing pass (Step 5) is allowed to be deferred for sub-1K-device spikes if the throw-site + dominant message already pin the originator unambiguously — but say so explicitly in the card ("dims not yet sliced — file the bug first; pull dims if it persists").

### Step 4 — Code attribution (deep PR correlation)

> ⚠️ **HARD RULE — Originator pre-check.** Before claiming `Originator: Broker` on any card, you MUST run [`assets/queries/error-message-and-location.kql`](assets/queries/error-message-and-location.kql) for that error code (or type) and read **(a) the throw-site stack and (b) the top 3 `error_message` strings**. Most broker error codes flow through `common/ExceptionAdapter.{getExceptionFromTokenErrorResponse, exceptionFromAuthorizationResult, clientExceptionFromException}` — which intentionally bridge eSTS responses into broker exceptions. **If the throw site is in any of those three methods AND the error_message starts with `AADSTS`, the originator is eSTS, not broker.** See the AADSTS reference table in [`assets/docs/kusto-cheatsheet.md`](assets/docs/kusto-cheatsheet.md). Cards that skip this step must be marked low-confidence, not high.
>
> **Window:** use the FULL 7-day reporting window (`<CURR_START>` → `<CURR_END>`) on `PipelineInfo_IngestionTime`, NOT a narrower 3–5 day slice — low-volume types (e.g. `SSLHandshakeException`, `IntuneAppProtectionPolicyRequiredException`) routinely return zero rows in a sub-week window. If a code/type still returns nothing, fall back to the prior 14 days before declaring "no data".

For every regression card, the Code Attribution block **must** populate the following fields. Shallow PR-citation only is not acceptable. Use [`assets/docs/code-attribution-template.md`](assets/docs/code-attribution-template.md) as the per-card checklist.

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

**Read the full PR window first, then reason — don't `--grep` blind.** The 4-week window across `broker/` and `common/` typically returns &lt;30 PRs total, small enough to read end-to-end. Targeted `--grep` matches will miss PRs whose titles don't mention the error string (most of them). **The recommended order is:**

1. **Run plain `git log` on both repos** for the 4-week window. Read the resulting list end-to-end before any greps.
2. **Cross-reference titles + dates** against the Originator pre-check throw-site class.
3. **Only when you have a specific symbol** to chase (e.g. the throw-site class identified in step 2), reach for `find-suspect-prs.ps1` to do the symbol-targeted parallel pickaxe + grep.

The historical mistake (pre-v8) was to jump straight to `find-suspect-prs.ps1` without reading the window first, which silently dropped PRs whose titles didn't mention the symbol.

```pwsh
# Step 1: read the full 4-week window
cd c:\Users\shjameel\Repos\android-complete\broker
git --no-pager log --since='<windowStart>' --until='<windowEnd>' --pretty=format:'%h | %ai | %an | %s' --no-merges

cd ..\common
git --no-pager log --since='<windowStart>' --until='<windowEnd>' --pretty=format:'%h | %ai | %an | %s' --no-merges
```

For each candidate PR, **read the diff** to confirm it touches the throw site / wrapper class identified in the Originator pre-check. Don't cite a PR just because the title mentions a related concept.

```pwsh
# Step 3 (optional): symbol-targeted focused follow-up. Use ONLY after step 1 gave
# you a specific class/method name to chase from the Originator pre-check.
# Searches both repos in parallel via `git log -S` (pickaxe on diff) AND `--grep` (subject).
# Returns a unified table: repo | date | author | sha | PR# | URL | subject.
.\.github\skills\oncall-weekly-telemetry-report\assets\scripts\find-suspect-prs.ps1 `
  -Symbol 'ExceptionAdapter' -Since 2026-04-01 -Until 2026-05-09
```

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

Slice on **all 7 dimensions** for each spike. **Preferred for 2-week WoW attribution: one union query that covers all 7 dims for all regressions in a single round-trip** — see [`assets/queries/attr-union-by-dim.kql`](assets/queries/attr-union-by-dim.kql). Typical payload for 8 codes × 2 weeks × 7 dims is ~800 KB, well under the MCP limit. Pipe the result into `summarize-attribution.js --union <file.json>` (which prints per-dim top-N share + Δ devices + Δ requests for every code). Fall back to the per-dim form ([`attr-codes-by-dim.kql`](assets/queries/attr-codes-by-dim.kql)) only when (a) you need a wider time window, or (b) the union response exceeds payload size.

For `error_type` cards, swap `error_code in (codes)` for `unified_error_type in (types)` and aggregate by the `MergeUiRequiredExceptions(error_type)` extension — otherwise everything else is identical.

> **Low-volume fallback (extends Step 4's pre-check fallback to the 7-dim union):** when a code/type returns sparse dim rows in the 7-day reporting window — typical for sub-1k-device entries like `TimeoutCancellationException`, `JsonSyntaxException`, `kdfv2_key_derivation_error` — widen the union query to **14 days** (`<START>` = baseline-week Sunday − 7d) before declaring "broad — needs targeted slice". The added week of context usually surfaces enough rows to compute concentration percentages. If a code STILL has no concentration after 14 days, mark every dim cell as "not sliced — sub-week volume; file the bug first, slice on persistence" — do NOT fabricate "Broad" verdicts.

| # | Dimension | Source | Cross-check |
|---|-----------|--------|-------------|
| 1 | Broker version | `ErrorStatsMetrics` group by `broker_version` | Cross-reference `BrokerAdoptionStatsUpdated` to see if the version's request share *also* moved that week — if yes, the spike is rollout-driven, not code-driven |
| 2 | Span name | `ErrorStatsMetrics` group by `span_name` | A single span hosting >60% of the error → strong code-path signal |
| 3 | Active broker package | `ErrorStatsMetrics` group by `active_broker_package_name` | E.g. CompanyPortal vs Authenticator vs LTW |
| 4 | Calling package | `ErrorStatsMetrics` group by `calling_package_name` | If 1–2 callers dominate, this is likely a traffic-attribution case (see Step 6) |
| 5 | Account type (AAD vs MSA) | `ErrorStatsMetrics`, `extend t = MergeAccountType(account_type)` group by `t` | If the split deviates significantly from fleet (~85% AAD / 15% MSA), call it out |
| 6 | Shared device mode | `ErrorStatsMetrics`, `extend s = MergeIsSharedDevice(is_shared_device)` group by `s` | Shared-device fleets have very different error profiles |
| 7 | OS version | [`assets/queries/os-version-slice.kql`](assets/queries/os-version-slice.kql) — raw `android_spans`, group by `DeviceInfo_OsVersion` | **On-demand only** — slice OS-version when EITHER (a) the wrapper class is in `ExceptionAdapter.clientExceptionFromException` (catch-all wrapping a system exception, where the OEM/version often is the cause), OR (b) the error code is one of `Code:-6`, `Code:-10`, `Code:-11`, `unknown_crypto_error`, `io_error`, `null_pointer_error`. Otherwise mark the dim row as "not sliced this week — no OEM concentration suspected" and move on. Slicing OS-version on every card wastes a raw-spans query without changing the verdict. |

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

Feed the union JSON output into the summarizer (one round-trip):

```pwsh
# Union mode (preferred). attr-union.json comes from attr-union-by-dim.kql.
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\summarize-attribution.js `
  --union attr-union.json --top=5
# For type cards, add --key=unified_error_type
```

Legacy per-dim mode (one JSON per dimension) is still supported for the rare wider-time-window case:

```pwsh
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\summarize-attribution.js `
  --label=span span.json `
  --label=calling_app app.json `
  --label=active_broker ab.json `
  --label=broker_version ver.json `
  --label=acct_type acct.json `
  --label=shared_dev shared.json `
  --label=client_sku sku.json
```

Ready-to-paste KQL for both forms: union → [`assets/queries/attr-union-by-dim.kql`](assets/queries/attr-union-by-dim.kql); per-dim → [`assets/docs/kusto-cheatsheet.md` § 8c](assets/docs/kusto-cheatsheet.md).

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

Run the bundled validator FIRST — it covers all the silent-failure cases this skill has tripped on in the past:

```pwsh
.\.github\skills\oncall-weekly-telemetry-report\assets\scripts\validate-report.ps1
# defaults to most-recent oncall-wow-report-*.html under ~/android-oce-reports/
# pass -Path explicitly to validate a specific file
```

The validator hard-fails on:
1. Stale `{{...}}` tokens or `EXAMPLE CONTENT BELOW` / `EXAMPLE_*` sentinels.
2. `devs` / `reqs` in user-facing text (KQL inside `<pre><code>` is exempted).
3. `U+FFFD` replacement characters (catches mojibake from emoji edits).
4. Unbalanced `<div>` depth in the Section 2 attention block (catches the inception-style nested-callout bug from past runs).
5. A second callout opening before the previous one closes (nested-callout sanity check).
6. **Chartless KPI grid** — if more than half the `.kpi` tiles lack a `data-spark` element (catches the v7 regression where the body was rebuilt without sparklines). Also warns when total chart count (sparks + trends + inline svgs) is &lt; 15.
7. **Code-attribution depth** — each `.attr-card`'s "Code attribution" block must contain an `Originator` row (proxy for the full 8-field structure: Originator / Top throw site / Wrapper / Caller hot-spots / Underlying cause / Top error_messages / Likely PRs / Next step). Catches the v7-third-pass regression where cards shipped with a `pr-list`-only stub.
8. **Attribution-card layout guards (v8)** — the CSS must define `.attr-card { margin-bottom: 16px }` AND `.dim-row` overflow rules (`text-overflow: ellipsis` + `min-width: 0`). Catches the "cards touching" and "text bleeding out of dim boxes" regressions from a stale `<head>` block.
9. **Fabricated-sparkline heuristic (v8)** — warns when a `data-trend` array's peak value is < 100 (almost certainly hand-rolled rather than sourced from real data). See [`assets/queries/wow-table-sparkline-series.kql`](assets/queries/wow-table-sparkline-series.kql) for the canonical KQL that pulls real 8-week series for every code in the WoW tables.

Then:
- **Run the visual smoke test (recommended)** — catches rendered-layout bugs that pure HTML/CSS validation can't see:

  ```pwsh
  .\.github\skills\oncall-weekly-telemetry-report\assets\scripts\visual-smoke.ps1
  # Opens the report at 1400px in headless Chromium via Playwright, captures a
  # full-page screenshot to ~/android-oce-reports/_visual/, and runs DOM-based
  # checks for:
  #   - element overflow inside .dim / .attr-card (catches "text bleeding out")
  #   - adjacent .attr-card pairs with gap < 8px (catches "cards touching")
  # First run auto-installs Playwright + Chromium into %LOCALAPPDATA%\oce-skill-playwright
  ```
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
- **Always filter the partial in-progress week at the source** with `| where week < datetime(<END>)` where `<END>` is the Sunday immediately after the reporting week. Otherwise `bucket-trends.js` will show every error as a fake −99% improvement once UTC has crossed midnight Sunday.
- **Never carry a numeric telemetry value forward between runs.** Every KPI, table cell, delta %, device/request count, sparkline point, and verdict number must be re-pulled from Kusto for *this* week — never copied from last week's report, from a checkpoint/summary, from notes, or from memory. Telemetry shifts week to week and stale numbers read as fabricated. Near-miss precedent: a `no_tokens_found` count was about to be carried as ~23.7M when the actual current-week value was ~4.86M — a ~5× error that only the re-pull caught. If a number isn't backed by a query result file in this run's `_data/<sunday>/`, it does not go in the report.
- **Never hardcode the "Generated" date.** It is the *run* date (system clock, local), auto-stamped by `bootstrap-report.ps1`. If you rebuild the body programmatically, derive it live with a **local-date** formatter (`new Date().toLocaleDateString('en-CA')` in Node, `(Get-Date).ToString('yyyy-MM-dd')` in PowerShell) — never paste a literal, and avoid `new Date().toISOString().slice(0,10)` (UTC; stamps tomorrow's date when run in the evening in a UTC-negative timezone). The v8 "Generated 2026-06-15 on a 2026-06-18 file" bug came from a hardcoded string in the assembler. (Reporting-week / baseline / 60d window dates are author-set and verified against the user's intended Sunday bucket — see template-readme "Date fields".)
- **Originator pre-check is mandatory.** A card cannot claim `Originator: Broker` without first running [`assets/queries/error-message-and-location.kql`](assets/queries/error-message-and-location.kql) and reading the throw site + top 3 `error_message` strings. If the throw site is in `common/ExceptionAdapter.{getExceptionFromTokenErrorResponse, exceptionFromAuthorizationResult}` AND the message starts with `AADSTS`, the originator is **eSTS, not broker** — see the AADSTS reference in [`assets/docs/kusto-cheatsheet.md`](assets/docs/kusto-cheatsheet.md).
- **WoW-movers pass is mandatory.** The 60d bucketer's `--peak-floor` silently drops sub-10K-device codes, so [`assets/queries/wow-movers.kql`](assets/queries/wow-movers.kql) MUST be run as a separate pass for both `error_code` and `error_type` (per Step 3d). Its output is **merged into the single 🔴 WoW regressions callout**, sorted by current-week device count descending, with rows tagged `NEW` / `60d↑` / originator chip. Do not render a separate "emerging" callout. Skipping the pass is how the Apr 26 `Failed to parse JWT` spike (7 → 3,461 devs over 7 weeks) hid for two reports running.
- **Section 2 callouts are at-a-glance, Section 4 is the deep dive.** WoW / Slow-burn / Wins items in Section 2 use the `.item` flat-row pattern (no nested cards, no per-item left bars — the parent `.callout` border is the only severity affordance). Each row is a single line of metric chips + a one-line body + an `Attribution card →` link to the corresponding `.attr-card` in Section 4. Do NOT duplicate the dim slicing, PR analysis, or detailed verdict between the two sections — Section 4 is where that lives. See [`assets/templates/template-readme.md`](assets/templates/template-readme.md) for the CSS class reference and the example `.item` markup.
- **Never use bash/PowerShell regex to bulk-edit balanced HTML.** This skill has burned twice on regex strip scripts that ate matched-pair `</div>` closes, producing inception-style nested-callout bugs that take a depth-tracking script to find. If you need a structural change to the HTML, make a targeted, single-occurrence string replacement (with explicit before/after context) or rewrite the affected block end-to-end. Never run a `-replace` across the whole file expecting it to leave balance intact.
- **Denominator caveat must cite evidence, not hand-wave.** If you flag a large all-spans device-count shift, run [`assets/queries/broker-version-share-wow.kql`](assets/queries/broker-version-share-wow.kql) (single WoW snapshot) or [`assets/queries/broker-version-share.kql`](assets/queries/broker-version-share.kql) (time-series) and name the version cohort the shift moved with. Do not write "recurring telemetry-shape artifact" without backing data; if you don't have it, drop the callout.
- **"Recovery" still merits a PR citation.** When an error pins to a single old broker version and recovers as that version retires, look for the **fix PR in the version that replaced it** before calling it a "natural rolloff." Often the fix is real and just under-credited.
- **Never report WoW-only verdicts** for errors that are flat-or-down WoW but rising on 60d — always cross-check both windows.
- **Never page** based on a regression that turns out to be a downstream of a denominator shift; always include the auth-only-denominator number alongside the all-spans number.
- **Always cite PRs** with full GitHub URLs (the repo URL patterns above), not bare commit SHAs.
- **Filename collision rule.** If a report file already exists for the same Sunday bucket, do not silently overwrite. Open the existing report, list its top-3 findings, and explicitly state in chat what changed in the new data before regenerating. A second run on the same week without a delta is wasted work.
- **No `devs` / `reqs` in user-facing strings.** All UI text — callouts, table headers, KPI labels, verdicts, badges — must say `devices` and `requests`. Internal variable / column / file names in scripts and JSON can stay short.
- **Do not create a separate Markdown summary** of the report — the HTML *is* the deliverable.
- **Do not commit** the report file. It lives in `$env:USERPROFILE\android-oce-reports\` (outside the workspace) precisely so it can't be staged accidentally.

---

## Output checklist

- [ ] New `oncall-wow-report-YYYY-MM-DD.html` (where `YYYY-MM-DD` is the reporting-week Sunday) exists at `$env:USERPROFILE\android-oce-reports\` (NOT at repo root). If a file for this Sunday already existed, the chat session explicitly stated what changed before regenerating.
- [ ] All sections present and populated (incl. 🚚 Traffic Attribution — even if “None this week”)
- [ ] **60-day trend bucketing run on the full cross-product** — `{error_code, error_type} × {devices, requests}` = 4 runs — union of regressions reported. Per-request retry storms (e.g. small device pool, exploding request count) are flagged on both axes. Source KQL filtered the partial in-progress week with `| where week < datetime(<END>)`.
- [ ] **WoW-movers pass run** ([`wow-movers.kql`](assets/queries/wow-movers.kql)) for BOTH `error_code` and `error_type`. Its output rows are **merged into the single 🔴 WoW regressions callout in Section 2** (sorted by curr-week devices descending), each row tagged `NEW` / `60d↑` / originator chip. No separate "emerging" callout. Every row carries throw-site, dominant message, originator, and a next step. If the WoW callout is empty (rare), render "None this week" rather than omit.
- [ ] **Both error-codes AND error-types WoW tables have `Δ requests %` and `Δ devices %` columns**, the 60d sparkline, and a status pill. Any row crossing threshold on either metric is in the regression list.
- [ ] Every WoW regression AND every 60d regression — **for both `error_code` and `error_type`** — has its own spike-attribution card with all 7 dimensions sliced. Cards are built from [`assets/templates/spike-card.html`](assets/templates/spike-card.html).
- [ ] **Every `error_type` regression card includes the 8th-dimension sub-code decomposition** showing the top 3–5 contributing `error_code`s with their Δ vs prior week, and links to those sub-codes' own attribution cards.
- [ ] **Originator pre-check has been run for every broker-tagged card** ([`error-message-and-location.kql`](assets/queries/error-message-and-location.kql)). Throw site and top 3 `error_message` strings are populated from real data, not from the code map. AADSTS-prefixed messages are tagged `eSTS`, not `Broker`.
- [ ] **Every regression card's Code Attribution block populates Originator + Top throw site + Wrapper + Caller hot-spots + Underlying cause + Top error_messages + Likely PRs (with confidence/why-it's-the-suspect) + Next step (with named owner)**. For type cards, the wrapper field focuses on the type's catch-and-rethrow site (e.g. `BaseException`, `ServiceException` constructor). Shallow PR-only attribution is not acceptable.
- [ ] Non-broker errors are explicitly tagged `environmental` / `non-broker` with confidence `none` — not invented broker PRs.
- [ ] Traffic analysis covers totals, per-app, per-span, requests-per-device ratio (per error AND overall), and a sampling-change check.
- [ ] **Every material traffic shift (>10% on any segment, up or down) has a reasoning paragraph** that names the dominant span/app/active-broker/broker-version, and either cites a causal PR (with confidence) — span removed/added, `goAsync()` refactor, sampling change, caller-side SDK release, ECS flight ramp — or explicitly says "no PR identified, suspect X" rather than leaving it unexplained.
- [ ] Denominator caveat (if used) is backed by [`broker-version-share-wow.kql`](assets/queries/broker-version-share-wow.kql) or [`broker-version-share.kql`](assets/queries/broker-version-share.kql) evidence naming the responsible version cohort. No hand-waving.
- [ ] Auth-only denominator used for all reliability %s, denominator caveat called out at top.
- [ ] No `\bdevs\b` or `\breqs\b` in user-facing text. (`Select-String -Pattern '\bdevs\b|\breqs\b' -CaseSensitive:$false` returns 0.)
- [ ] **Sparklines rendered.** Every `.kpi` tile in the Top-line health section has a `data-spark` array with 8–9 weekly values. Every row in the 60-day trend tables and both WoW tables (codes + types) has a `data-trend` mini-spark. The validator's chart-coverage check passes (KPI coverage ≥1/2 of tiles, total elements ≥15). Past failure mode: the v7 body rebuild dropped all sparklines silently — see `template-readme.md` § "Sparklines are MANDATORY".
- [ ] **Code-attribution depth.** Every `.attr-card`'s Code attribution block uses the full 8-field `<div class="origin-row">` structure (Originator / Top throw site / Wrapper / Caller hot-spots / Underlying cause / Top error_messages / Likely PRs / Next step) per [`assets/docs/code-attribution-template.md`](assets/docs/code-attribution-template.md). A `pr-list`-only stub is **not acceptable** — the validator hard-fails this. Past failure mode (v7 third pass): all 10 cards shipped with PR-only stubs and lost the throw-site / wrapper / underlying-cause analysis.
- [ ] No stale text from previous weeks. (`Select-String -Pattern 'EXAMPLE CONTENT BELOW'` returns 0 — that's the unfinished-section sentinel. The template no longer ships `{{TOKEN}}` placeholders since v2; if the file still contains any `{{`, that's also a leftover.)
- [ ] `get_errors` clean on the HTML file.
