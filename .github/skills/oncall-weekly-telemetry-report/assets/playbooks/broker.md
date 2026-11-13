# Broker playbook — weekly OCE telemetry report

> Invoked by [`SKILL.md`](../../SKILL.md) in `broker` or `both` mode. **Read the router first** —
> it owns the reporting-window resolution, output paths, mode routing, and the shared hard rules
> (UTF-8 trap, never-carry-a-number-forward, never-commit-the-report). This file owns everything
> Broker-specific: required sections, the 7-step workflow, Broker hard rules, and the Broker
> output checklist.

**Cluster:** `https://idsharedeus2.kusto.windows.net` · **Database:** `ad-accounts-android-otel` ·
**Time column:** `EventInfo_Time` · **Distinct devices:** `dcount_hll(hll_merge(countDevicesHll))`

**Before writing any KQL, read [`../docs/kusto-cheatsheet.md`](../docs/kusto-cheatsheet.md).**

## Broker asset map

| File | Purpose |
|---|---|
| [`report-template.html`](../templates/report-template.html) | Canonical layout — a real prior-week report kept verbatim. **Edit in place**; do not restyle. See [`template-readme.md`](../templates/template-readme.md). |
| [`template-readme.md`](../templates/template-readme.md) | Author guide — what to change per week, colour palette, CSS class quick-reference |
| [`kusto-cheatsheet.md`](../docs/kusto-cheatsheet.md) | Schemas, helper funcs, gotchas, ready-to-paste KQL, AADSTS reference |
| [`code-attribution-template.md`](../docs/code-attribution-template.md) | Per-card checklist for the deep code-attribution block |
| [`queries/`](../queries/) | Canonical KQL templates, one per query — see [`queries/README.md`](../queries/README.md) |
| [`templates/`](../templates/) | Copy-paste HTML snippets (`spike-card.html`, `traffic-attr-card.html`, `sparkline-footer.html`) |
| [`bucket-trends.js`](../scripts/bucket-trends.js) | Bucket error codes into 60-day regression / spike / improvement / flat. Run with `--metric=devs` AND `--metric=reqs`. |
| [`classify-novelty.js`](../scripts/classify-novelty.js) | Split movers into NEW / ACCELERATING / ONGOING / VOLATILE / RECOVERY / IMPROVING / STABLE against their own 7-week baseline, and cluster related codes into families. **This is what makes Section 2 readable** — without it the attention list is volume-ranked noise — and it is also the **noise gate**: its `attention` set (`NEW` + `ACCELERATING`), plus at most 2 wins, is all that renders visibly with charts. |
| [`agg.js`](../scripts/agg.js) | Per-error per-dim top-N rollup with WoW deltas |
| [`summarize-attribution.js`](../scripts/summarize-attribution.js) | Roll up 7-dim attribution slices for spike-attribution cards |
| [`find-suspect-prs.ps1`](../scripts/find-suspect-prs.ps1) | Parallel `git log -S` + `--grep` across `broker/` + `common/` for a class/method symbol |
| [`validate-report.ps1`](../scripts/validate-report.ps1) | Pre-publish validator. Run with `-App broker` (the default). |
| [`run-kql.ps1`](../scripts/run-kql.ps1) | Direct-REST Kusto helper — fallback when the Kusto MCP times out |
| [`bootstrap-report.ps1`](../scripts/bootstrap-report.ps1) | Bootstrap a report from the canonical template. Run with `-App broker`. |
| [`visual-smoke.ps1`](../scripts/visual-smoke.ps1) | Optional Playwright layout smoke test |

---
## Required sections (in order)

1. **Top-line health KPIs** — total requests, total devices, silent-auth reliability %, interactive reliability %, p95 latency on the hot spans. WoW delta on each. Inline SVG sparklines.
2. **Things that need attention this week** — callouts:
   - **Denominator caveat** — explain any large total-spans device-count shift caused by span-emission changes (e.g. `goAsync()` refactors). Always state which denominator the report uses (auth-only: `SilentAuthStats` ∪ `InteractiveAuthStats`).
   - **🔴 Regressions — grouped by NOVELTY, never by volume.** Built from [`classify-novelty.js`](../scripts/classify-novelty.js) (Step 3e), unioned with [`assets/queries/wow-movers.kql`](../queries/wow-movers.kql) so small-but-recent spikes land in the same grouping.

     > **The section has a hard budget: ≤ 8 visible rows, counting the wins.** The classifier's
     > `attention` set (`NEW` + `ACCELERATING`) is typically 3–6 series out of 40–50 — that is the
     > whole point — and you may add **at most 2** "Real wins" rows on top. Everything else goes into
     > a collapsed fold. A previous report shipped **13 visible rows and zero charts** while the
     > 60-day section below it carried **38 charts**; a reader could not tell which of the 13 was this
     > week's story. `validate-report.ps1` check 17 warns above 8 rows and it counts **every** visible
     > `.item` row in Section 2, wins included — so budget accordingly.
     >
     > **This budget caps VISIBILITY, never COVERAGE.** Step 5's rule that every regressed code/type
     > must get an attribution card still holds in full — the surplus cards go into a collapsed fold,
     > they are never dropped. See the boxed note under Step 5 for the exact resolution. If you ever
     > find yourself deleting a mandated card to hit 8 rows, you have misread both rules.

     Emit these sub-groups **in this order**, omitting any that are empty:

     1. **🆕 New this week** — label `NEW`: a genuinely boring baseline (cv < 0.25) that took a clean
        step up. **These lead the section.** Typically 0–3 items. If there are none, say *"nothing new
        this week"* explicitly — do **not** promote an `ACCELERATING` or `ONGOING` item to fill the slot.
     2. **🟠 Getting worse** — label `ACCELERATING`: already elevated, but **still climbing** (rising
        over the window, ≥ 10% above its own median, and not falling WoW). This is the "known issue is
        deteriorating" bucket and it is the *only* multi-week category that stays visible.

        > **The classifier's WoW and the headline WoW are the SAME number** — both are the rolling
        > 7-day window, since the trend's final `bin_at` bucket is that window. They should agree to
        > within HLL noise. A sign disagreement means `--start`/`--end` were passed wrong; go fix the
        > invocation rather than writing a hedge into the report. (Before the `bin_at` fix the two ran
        > on different bases and could disagree by 100 points — that was the bug, not a feature.)
        >
        > A row can still be `ACCELERATING` while its **multi-week** climb outpaces this week's step —
        > e.g. up 18% over three weeks but +2% in the last one. Keep the group heading exactly
        > **"Getting worse"** and resolve it *in the row body*: *"Up 18% over three weeks, +2% this
        > week as the ramp flattens. Still ~30% above its own 60-day median — watch, don't close."*
        > The sparkline settles it visually. Do **not** invent a "needs verification" group.
     3. **🔵 Ongoing / known** — label `ONGOING`: elevated but flat. **These go inside a collapsed
        `<details class="fold">`**, summarised by one line ("N codes remain elevated, none accelerating").
        They are still in the report — a reader can open the fold — but they no longer compete with the
        finding. Give each the number of weeks it has been elevated (`weeksElevated` from the classifier)
        so a reader can see it is old news at a glance.
     4. **🔁 Volatile** — label `VOLATILE` (`suppressRatio: true`): high-variance series where a WoW
        percentage is an artifact of a depressed baseline. **Delete the `Δ WoW` chip from the row head
        and put a `vs 60d median` chip in its place** — a caveat in the body does not undo a `+401.8%`
        sitting in the chip row, because the chip is what the eye reads first:
        ```html
        <!-- WRONG: the caveat below is invisible next to this -->
        <span class="metric up"><span class="m-label">Delta WoW</span><span class="m-value">+401.8%</span></span>
        <!-- RIGHT: absolute level + position in the 60-day band -->
        <span class="metric"><span class="m-label">vs 60d median</span><span class="m-value">-94.5%</span></span>
        ```
        `validate-report.ps1` check 15 **hard-fails** any `VOLATILE`/`RECOVERY` row that still carries a WoW chip ≥ 25%.
     5. **↩️ Recovery** — label `RECOVERY`: returning to its normal band after a suppressed week. Explicitly *not* a regression.

     **⚠️ Every visible row carries its own 8-week sparkline.** The shape is what separates a step
     change from ordinary variance, and it must sit *next to the claim* — not in a separate browsable
     section further down. Use the `.item-spark` pattern from the template:
     ```html
     <span class="item-name">ipc_return_null_cursor</span>
     <span class="item-spark trend" data-trend="[41200,40800,41500,41100,40900,41500,52100]"
           data-w="120" data-h="22" data-color="#cf222e"></span>
     <span class="spark-cap">8 wk</span>
     ```
     Colour by direction: `#cf222e` worsening, `#1a7f37` improving, `#9a6700` volatile. The series is
     the same `comparable` array `classify-novelty.js` already read from the trend sidecar — you do not
     run another query for it. `validate-report.ps1` check 16 **hard-fails** any visible attention row
     without one. Rows inside the collapsed fold are exempt.

     **Novelty chips.** Tag each row with its label so the grouping survives skimming:
     `<span class="tag tag-new">NEW</span>`, `tag-accel` for `ACCELERATING`, `tag-ongoing` for
     `ONGOING`, plus `elevated Nw` where the classifier reports `weeksElevated > 1`.

     **Families outrank individuals.** If the classifier emits a `families` entry, render it as ONE row
     naming the family and its members — related codes moving together are one root cause, not N
     findings. Within each sub-group, order by the classifier's `ORDER`/novelty ranking, **not** by
     current-week device volume.

     **Quiet weeks are a valid, good outcome.** If the classifier reports `quietWeek: true` (empty
     attention set), lead the section with the quiet-week banner from the template and keep the fold
     closed. Do **not** manufacture a headline finding — resist the pull to promote the largest flat
     code. A short report that says "nothing new" is more trustworthy than a long one that pads.

     Each row uses the `.item` flat-row pattern (see `assets/templates/template-readme.md` § "Section 2 callouts"): name + sparkline + inline metric chips + tags pushed right + one-line body + optional foot with `Attribution card →` link. **Section 2 rows are at-a-glance only** — no dim slicing or PR analysis here; that belongs in the Section 4 card. Tags: `60d↑` (also rising on 60d) plus an originator chip (`broker` / `eSTS` / `Android` / `env`).

     > **Every row's one-line body must say something specific to that row** — what changed, from what to what, and why it is or isn't alarming. A sentence that would read identically on any other row (*"movement needs owner triage; deep dive below"*) is worthless and the validator will fail the report for it. If you have nothing specific to say, the row does not belong in Section 2.
   - **Real wins this week**, with PR links. These carry sparklines too — a recovery is a shape claim.
     **Cap at 2 rows, and they count against Section 2's ≤ 8 visible-row budget.** A win is worth
     showing; a list of wins is padding.
   - **Traffic shape** — flat / surge / collapse summary.
3. **📈 60-day cross-check** — a **slow-burn detector, not a browsing list**. Built from the
   `ErrorStatsMetrics` materialized view over the **literal last 60 days ending today** (final bar =
   current in-progress week). **Run the bucketing pipeline FOUR times — the cross-product of
   `{error_code, error_type} × {devices, requests}`** — and union the regression sets. An entry (code
   OR type) is flagged if it regresses on either metric. Deltas are computed on complete weeks only;
   the partial current week is charted but excluded from classification.

   - **% of devices** affected (`devicesHit / authActiveDevices`) — catches errors hitting more users.
   - **% of requests** affected (`errRequests / authTotalRequests`) — catches per-device retry storms (fewer users, more traffic per user). The previous report would have missed `kdfv2_key_derivation_error` (262 → 5,374 requests on ~57 devices) without this dim.

   > **⚠️ This section exists to catch what a 7-day window structurally cannot see: something that has
   > crept up ~5%/week for eight weeks and never triggers a WoW alarm.** That is its *only* job.
   >
   > **Chart only the codes it promotes** — series flagged as rising on 60d that are **not already in
   > Section 2**. In a typical week that is **0–3 rows**, and an empty result is the normal, healthy
   > outcome; say "no slow burns this week" and move on. Everything else — the full classification of
   > all 40–50 series — goes into a collapsed `<details class="fold">` **with no chart column at all**.
   >
   > Rationale: a previous report rendered 38 charts here, ~93% of which duplicated rows already in the
   > error tables below, while the attention section above had none. Reviewing 38 long-elevated series
   > every week is exactly the noise that trains an on-call engineer to skim. `validate-report.ps1`
   > check 18 **hard-fails** if this section renders more than 6 charts outside a fold.

   Categories: True 60d regression / Ephemeral 60d spike (peak-then-recover) / True 60d improvement / Flat. Every **promoted** rising entry — whether `error_code` or `error_type` — gets the same Spike Attribution + Code Attribution treatment (Step 4 / Step 5); entries already covered in Section 2 are not re-analysed here, just cross-referenced.

   Always apply `MergeUiRequiredExceptions(error_type)` before bucketing on type; otherwise the 6+ string variants of `UiRequiredException` will each be tracked separately and skew the buckets.
4. **🔎 Spike Attribution** — one card per WoW regression AND per 60-day regression, **for both `error_code` and `error_type` regressions**. Each card slices on **all 7 dimensions** (broker version, span, active broker pkg, calling app, account type AAD/MSA, shared-device mode, client SKU). Each card ends with a **deep Code Attribution block** (see Step 4 for the required fields) and a Traffic Attribution verdict.
5. **🚚 Traffic Attribution** — top-level section listing every error whose spike is fully or partly explained by traffic volume from a specific calling app, rather than a code regression. If none qualify this week, render the section with an explicit "None this week" note.
6. **Error codes — WoW with stable denominator** — full table with `Δ requests %` and `Δ devices %` columns and the 60d sparkline.
7. **Error types — WoW with stable denominator** — full table, **same columns and rigor as the error-codes table** (`Δ requests %`, `Δ devices %`, 60d sparkline, status pill). Any regressing type also gets a spike-attribution card in Section 4. For composite types (e.g. `ClientException` is the umbrella for many sub-codes), include a **decomposition card** that breaks the WoW Δ down into the top 3 contributing sub-codes — so a `ClientException` −5 pp drop is explicitly attributed to e.g. `−8.5 pp timed_out_execution` + `−3.4 pp unknown_authority` + `−0.15 pp illegal_argument_exception`.

> **📌 Sections 6 and 7 keep a sparkline on EVERY row — this is a deliberate exemption from the
> "charts follow findings" rule, decided explicitly. Do not strip them as part of noise reduction.**
> These are **lookup tables**, not a browsing section: the reader arrives with a code in mind, scans
> the `Error code` column for it, and the 60-day sparkline is glanceable context in a cell their eye
> is already on. It costs no extra attention. The noise problem the redesign fixed was the *60-day
> trend catalog* — a section you had to read top-to-bottom, ~93% of whose rows duplicated these very
> tables. Checks 16/17/18 deliberately scope to Section 2 and the 60-day section only; the
> `$totalCharts` count that these ~62 charts dominate is now a floor-only guard ("the charts didn't
> vanish"), never a ceiling.
8. **📊 Traffic analysis** — total requests/devices (WoW + 60d), top calling apps, top spans, **requests-per-device ratio** per error and overall (a rising ratio = retry storm; a falling ratio = caching gain), sampling-rate change indicator.
9. **Latency** — p50/p95/p99 by hot span.
10. **Broker version adoption** — week-over-week version share.
11. **Appendix** — query list and methodology.

---

## Step-by-step workflow

### Step 1 — Bootstrap the new report file from the template

This skill ships with a canonical template at [`assets/templates/report-template.html`](../templates/report-template.html) (a real prior report kept as the reference layout). **Use [`assets/scripts/bootstrap-report.ps1`](../scripts/bootstrap-report.ps1)** to handle all the boilerplate (rolling-window computation, `_data/broker-<curEnd>/` directory, header stamping, retention-pruning, collision detection):

```pwsh
.\.github\skills\oncall-weekly-telemetry-report\assets\scripts\bootstrap-report.ps1 -App broker
# Optional: explicit end-date (curEnd, exclusive upper bound) + force overwrite
# .\bootstrap-report.ps1 -App broker -EndDate 2026-07-02 -Force
```

> `-App broker` is the default and may be omitted, but pass it explicitly in `both` mode so the
> two bootstrap calls in the run transcript are unambiguous.

What it does:
* Resolves the rolling 7-day window from the system clock in UTC (`curEnd = today`, `curStart = curEnd - 7d`, `prevStart = curEnd - 14d`) — or from `-EndDate` if passed.
* Creates `~/android-oce-reports/oncall-wow-report-<curEnd>.html` from the canonical template.
* Creates `~/android-oce-reports/_data/broker-<curEnd>/` for raw KQL JSON payloads.
* **Stamps the resolved window into the report's `<title>`, `<div class="meta">` block, and Generated banner** — you never hand-edit header dates. The resolved window is echoed in the report header for transparency.
* Prunes `_data/broker-<old-end-date>/` folders older than 60 days so the cache doesn't accumulate.
* **Collision detection (fail-safe):** an existing same-day report is silently re-bootstrapped only when it is *positively* identified as an unpopulated stub — it still carries the `OCE-UNPOPULATED-STUB` sentinel that bootstrap injects **and** its first KPI still equals the template's value. Anything else (sentinel removed, or KPIs edited) is treated as real work: **HARD HALT, exit 2**, requiring `-Force` to overwrite. `validate-report.ps1` refuses to pass a report that still carries the sentinel, so a published report can never be misclassified as a stub.

Edit the bootstrapped file in place — the template ships as a real prior-week report (not a tokenized skeleton). **Walk top-to-bottom and replace every prior-week date / KPI value / table row / verdict / PR citation with current-week data.** The CSS, sparkline JS, section ordering, and attribution-card markup are canonical — do not redesign them. See [`assets/templates/template-readme.md`](../templates/template-readme.md) for the full guide on what to change vs leave alone, the sparkline color palette, the CSS class reference, and the two v8 layout traps.

> **⚠️ UTF-8 trap — DO NOT use PowerShell `@'...'@` heredocs to compose HTML content containing emojis, em-dashes, arrows, or middle dots.** PowerShell silently strips multi-byte UTF-8 characters when piping heredocs to `Set-Content` / `Out-File`. Use Node.js (`fs.writeFileSync`), `[IO.File]::WriteAllText($path, $text, [System.Text.UTF8Encoding]::new($false))`, or explicit Unicode-pair literals (`[char]0xD83D + [char]0xDCCA` for 📊) instead. This trap cost ~30 min in v8 and required a full emoji-restoration pass — every callout icon, every section header emoji, every arrow link had to be re-injected. The validator's `U+FFFD` check catches the worst case (mojibake replacement char) but cannot detect characters that were silently stripped to nothing.

Mark any unfinished card or table cell with the literal sentinel `EXAMPLE CONTENT BELOW` inside an HTML comment — the final-pass validator (Step 7) greps for it.

If the template ever needs structural improvements (new section, new card style, etc.), update `assets/templates/report-template.html` in the skill folder and commit it so future weeks inherit the change.

### Step 2 — Pull WoW reliability data

Use the Kusto MCP tool against:
- **Cluster:** `https://idsharedeus2.kusto.windows.net`
- **Database:** `ad-accounts-android-otel`

**Always prefer the canonical `materialized_view('XxxMetrics' or 'XxxUpdated')` variants** — these are what the production dashboard uses, are pre-aggregated and HLL-bucketed, and avoid the 240 s MCP timeout that plain `android_spans` queries hit. Full schema, gotchas, and query templates: [`assets/docs/kusto-cheatsheet.md`](../docs/kusto-cheatsheet.md).

> **Fallback when the Kusto MCP times out:** use [`assets/scripts/run-kql.ps1`](../scripts/run-kql.ps1). It acquires a token via `az account get-access-token`, POSTs directly to `/v2/rest/query`, and writes the result as a JSON file the JS helpers (`bucket-trends.js`, `summarize-attribution.js`) can consume directly. The skill's MCP-vs-REST switch is roughly: try the MCP once; if it returns `McpError -32001 (timeout)`, switch to the REST helper for the rest of the run. Run multiple queries in parallel via PowerShell `Start-Job`:
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

Use [`assets/queries/60d-trend-codes.kql`](../queries/60d-trend-codes.kql) (template; replace `<TREND_START>` and `<TREND_END>` tokens. **`<TREND_START>` = `curEnd − 60d`** and **`<TREND_END>` = `curEnd` (today), exclusive** — the literal last 60 days. `bootstrap-report.ps1` prints the resolved values):

```kql
materialized_view('ErrorStatsMetrics')
| where EventInfo_Time >= datetime(<TREND_START>) and EventInfo_Time < datetime(<TREND_END>)
| where isnotempty(error_code) and error_code != 'success'
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week = bin_at(EventInfo_Time, 7d, datetime(<TREND_END>)), error_code
| order by error_code asc, week asc
```

**`bin_at`, not `startofweek` — this is load-bearing.** Anchoring the 7-day bins at `<TREND_END>`
(= `curEnd`) makes the newest bucket exactly `[curEnd − 7d, curEnd)`, i.e. **the report's own WoW
window**, so the novelty classifier grades the same period the tables print. Under the old
`startofweek()` bucketing the classifier lagged by up to a week and silently suppressed real risers
(`authorization_pending` +63.2% in the report, −37.1% to the classifier). See the ⚠️ block in Step 4.

**Every bucket here is a complete 7 days**, so there is no partial end bar and nothing to exclude
from the delta math — `--include-partial-end` and `TREND_CLASS_END` are obsolete. Because 60 isn't a
multiple of 7, the *oldest* bucket (`curEnd − 63d`) is the 4-day stub; `--start` drops it, leaving
8 clean rolling weeks.

#### 3b. Per-error-type trend (same rigor)

```kql
materialized_view('ErrorStatsMetrics')
| extend unified_error_type = MergeUiRequiredExceptions(error_type)
| where EventInfo_Time >= datetime(<TREND_START>) and EventInfo_Time < datetime(<TREND_END>)
| where isnotempty(unified_error_type)
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week = bin_at(EventInfo_Time, 7d, datetime(<TREND_END>)), unified_error_type
| order by unified_error_type asc, week asc
```

`MergeUiRequiredExceptions` is mandatory — without it the 6+ string variants of `UiRequiredException` (raw, fully-qualified, com.microsoft.identity.common.exception.*) each show as separate rows and skew the buckets.

#### 3c. Run the bucketer 4 times (cross-product of `{code, type} × {devices, requests}`)

`bucket-trends.js` defaults to grouping by `error_code`. For the type runs you MUST pass `--key=unified_error_type` so it picks up the right column from the type-trend JSON.

```pwsh
# Error codes — by devices, then by requests.
#   TREND_START = curEnd - 60d   TREND_END = curEnd    (both printed by bootstrap-report.ps1)
# Pass BOTH --start and --end. See the note below for why --end is not optional.
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <codes.json> --start=<TREND_START> --end=<TREND_END>
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <codes.json> --start=<TREND_START> --end=<TREND_END> --metric=reqs

# Error types — by devices, then by requests (note --key)
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <types.json> --start=<TREND_START> --end=<TREND_END> --key=unified_error_type
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <types.json> --start=<TREND_START> --end=<TREND_END> --key=unified_error_type --metric=reqs
```

**⚠️ `--end=<TREND_END>` is mandatory even though it filters nothing.** Every bucket label is
`< curEnd` by construction, so `--end` removes no data — its job is to **disable the partial-end
auto-drop heuristic**, which is guarded by `if (!endArg …)`. Under rolling alignment the newest
bucket is genuinely complete, so leaving the heuristic armed means a real 70% collapse could be
discarded as "looks partial". The script warns if you omit `--end`; treat that warning as an error.
`--include-partial-end` is a retained no-op — do not add it to new invocations.

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
- `Code:-11` — sat at ~1,030 devs/week for 7 weeks then jumped to 2,433 (+165% WoW). Sub-floor.
- `SSLHandshakeException` — devices flat at 260 but requests +186% WoW (per-device retry storm). The bucketer's reqs-axis floor (100K) just barely captures it but the device floor doesn't.

To catch these, **always** run [`assets/queries/wow-movers.kql`](../queries/wow-movers.kql) **as a separate pass after the 60d bucketing**:

```kql
// inputs: <CUR_END> = curEnd (exclusive), <CUR_START> = curEnd - 7d,
//         <PREV_START> = curEnd - 14d. Printed by bootstrap-report.ps1.
// floor: cDev>=500 OR cReq>=5000   move: |Δd|>=25% OR |Δr|>=50% OR new-this-window
```

Run it **twice — once for `error_code`, once for `error_type`**. **Merge its output rows into the same regression callout as the standard WoW table**, then let Step 3e's novelty classification decide their grouping and order. The size split is implementation detail; what a reader needs first is *"is this new?"*, not *"is this big?"*. Do **not** sort the merged list by device count — that is exactly how a flat-but-huge code ends up above a real step change.

For each WoW mover (regardless of size), you still owe the full Code Attribution treatment (Step 4). The dim-slicing pass (Step 5) is allowed to be deferred for sub-1K-device spikes if the throw-site + dominant message already pin the originator unambiguously — but say so explicitly in the card ("dims not yet sliced — file the bug first; pull dims if it persists").

### Step 3e — Classify novelty (what is actually NEW vs already-known)

**This step is mandatory and it is what makes Section 2 readable.** `bucket-trends.js` tells you *what moved*; it cannot tell you *whether the movement is news*. Without this pass the attention section degenerates into a volume-ranked list where a flat-but-huge code leads and the real step change sits at position #9.

Run it over each sidecar `bucket-trends.js` wrote (`--json=<path>`):

```powershell
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\classify-novelty.js <codes-devs-buckets.json> --summary --json=<codes-devs-novelty.json>
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\classify-novelty.js <types-devs-buckets.json> --summary --json=<types-devs-novelty.json>
```

Each key is classified against **its own history**, using complete weeks only (first match wins):

| Label | Rule | What it means for the report |
|---|---|---|
| `VOLATILE` | `cv > 0.60` | Series swings wildly. **Suppress the ratio** — a WoW % here is meaningless. |
| `RECOVERY` | prior week `< median × 0.5` **and** now back near median | Bounce-back off a suppressed week, not a regression. **Suppress the ratio.** |
| `NEW` | `ratio > 1.15` **and** `cv < 0.25` | Boring baseline, clean step up. **This is the news.** Visible + charted. |
| `ACCELERATING` | rising over the window (`climb > 1.15`) **and** `ratio > 1.10` **and** `recentRatio > 1.10` **and** not falling WoW | Known issue that is **still deteriorating**. Visible + charted. |
| `ONGOING` | rising over the window, but level or easing now | Elevated and flat. **Collapse into the fold** with its `weeksElevated` count. |
| `IMPROVING` | `ratio < 0.8` | |
| `STABLE` | otherwise | |

**⚠️ The `ACCELERATING` / `ONGOING` split is the whole noise fix.** Both are multi-week elevated
series, and lumping them together is what produced a 13-row attention list. `ACCELERATING` answers
"is this getting *worse*?" — the only reason a known issue deserves the on-call engineer's eye a
second time. Everything else that is merely still-elevated goes in the fold.

**The classifier decides what gets a chart.** `attention = NEW ∪ ACCELERATING` — that set, and only
that set, is rendered visibly with sparklines. The sidecar exposes it directly:

```jsonc
{ "attention": ["ipc_return_null_cursor", "access_denied"],   // render these, with charts
  "attentionLabels": { "ipc_return_null_cursor": "NEW", "access_denied": "ACCELERATING" },
  "quietWeek": false,                                          // true => nothing to headline
  "counts": { "NEW": 3, "ACCELERATING": 1, "ONGOING": 9, "STABLE": 35, "VOLATILE": 3, "IMPROVING": 2 } }
```

On the 2026-07-30 fixture that is **5 attention rows out of 53 series**. If your attention section is
much longer than the `attention` array, you promoted rows the classifier did not.

**`weeksElevated` is derived, never persisted.** It counts consecutive recent weeks above the
*early-window baseline* (`median` of the first third), so it is identical on any machine and needs no
state file. Its known limit: with only 8 weeks of history you cannot distinguish "elevated for 7 weeks"
from "normal at a high level" — the classifier sets `sustainedFullWindow: true` for those, and the
correct phrasing is *"elevated for the entire visible window"*, not a hard week count.

**Two guards that exist because they were violated in real runs:**
- **`ratio > 1.10` on `ACCELERATING`** — `IntuneAppProtectionPolicyRequiredException` (cv 0.08, flat,
  **down 3.7% WoW**, only 4.9% above its own median) was labelled `ACCELERATING` by a slow drift in
  block means and led the whole types list. A code within 10% of its own median is not this week's
  story regardless of slope.
- **`cur >= prev * 0.95`** — a series that is *falling* this week cannot be "getting worse", even if
  the multi-week trend is up.

**Why `cv < 0.25` gates `NEW`:** a series must have been genuinely boring before a jump counts as news. Without that guard a jittery code that happens to be up this week gets promoted over a real step change.

**⚠️ The trap this exists to kill — a big WoW % off an anomalous baseline is not a regression.** Real 2026-07-30 data:

```
429                      300,664 299,965 892,839 974,980  11,512  32,530   2,724 → 16,531   cv=1.07
temporarily_unavailable   30,257  29,962  37,263   6,168  41,971     141      71 → 36,153   cv=0.80
```

`429` was reported as **+397.8% WoW** — but it is **94.5% *below* its own 60-day median**; the ratio is measured off a 2,724 floor after a collapse from ~975K. `temporarily_unavailable` was reported as **+400.7%** — it merely returned to its normal ~36K band after two suppressed weeks. Both were headlined. Neither is a regression. **A WoW percentage is meaningless whenever the *prior* week was itself anomalous.**

Meanwhile the week's actual story classified as `NEW` and was buried at report positions #6/#9/#10:

```
ipc_return_null_cursor                43,093 43,950 43,552 44,571 43,759 42,117 41,473 → 52,129  cv=0.02
ipc_operation_not_supported_on_server 19,503 19,957 20,074 21,457 21,466 20,849 20,575 → 24,050  cv=0.03
ipc_connection_error                   8,317  8,685  8,517  8,634  8,104  8,410  8,593 → 10,133  cv=0.02
IPC FAMILY TOTAL                                                          70,641 → 86,312  (+22.2%)
```

Three codes, each flat for seven straight weeks, all stepping up in the *same* week — one root cause in the IPC layer, reported as **one** finding. (`BrokerCommunicationException`, `NEW` on the type axis at +21.8%, is the same incident seen through the type dimension — say so rather than filing it twice.)

**Families.** The classifier clusters keys sharing a prefix before `_` when ≥2 members share the same label. Report a family as ONE row. Error *types* are CamelCase and produce no families under `_` — that is correct, not a bug.

**⚠️ The classifier and the report now share ONE basis. This used to be a bug.** Both the headline
`Δ WoW` and the classifier's `WoW` are computed on the **same rolling 7-day window**
(`[CUR_START, CUR_END)` vs the 7 days before), because the 60-day trend is bucketed with
`bin_at(t, 7d, curEnd)` and its final bucket **is** that window. **If a classifier `WoW` ever
disagrees in sign or by more than HLL noise from the number in the table, the pipeline is
misconfigured — stop and check that `--start`/`--end` were passed as bootstrap printed them.**

> **Why this warning exists.** Buckets used to be Sun–Sat calendar weeks cut off at
> `startofweek(curEnd)`, and the two bases were documented as "legitimately disagreeing". They did
> not legitimately disagree — the calendar basis lagged the report by up to a full week and was blind
> to anything that turned in the last ~6 days. On the 2026-08-01 run the classifier's current week
> was 07/19–07/26 against a report window of 07/25–08/01, **one day of overlap**:
>
> | code | report ΔWoW | old classifier WoW | old verdict |
> |---|---|---|---|
> | `authorization_pending` | **+63.2%** (171,897 → 280,572) | −37.1% | ONGOING — "do not re-triage" |
> | `expired_token` | **+26.7%** (86,255 → 109,251) | −51.0% | ONGOING — "do not re-triage" |
>
> Both were real risers the on-call engineer had already spotted by hand, and the report silently
> suppressed both. Re-bucketing on `bin_at` promoted them to ACCELERATING **and** demoted
> `access_denied` — an ONGOING-worthy code the old basis had wrongly promoted (actually −53.2%).
> The attention set went 4 → 5 keys, not 4 → 15. **Do not reintroduce `startofweek()` here.**

> **What the classifier still contributes.** Selection and narrative, not numbers:
>
> | Take from the query results | Take from `classify-novelty.js` |
> |---|---|
> | Every KPI tile, table cell, and Δ% chip | Which rows are promoted (the `attention` set) |
> | The sentence "X rose N% this week" | Which label a row carries (NEW / ACCELERATING / …) |
> | | The sentence "…and it has been climbing for six weeks" |
>
> The classifier's job is to answer *"is this new?"*, which a single delta cannot. It is no longer a
> second source of truth for *"how much did it move?"* — there is only one answer to that now.

---


> ⚠️ **HARD RULE — Originator pre-check.** Before claiming `Originator: Broker` on any card, you MUST run [`assets/queries/error-message-and-location.kql`](../queries/error-message-and-location.kql) for that error code (or type) and read **(a) the throw-site stack and (b) the top 3 `error_message` strings**. Most broker error codes flow through `common/ExceptionAdapter.{getExceptionFromTokenErrorResponse, exceptionFromAuthorizationResult, clientExceptionFromException}` — which intentionally bridge eSTS responses into broker exceptions. **If the throw site is in any of those three methods AND the error_message starts with `AADSTS`, the originator is eSTS, not broker.** See the AADSTS reference table in [`assets/docs/kusto-cheatsheet.md`](../docs/kusto-cheatsheet.md). Cards that skip this step must be marked low-confidence, not high.
>
> **Window:** use the FULL 7-day rolling window (`<CUR_START>` → `<CUR_END>`) on `PipelineInfo_IngestionTime`, NOT a narrower 3–5 day slice — low-volume types (e.g. `SSLHandshakeException`, `IntuneAppProtectionPolicyRequiredException`) routinely return zero rows in a sub-window slice. If a code/type still returns nothing, fall back to the prior 14 days (`<PREV_START>` → `<CUR_END>`) before declaring "no data".

For every regression card, the Code Attribution block **must** populate the following fields. Shallow PR-citation only is not acceptable. Use [`assets/docs/code-attribution-template.md`](../docs/code-attribution-template.md) as the per-card checklist.

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
| `broker/` | `https://msft.ghe.com/security/ad-accounts-for-android/pull/<num>` |
| `msal/` | `https://github.com/AzureAD/microsoft-authentication-library-for-android/pull/<num>` |
| `adal/` | `https://github.com/AzureAD/azure-activedirectory-library-for-android/pull/<num>` |

#### Non-broker errors

For errors with no broker code in the stack (Android system errors like `Code:-10`/`Code:-11`, OEM-specific keystore failures, eSTS-returned codes, environmental TLS interception), explicitly cite **"⚪ None — not in scope"** with confidence `none`, and explain *why* in the why-it's-the-suspect line. Do not invent broker PRs to fill the slot. Tag these errors as `environmental` or `non-broker` so they're tracked but don't page.

### Step 5 — Spike attribution dimensions

**Coverage rule: every `error_code` AND every `error_type` that lands in either the WoW regression list OR the 60-day regression list MUST get a spike-attribution card.** No silent skips.

> **⚠️ Coverage and the ≤ 8 visible-row budget are NOT in conflict — they govern different things.**
> This is the most-reported ambiguity in the playbook, so read it carefully:
> - The **≤ 8 budget (§2) limits what is VISIBLE at the top level.** It is about what the on-call
>   engineer is asked to read first.
> - The **coverage rule here limits what may be OMITTED.** It is about what must exist somewhere in
>   the document, so a regression can never silently vanish.
>
> **Resolution: cards beyond the budget go into a collapsed fold, they do not get dropped.** Render
> the `attention` set (plus ≤ 2 wins) as visible cards, and put every remaining mandated card in a
> `<details>` fold titled *"Full attribution coverage (N more codes/types)"*. Coverage is satisfied
> by the card **existing and being reachable**, not by it being expanded on load. A run with 12
> mandated cards and 7 visible rows is correct and expected — that is the design working, not a
> budget violation.
>
> Never resolve this the other way: do **not** expand Section 2 past 8 rows to fit the cards, and do
> **not** skip a mandated card to protect the budget.

**`ErrorStatsMetrics` already carries `account_type` and `is_shared_device`** (use the `MergeAccountType` / `MergeIsSharedDevice` helpers to normalize) — so you do **not** need a fallback to raw `android_spans` for these dims. Earlier versions of this skill claimed otherwise; that was wrong. The only dim that requires `android_spans` is `DeviceInfo_OsVersion` (OEM/version slicing).

Slice on **all 7 dimensions** for each spike. **Preferred for 2-week WoW attribution: one union query that covers all 7 dims for all regressions in a single round-trip** — see [`assets/queries/attr-union-by-dim.kql`](../queries/attr-union-by-dim.kql). Typical payload for 8 codes × 2 weeks × 7 dims is ~800 KB, well under the MCP limit. Pipe the result into `summarize-attribution.js --union <file.json>` (which prints per-dim top-N share + Δ devices + Δ requests for every code). Fall back to the per-dim form ([`attr-codes-by-dim.kql`](../queries/attr-codes-by-dim.kql)) only when (a) you need a wider time window, or (b) the union response exceeds payload size.

For `error_type` cards, swap `error_code in (codes)` for `unified_error_type in (types)` and aggregate by the `MergeUiRequiredExceptions(error_type)` extension — otherwise everything else is identical.

> **Low-volume fallback (extends Step 4's pre-check fallback to the 7-dim union):** when a code/type returns sparse dim rows in the 7-day rolling window — typical for sub-1k-device entries like `TimeoutCancellationException`, `JsonSyntaxException`, `kdfv2_key_derivation_error` — widen the union query to **14 days** (use `<PREV_START>` as the lower bound so the window becomes `[curEnd − 14d, curEnd)`) before declaring "broad — needs targeted slice". The added week of context usually surfaces enough rows to compute concentration percentages. If a code STILL has no concentration after 14 days, mark every dim cell as "not sliced — sub-window volume; file the bug first, slice on persistence" — do NOT fabricate "Broad" verdicts.

| # | Dimension | Source | Cross-check |
|---|-----------|--------|-------------|
| 1 | Broker version | `ErrorStatsMetrics` group by `broker_version` | Cross-reference `BrokerAdoptionStatsUpdated` to see if the version's request share *also* moved that week — if yes, the spike is rollout-driven, not code-driven |
| 2 | Span name | `ErrorStatsMetrics` group by `span_name` | A single span hosting >60% of the error → strong code-path signal |
| 3 | Active broker package | `ErrorStatsMetrics` group by `active_broker_package_name` | E.g. CompanyPortal vs Authenticator vs LTW |
| 4 | Calling package | `ErrorStatsMetrics` group by `calling_package_name` | If 1–2 callers dominate, this is likely a traffic-attribution case (see Step 6) |
| 5 | Account type (AAD vs MSA) | `ErrorStatsMetrics`, `extend t = MergeAccountType(account_type)` group by `t` | If the split deviates significantly from fleet (~85% AAD / 15% MSA), call it out |
| 6 | Shared device mode | `ErrorStatsMetrics`, `extend s = MergeIsSharedDevice(is_shared_device)` group by `s` | Shared-device fleets have very different error profiles |
| 7 | OS version | [`assets/queries/os-version-slice.kql`](../queries/os-version-slice.kql) — raw `android_spans`, group by `DeviceInfo_OsVersion` | **On-demand only** — slice OS-version when EITHER (a) the wrapper class is in `ExceptionAdapter.clientExceptionFromException` (catch-all wrapping a system exception, where the OEM/version often is the cause), OR (b) the error code is one of `Code:-6`, `Code:-10`, `Code:-11`, `unknown_crypto_error`, `io_error`, `null_pointer_error`. Otherwise mark the dim row as "not sliced this week — no OEM concentration suspected" and move on. Slicing OS-version on every card wastes a raw-spans query without changing the verdict. |

#### Type cards have one extra required dimension: sub-code decomposition

Because `error_type` is an umbrella over many `error_code` values, every `error_type` regression card MUST also include an **8th dimension: sub-code breakdown** showing the top 3–5 `error_code`s rolled up under that type, with their device counts and Δ vs prior week. This lets the reader see whether the type-level move is driven by one sub-code or many — and routes the deep Code Attribution work to the right sub-code.

```kql
let curEnd    = datetime(<CUR_END>);
let curStart  = datetime(<CUR_START>);
let prevStart = datetime(<PREV_START>);
let target_types = dynamic(['ClientException', 'ServiceException']);
materialized_view('ErrorStatsMetrics')
| extend unified_error_type = MergeUiRequiredExceptions(error_type)
| where EventInfo_Time >= prevStart and EventInfo_Time < curEnd
| where unified_error_type in (target_types)
| extend week = iff(EventInfo_Time >= curStart, curStart, prevStart)
| summarize devs = dcount_hll(hll_merge(countDevicesHll)),
            errs = sum(countOverall)
     by week, unified_error_type, error_code
| order by unified_error_type asc, week asc, devs desc
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

Ready-to-paste KQL for both forms: union → [`assets/queries/attr-union-by-dim.kql`](../queries/attr-union-by-dim.kql); per-dim → [`assets/docs/kusto-cheatsheet.md` § 8c](../docs/kusto-cheatsheet.md).

**Concentration thresholds** (paint the dim bar red):
- > 80% in a single value → strong attribution (one root cause)
- 60–80% → medium attribution
- < 60% → broad / cross-cutting → say so explicitly, don't fabricate a single cause

### Step 6 — Traffic analysis + traffic attribution

Do this section in three parts. Traffic changes (up *or* down) need the same level of root-cause reasoning as error spikes — a uniform "−9% requests across all top apps with flat devices" is **not** a satisfactory verdict on its own; explain *why*.

**6a. Top-line traffic shape.** Compare WoW *and* 60d for both totals and per-segment:

```kql
materialized_view('BrokerAdoptionStatsUpdated')
| where EventInfo_Time >= datetime(<TREND_START>) and EventInfo_Time < datetime(<TREND_END>)
| summarize totalReq = sum(countRequests),
            totalDev = dcount_hll(hll_merge(countDevicesHll))
     by week = bin_at(EventInfo_Time, 7d, datetime(<TREND_END>))
| order by week asc
```

Same `bin_at` anchoring as 3a/3b, so the final bucket is the report's WoW window and this traffic
series lines up bucket-for-bucket with the error trends you compare it against.

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
let curEnd    = datetime(<CUR_END>);
let curStart  = datetime(<CUR_START>);
let prevStart = datetime(<PREV_START>);
let target_code = "<error_code>";
materialized_view('ErrorStatsMetrics')
| where EventInfo_Time >= prevStart and EventInfo_Time < curEnd and error_code == target_code
| extend week = iff(EventInfo_Time >= curStart, curStart, prevStart)
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week, calling_package_name
| order by week asc, devs desc
```

If the spike is concentrated in a single calling app whose **overall** request volume also rose that week (cross-check `AppStatsUpdated`), and the **per-request failure rate is essentially flat**, classify the spike as a **traffic-attribution case** rather than a code regression:

> Example: "`no_account_found` +60% devices this week is fully explained by Outlook's request volume rising 65% — the per-Outlook-request failure rate is unchanged. No broker code change is implicated."

Add a top-level **🚚 Traffic Attribution** section that lists every error matched to a traffic-driven origin, mirroring the Code Attribution section. **Each card must include**: the dominant calling app(s) with their WoW request-volume delta, the per-app per-request failure rate (now vs prior — show it's flat), and the recommended owner to route to (typically the calling app's team, not broker). If no errors qualify in a given week, render the section with an explicit "None this week" note rather than omitting it.

### Step 7 — Validate & write

Run the bundled validator FIRST — it covers all the silent-failure cases this skill has tripped on in the past:

```pwsh
.\.github\skills\oncall-weekly-telemetry-report\assets\scripts\validate-report.ps1 -App broker
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
9. **Fabricated-sparkline heuristic (v8)** — warns when a `data-trend` array's peak value is < 100 (almost certainly hand-rolled rather than sourced from real data). See [`assets/queries/wow-table-sparkline-series.kql`](../queries/wow-table-sparkline-series.kql) for the canonical KQL that pulls real 8-week series for every code in the WoW tables. Its `<SPARK_START>` / `<SPARK_END>` tokens are the last **8 complete rolling weeks** anchored at `curEnd` (`<SPARK_END>` = `curEnd`, exclusive; `<SPARK_START>` = `curEnd − 56d`) — the same `bin_at` basis as the trend chart, just a shorter span. Every point is a full 7 days, so no row can end on a misleading partial dip.

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

> **Shared hard rules live in the router** — [`SKILL.md` § Shared hard rules](../../SKILL.md).
> They apply here too and are NOT repeated below: never carry a telemetry number forward between
> runs · never hardcode the Generated date · never compose report HTML via a PowerShell `@'...'@`
> heredoc (UTF-8 strip) · never bulk-regex-edit balanced HTML · no `devs`/`reqs` in user-facing
> text · **every red/amber table pill is either promoted into attention or explained in a
> `.reconcile-note`** (validator check 19) · same-end-date collision requires an explicit delta
> statement · no separate Markdown summary · never commit the report.
> **Read them before writing any HTML.**
>
> The reconciliation rule bites on Broker too, for the same structural reason it does on the
> Authenticator: a code whose 60-day **peak** sits under the 10,000-device floor is excluded from
> classification outright, so it can hold a red pill indefinitely while attention says "quiet week".
> Now that both bases are rolling, disagreement on *direction* is a bug — what legitimately remains
> is disagreement on **classifiability**. Say which it is.
>
> The rules below are Broker-specific and do **not** transfer to the Authenticator playbook.


- **Never `sum(countDevices)`.** Always `dcount_hll(hll_merge(countDevicesHll))`. Summing the per-row distinct count double-counts.
- **Always wrap view names in `materialized_view('Xxx')`** and use the canonical `Metrics`/`Updated` variants (see cheatsheet § 2).
- **Never sum percentiles.** Latency is a TDigest sketch — `percentile_tdigest(tdigest_merge(responseTimeTDigest), N, typeof(long))` only.
- **Always apply `MergeAccountType` / `MergeIsSharedDevice` / `MergeUiRequiredExceptions`** so this report agrees with the dashboard.
- **Confirm the week bucket label matches the user's intent** before writing the rest of the queries (Sunday-aligned).
- **Bucket the 60-day trend with `bin_at(EventInfo_Time, 7d, datetime(<TREND_END>))`, never `startofweek()`.** Anchoring at `curEnd` makes the newest bucket exactly the report's WoW window, so the noise gate grades the period the tables print. `startofweek()` bucketing lagged by up to 6 days and structurally suppressed anything that turned late in the window — that is how `authorization_pending` shipped as "ONGOING, do not re-triage" while the report showed it up 63.2%. Every bucket is now a complete 7 days (the 4-day stub is the *oldest* bucket and `--start` drops it), so there is nothing to exclude from the delta math: `--include-partial-end` and `TREND_CLASS_END` are obsolete. **Always pass both `--start` and `--end` to `bucket-trends.js`** — `--end` filters no rows but disables the partial-end auto-drop heuristic, which would otherwise be free to discard a real collapse. `wow-table-sparkline-series.kql` uses the same `bin_at` basis over 8 weeks.
- **Originator pre-check is mandatory.** A card cannot claim `Originator: Broker` without first running [`assets/queries/error-message-and-location.kql`](../queries/error-message-and-location.kql) and reading the throw site + top 3 `error_message` strings. If the throw site is in `common/ExceptionAdapter.{getExceptionFromTokenErrorResponse, exceptionFromAuthorizationResult}` AND the message starts with `AADSTS`, the originator is **eSTS, not broker** — see the AADSTS reference in [`assets/docs/kusto-cheatsheet.md`](../docs/kusto-cheatsheet.md).
- **WoW-movers pass is mandatory.** The 60d bucketer's `--peak-floor` silently drops sub-10K-device codes, so [`assets/queries/wow-movers.kql`](../queries/wow-movers.kql) MUST be run as a separate pass for both `error_code` and `error_type` (per Step 3d). Its output is **merged into the single regression callout** and then grouped by Step 3e's novelty labels. Do not render a separate "emerging" callout. Skipping the pass is how the Apr 26 `Failed to parse JWT` spike (7 → 3,461 devs over 7 weeks) hid for two reports running.
- **Novelty classification is mandatory, and Section 2 is ordered by it — never by volume.** Run [`classify-novelty.js`](../scripts/classify-novelty.js) (Step 3e) and lead with `NEW`. Ranking the attention list by device count is a known, reported defect: it put `IntuneAppProtectionPolicyRequiredException` (ΔWoW **+0.1%**, classifier says `ONGOING` and *falling*) at #1 while the genuinely new `ipc_*` family sat at #6/#9/#10. If the `NEW` bucket is empty, write "nothing new this week" — do not backfill it with `ONGOING` items.
- **Section 2's visible rows are the classifier's `attention` set plus at most 2 wins — nothing else.**
  `NEW` + `ACCELERATING` visible with sparklines; `ONGOING` inside a collapsed `<details class="fold">`.
  Budget: **≤ 8 visible rows total, wins included** (validator check 17 warns above it and counts wins).
  The failure mode this replaces is measured, not hypothetical: 13 visible rows, 0 charts, and the
  60-day section below carrying 38.
- **Every visible attention row carries an 8-week `.item-spark`.** Validator check 16 hard-fails
  otherwise. The series comes from the trend sidecar you already loaded — no extra query. Charts belong
  beside the claim they support; a separate browsable chart section is the noise, not the signal.
- **The 60-day section is a detector, not a catalog.** Chart only the slow burns it *promotes* (rising
  on 60d and absent from Section 2) — typically 0–3, often zero. The full classification goes in a fold
  with no charts. Validator check 18 hard-fails above 6 visible charts there.
- **A quiet week is a valid outcome — publish it as one.** If `quietWeek: true`, say so plainly and
  keep the report short. Padding the attention list with the biggest flat code to look thorough is the
  exact behaviour that trains readers to skim.
- **A `VOLATILE`/`RECOVERY` row must not carry a `Δ WoW` chip at all — swap it for `vs 60d median`.** These carry `suppressRatio: true` because their WoW % is an artifact of a depressed prior week, not a regression. Tagging the row `VOLATILE` and caveating in the body is **not sufficient**: a naive run shipped `429` tagged `VOLATILE` with the body reading *"large percentage move but classified volatile"* — and still rendered `+401.8%` in `metric up` styling, which is the first thing a reader sees. `429` at "+401.8%" while sitting 94.5% *below* its own 60-day median is the canonical failure. `validate-report.ps1` check 15 hard-fails this.
- **A family is one finding, not N — and that includes the type axis.** When related codes move together (classifier `families`), emit **one** row whose `item-name` is the family and whose body names the members. Reconcile across axes too: if a `NEW` type is the umbrella for a `NEW` code family, that is still **one** row. A naive run emitted four rows — `BrokerCommunicationException`, `ipc_return_null_cursor`, `ipc_operation_not_supported_on_server_side`, `ipc_connection_error` — for a single IPC incident, which re-creates the wall-of-codes problem this section exists to fix. Correct shape:
  ```html
  <span class="item-name">ipc_* / BrokerCommunicationException<span class="kind">family</span></span>
  ```
  with the body reading *"Three IPC codes stepped up together off a flat 7-week baseline (…null_cursor 41.5K→52.1K, …not_supported 20.6K→24.1K, …connection_error 8.6K→10.1K; family +22.2%). `BrokerCommunicationException` is the same incident seen on the type axis."* One row, one owner, one attribution card.
- **No boilerplate in Section 2.** Every row's one-line body must be specific to that row — what changed, from what to what, why it is or isn't alarming. Reusing one generic sentence across rows (*"Current-window movement needs owner triage; deep dive below has originator and dimensions."*) makes the section unreadable and `validate-report.ps1` fails the report for it.
- **Section 2 callouts are at-a-glance, Section 4 is the deep dive.** WoW / Slow-burn / Wins items in Section 2 use the `.item` flat-row pattern (no nested cards, no per-item left bars — the parent `.callout` border is the only severity affordance). Each row is a single line of metric chips + a one-line body + an `Attribution card →` link to the corresponding `.attr-card` in Section 4. Do NOT duplicate the dim slicing, PR analysis, or detailed verdict between the two sections — Section 4 is where that lives. See [`assets/templates/template-readme.md`](../templates/template-readme.md) for the CSS class reference and the example `.item` markup.
- **Denominator caveat must cite evidence, not hand-wave.** If you flag a large all-spans device-count shift, run [`assets/queries/broker-version-share-wow.kql`](../queries/broker-version-share-wow.kql) (single WoW snapshot) or [`assets/queries/broker-version-share.kql`](../queries/broker-version-share.kql) (time-series) and name the version cohort the shift moved with. Do not write "recurring telemetry-shape artifact" without backing data; if you don't have it, drop the callout.
- **"Recovery" still merits a PR citation.** When an error pins to a single old broker version and recovers as that version retires, look for the **fix PR in the version that replaced it** before calling it a "natural rolloff." Often the fix is real and just under-credited.
- **Never report WoW-only verdicts** for errors that are flat-or-down WoW but rising on 60d — always cross-check both windows.
- **Never page** based on a regression that turns out to be a downstream of a denominator shift; always include the auth-only-denominator number alongside the all-spans number.
- **Always cite PRs** with full GitHub URLs (the repo URL patterns above), not bare commit SHAs.

---

## Output checklist

- [ ] New `oncall-wow-report-YYYY-MM-DD.html` (where `YYYY-MM-DD` is the resolved `curEnd` — the end-date of the rolling 7-day window) exists at `$env:USERPROFILE\android-oce-reports\` (NOT at repo root). If a file for this end-date already existed, the chat session explicitly stated what changed before regenerating.
- [ ] All sections present and populated (incl. 🚚 Traffic Attribution — even if “None this week”)
- [ ] **60-day trend bucketing run on the full cross-product** — `{error_code, error_type} × {devices, requests}` = 4 runs — union of regressions reported. Per-request retry storms (e.g. small device pool, exploding request count) are flagged on both axes. Source KQL spans the literal last 60 days ending today and buckets with `bin_at(…, 7d, <TREND_END>)`, so the newest bucket **is** the report's WoW window; every `bucket-trends.js` invocation passed **both** `--start` and `--end`.
- [ ] **WoW-movers pass run** ([`wow-movers.kql`](../queries/wow-movers.kql)) for BOTH `error_code` and `error_type`. Its output rows are **merged into the single regression callout in Section 2**. Every row carries throw-site, dominant message, originator, and a next step. If the callout is empty (rare), render "None this week" rather than omit.
- [ ] **Novelty classification run** ([`classify-novelty.js`](../scripts/classify-novelty.js)) on every `bucket-trends.js` sidecar. Section 2 is grouped 🆕 New → 🟠 Getting worse → 🔵 Ongoing (collapsed fold) → 🔁 Volatile → ↩️ Recovery, **not** sorted by device count. No `VOLATILE`/`RECOVERY` row headlines a percentage. Families are reported as one row. Every row body is specific — no sentence repeats across rows.
- [ ] **Attention section is short and charted.** Visible rows == the classifier's `attention` set (`NEW` + `ACCELERATING`), ≤ 8 of them, each with an `.item-spark` 8-week sparkline. `ONGOING` rows live inside a collapsed fold. If `quietWeek: true`, the quiet-week banner is shown and nothing was promoted to fill the gap.
- [ ] **60-day section charts only promoted slow burns** (rising on 60d, not already in Section 2 — often zero). Full classification is inside a `<details>` fold with no chart column. ≤ 6 visible charts.
- [ ] **Both error-codes AND error-types WoW tables have `Δ requests %` and `Δ devices %` columns**, the 60d sparkline, and a status pill. Any row crossing threshold on either metric is in the regression list.
- [ ] Every WoW regression AND every 60d regression — **for both `error_code` and `error_type`** — has its own spike-attribution card with all 7 dimensions sliced. Cards are built from [`assets/templates/spike-card.html`](../templates/spike-card.html).
- [ ] **Every `error_type` regression card includes the 8th-dimension sub-code decomposition** showing the top 3–5 contributing `error_code`s with their Δ vs prior week, and links to those sub-codes' own attribution cards.
- [ ] **Originator pre-check has been run for every broker-tagged card** ([`error-message-and-location.kql`](../queries/error-message-and-location.kql)). Throw site and top 3 `error_message` strings are populated from real data, not from the code map. AADSTS-prefixed messages are tagged `eSTS`, not `Broker`.
- [ ] **Every regression card's Code Attribution block populates Originator + Top throw site + Wrapper + Caller hot-spots + Underlying cause + Top error_messages + Likely PRs (with confidence/why-it's-the-suspect) + Next step (with named owner)**. For type cards, the wrapper field focuses on the type's catch-and-rethrow site (e.g. `BaseException`, `ServiceException` constructor). Shallow PR-only attribution is not acceptable.
- [ ] Non-broker errors are explicitly tagged `environmental` / `non-broker` with confidence `none` — not invented broker PRs.
- [ ] Traffic analysis covers totals, per-app, per-span, requests-per-device ratio (per error AND overall), and a sampling-change check.
- [ ] **Every material traffic shift (>10% on any segment, up or down) has a reasoning paragraph** that names the dominant span/app/active-broker/broker-version, and either cites a causal PR (with confidence) — span removed/added, `goAsync()` refactor, sampling change, caller-side SDK release, ECS flight ramp — or explicitly says "no PR identified, suspect X" rather than leaving it unexplained.
- [ ] Denominator caveat (if used) is backed by [`broker-version-share-wow.kql`](../queries/broker-version-share-wow.kql) or [`broker-version-share.kql`](../queries/broker-version-share.kql) evidence naming the responsible version cohort. No hand-waving.
- [ ] Auth-only denominator used for all reliability %s, denominator caveat called out at top.
- [ ] No `\bdevs\b` or `\breqs\b` in user-facing text. (`Select-String -Pattern '\bdevs\b|\breqs\b' -CaseSensitive:$false` returns 0.)
- [ ] **Sparklines rendered.** Every `.kpi` tile in the Top-line health section has a `data-spark` array with 8 weekly values. Every **visible** Section 2 attention row has an `.item-spark` (validator check 16). Every row in the WoW tables (codes + types) has a `data-trend` mini-spark. Under rolling alignment every bucket is a complete 7 days, so **all** sparkline and 60-day `data-trend` arrays carry 8 points — there is no in-progress bar and no 9-point variant. Past failure mode: the v7 body rebuild dropped all sparklines silently — see `template-readme.md` § "Sparklines are MANDATORY".
- [ ] **Code-attribution depth.** Every `.attr-card`'s Code attribution block uses the full 8-field `<div class="origin-row">` structure (Originator / Top throw site / Wrapper / Caller hot-spots / Underlying cause / Top error_messages / Likely PRs / Next step) per [`assets/docs/code-attribution-template.md`](../docs/code-attribution-template.md). A `pr-list`-only stub is **not acceptable** — the validator hard-fails this. Past failure mode (v7 third pass): all 10 cards shipped with PR-only stubs and lost the throw-site / wrapper / underlying-cause analysis.
- [ ] No stale text from previous weeks. (`Select-String -Pattern 'EXAMPLE CONTENT BELOW'` returns 0 — that's the unfinished-section sentinel. The template no longer ships `{{TOKEN}}` placeholders since v2; if the file still contains any `{{`, that's also a leftover.)
- [ ] `get_errors` clean on the HTML file.

