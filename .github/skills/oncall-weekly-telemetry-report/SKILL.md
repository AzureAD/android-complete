---
name: oncall-weekly-telemetry-report
description: Generate the weekly Android Broker on-call (OCE) WoW + 60-day trend telemetry report as a polished self-contained HTML file. Use this skill for the weekly OCE rotation when asked to "produce the OCE report", "weekly on-call report", "WoW telemetry report", "weekly broker health report", or "generate this week's on-call summary". Pulls from `android_spans` materialized views, attributes regressions/improvements to PRs in `broker/` and `common/`, and writes to `$env:USERPROFILE\android-oce-reports\oncall-wow-report-YYYY-MM-DD.html` (outside the workspace so reports are never committed).
---

# OCE Weekly Report

Produce the weekly Android Broker on-call (OCE) telemetry report as a self-contained HTML file at `$env:USERPROFILE\android-oce-reports\oncall-wow-report-YYYY-MM-DD.html` (where `YYYY-MM-DD` is the **end-date of the rolling 7-day window** — see "Inputs to confirm" §1). Writes to the user's home folder, **outside the workspace**, so reports never accidentally get committed.

The output mirrors the structure of the canonical template at [`assets/templates/report-template.html`](assets/templates/report-template.html). The Step 1 bootstrap script copies the template into `~/android-oce-reports/oncall-wow-report-<end-date>.html`, **stamps the resolved rolling-7-day window into the title / meta line / Generated banner**, and you edit it in place from there. Do **not** redesign the layout each run.

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
| [`bucket-trends.js`](assets/scripts/bucket-trends.js) | Bucket all error codes into 60-day regression / spike / improvement / flat. Run with `--metric=devs` AND `--metric=reqs`. Pass `--end=YYYY-MM-DD` (the Sunday that OPENS the current in-progress week, exclusive — i.e. `startofweek(today)`, printed by bootstrap as "Trend delta cutoff") to exclude the partial week from the delta math, plus **`--include-partial-end`** to still chart it as the final bar. **`--summary` suppresses the verbose header; `--json=<path>` emits a structured sidecar for programmatic consumption.** |
| [`classify-novelty.js`](assets/scripts/classify-novelty.js) | Reads a `bucket-trends.js --json=` sidecar and labels each key **NEW / ACCELERATING / ONGOING / VOLATILE / RECOVERY / IMPROVING / STABLE** against its own 7-week baseline, plus family clustering. **Mandatory** — it is what stops the attention section from being a volume-ranked list where a flat-but-huge code outranks a real step change, and it is also the **noise gate**: its `attention` set (`NEW` + `ACCELERATING`), plus at most 2 wins, is all that renders visibly with charts — everything else collapses into a fold. |
| [`agg.js`](assets/scripts/agg.js) | Per-error per-dim top-N rollup with WoW deltas. Workhorse for filling spike-attribution dim blocks. |
| [`summarize-attribution.js`](assets/scripts/summarize-attribution.js) | Roll up 7-dim attribution slices for spike-attribution cards. Supports BOTH `--union <file.json>` (preferred for 2-week WoW; pairs with `attr-union-by-dim.kql`) AND legacy `--label=<dim> file.json` per-dim mode. **Auto-detects the array-form schema produced by `assets/scripts/run-kql.ps1` — no schema-transformer step needed.** |
| [`find-suspect-prs.ps1`](assets/scripts/find-suspect-prs.ps1) | Parallel `git log -S` + `--grep` across broker/ + common/ for a class/method symbol, with PR numbers + URLs. Run *only after* the Originator pre-check has identified a specific throw-site class — the unscoped 4-week PR window is small enough (<30 PRs) to scan with plain `git log` first. |
| [`validate-report.ps1`](assets/scripts/validate-report.ps1) | Pre-publish validator. Catches stale tokens, devs/reqs leaks, mojibake (U+FFFD), unbalanced `<div>` depth in Section 2 (the nested-callout bug), KPI/trend sparkline coverage, code-attribution depth, layout-guard CSS presence, and suspicious low-peak fabricated `data-trend` arrays. Run as part of Step 7. |
| [`scripts/run-kql.ps1`](assets/scripts/run-kql.ps1) | **Direct-REST Kusto helper — drop-in fallback for the Azure Kusto MCP server when the MCP times out** (frequent on per-error-code queries). Acquires a token via `az`, POSTs to `/v2/rest/query`, writes a JSON file the JS helpers can consume directly. |
| [`scripts/bootstrap-report.ps1`](assets/scripts/bootstrap-report.ps1) | Bootstrap a new report from the canonical template. Auto-computes the rolling 7-day window (curEnd = today UTC; override with `-EndDate YYYY-MM-DD`), creates `_data/<end-date>/`, prunes `_data` folders older than 60 days, **stamps the resolved window into the `<title>` / `<div class="meta">` block / Generated banner**, and detects "unfilled template stub" vs "real prior report" collisions using a template-only sentinel token (see § collision detection). |
| [`scripts/visual-smoke.ps1`](assets/scripts/visual-smoke.ps1) | Optional Playwright-based layout smoke test. Renders the report at 1400 px viewport, captures a full-page screenshot under `~/android-oce-reports/_visual/`, and runs DOM-based overflow + adjacent-card-gap detection. Catches the rendered-layout bugs (text bleed, cards touching) that pure HTML/CSS validation can't see. |

---

## Inputs to confirm with the user

> **⚠️ Do NOT ask the user for a reporting date by default.** The skill uses a **rolling 7-day window** ending at start-of-day UTC on the invocation day so the most recent complete days are always captured. `assets/scripts/bootstrap-report.ps1` computes and stamps the window automatically. The previous "confirm the Sunday bucket with the user" flow silently produced stale windows (e.g. a Thursday run emitted a 4-day partial window and dropped the last complete week) — that failure mode is what this section explicitly guards against.

1. **Reporting window** — do NOT ask. Run `bootstrap-report.ps1` with no arguments; it resolves the window silently and prints:
   ```
   Resolved reporting window (UTC):   # example values for a run on 2026-07-15
     Last 7 days:   2026-07-08 -> 2026-07-15  (exclusive upper bound)
     Baseline:      2026-07-01 -> 2026-07-08
     60-day trend:  2026-05-16 -> 2026-07-15  (literal 60d ending today; chart includes current partial week)
     Trend delta cutoff: weeks < 2026-07-12  (startofweek(curEnd); pass as bucket-trends.js --end)
   ```
   These dates are stamped into the report's `<title>`, `<div class="meta">`, and Generated banner during bootstrap — you do not hand-edit them (see `assets/templates/template-readme.md` § Date fields).

   **Override only when the user explicitly requests a non-default window.** Signals: "the week of X", "as of last Friday", "the report from three weeks ago", "in-progress data", "just today's numbers". Then use:
   ```pwsh
   .\bootstrap-report.ps1 -EndDate 2026-07-02   # e.g. reproduce the report as of Jul 2
   ```
   `-EndDate` is the exclusive upper bound of the current window (`curEnd`); the script derives `curStart = curEnd - 7d` and `prevStart = curEnd - 14d` deterministically. `-EndDate` must be today or earlier — future dates are refused.

   **Supported override = `-EndDate` only (shifts the window *end*).** The 7-day primary span, the 7-day baseline, and the 60-day trend form a fixed frame that moves *rigidly* with `-EndDate`. There is **no** custom start-date and **no** arbitrary-span flag — a request like "last 30 days" or "from Jun 1 to Jun 20" is **not** expressible via a single parameter. To produce a longer or custom span you must edit the KQL window placeholders (`<CUR_START>` / `<CUR_END>` / `<PREV_START>`; the baseline end is always `<CUR_START>`, so there is no separate `<PREV_END>` token) by hand. If the user asks for a non-7-day span, say so up front rather than silently emitting a 7-day report.

2. **Comparison baseline** — auto-computed as the immediately-prior 7 days (`[curEnd - 14d, curEnd - 7d)`). No user input.

3. **60-day trend window** — auto-computed as the **literal last 60 days ending today** (`[curEnd - 60d, curEnd)`), so both bounds move with `-EndDate`. The section is still Sun-Sat weekly-bucketed (Kusto `startofweek()` is Sunday-aligned) because the trend needs stable weekly denominators, but the final bar is the **current in-progress (partial) week** — the chart ends today. Regression/improvement **delta classification is still computed on complete weeks only** (`bucket-trends.js --end=startofweek(curEnd) --include-partial-end`); a partial week as "last" would read as a fake −99% improvement, so it is charted but excluded from the delta math. `bootstrap-report.ps1` prints both the 60d data window and the "Trend delta cutoff" (= `startofweek(curEnd)`).

4. **Output filename** — `$env:USERPROFILE\android-oce-reports\oncall-wow-report-YYYY-MM-DD.html` where `YYYY-MM-DD` is the resolved `curEnd`. Example: run on 2026-07-09 (default) → `oncall-wow-report-2026-07-09.html`. User-scoped, outside the workspace. The filename date always matches the meta-line "Last 7 days" end-date; `validate-report.ps1` check #11 asserts this.

**Kusto note (60-day trend section only):** `startofweek()` is Sunday-aligned, so `startofweek('2026-05-09') == 2026-05-03T00:00:00Z`. When authoring weekly-bucketed queries (60-day trend, `wow-table-sparkline-series.kql`), verify by printing the distinct `startofweek(EventInfo_Time)` values from your first query. Off-by-one-week is the #1 silent error in weekly-bucket queries.

---

## Required sections (in order)

1. **Top-line health KPIs** — total requests, total devices, silent-auth reliability %, interactive reliability %, p95 latency on the hot spans. WoW delta on each. Inline SVG sparklines.
2. **Things that need attention this week** — callouts:
   - **Denominator caveat** — explain any large total-spans device-count shift caused by span-emission changes (e.g. `goAsync()` refactors). Always state which denominator the report uses (auth-only: `SilentAuthStats` ∪ `InteractiveAuthStats`).
   - **🔴 Regressions — grouped by NOVELTY, never by volume.** Built from [`classify-novelty.js`](assets/scripts/classify-novelty.js) (Step 3e), unioned with [`assets/queries/wow-movers.kql`](assets/queries/wow-movers.kql) so small-but-recent spikes land in the same grouping.

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

        > **When the classifier and the headline WoW disagree, keep the row here and show both numbers.**
        > The classifier's "not falling" gate runs on **complete Sun–Sat calendar weeks**; the report's
        > headline `Δ WoW` runs on the **rolling 7-day** window. These are different bases and they
        > legitimately disagree — a code can be `ACCELERATING` on calendar weeks while showing a small
        > rolling-window decline. That is *not* a reason to demote it, rename the group, or hedge the
        > heading. Keep the group heading exactly **"Getting worse"**, and resolve it *in the row body*:
        > *"Up 18% over the last three complete weeks; the rolling 7-day window shows −4% as the ramp
        > flattens. Still ~30% above its own 60-day median — watch, don't close."* The sparkline settles
        > it visually, which is why the row has one. Do **not** invent a "needs verification" group.
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

     **⚠️ Every visible row carries its own 9-week sparkline.** The shape is what separates a step
     change from ordinary variance, and it must sit *next to the claim* — not in a separate browsable
     section further down. Use the `.item-spark` pattern from the template:
     ```html
     <span class="item-name">ipc_return_null_cursor</span>
     <span class="item-spark trend" data-trend="[41200,40800,41500,41100,40900,41500,52100]"
           data-w="120" data-h="22" data-color="#cf222e"></span>
     <span class="spark-cap">9 wk</span>
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
3. **📈 60-day cross-check** — a **slow-burn detector, not a browsing list**. Built from the `ErrorStatsMetrics` materialized view over the **literal last 60 days ending today** (final bar = current in-progress week). **Run the bucketing pipeline FOUR times — the cross-product of `{error_code, error_type} × {devices, requests}`** — and union the regression sets. An entry (code OR type) is flagged if it regresses on either metric. Deltas are computed on complete weeks only; the partial current week is charted but excluded from classification.

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

This skill ships with a canonical template at [`assets/templates/report-template.html`](assets/templates/report-template.html) (a real prior report kept as the reference layout). **Use [`assets/scripts/bootstrap-report.ps1`](assets/scripts/bootstrap-report.ps1)** to handle all the boilerplate (rolling-window computation, `_data/<end-date>/` directory, header stamping, retention-pruning, collision detection):

```pwsh
.\.github\skills\oncall-weekly-telemetry-report\assets\scripts\bootstrap-report.ps1
# Optional: explicit end-date (curEnd, exclusive upper bound) + force overwrite
# .\bootstrap-report.ps1 -EndDate 2026-07-02 -Force
```

What it does:
* Resolves the rolling 7-day window from the system clock in UTC (`curEnd = today`, `curStart = curEnd - 7d`, `prevStart = curEnd - 14d`) — or from `-EndDate` if passed.
* Creates `~/android-oce-reports/oncall-wow-report-<curEnd>.html` from the canonical template.
* Creates `~/android-oce-reports/_data/<curEnd>/` for raw KQL JSON payloads.
* **Stamps the resolved window into the report's `<title>`, `<div class="meta">` block, and Generated banner** — you never hand-edit header dates. The resolved window is echoed in the report header for transparency.
* Prunes `_data/<old-end-date>/` folders older than 60 days so the cache doesn't accumulate.
* **Collision detection (fail-safe):** an existing same-day report is silently re-bootstrapped only when it is *positively* identified as an unpopulated stub — it still carries the `OCE-UNPOPULATED-STUB` sentinel that bootstrap injects **and** its first KPI still equals the template's value. Anything else (sentinel removed, or KPIs edited) is treated as real work: **HARD HALT, exit 2**, requiring `-Force` to overwrite. `validate-report.ps1` refuses to pass a report that still carries the sentinel, so a published report can never be misclassified as a stub.

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

Use [`assets/queries/60d-trend-codes.kql`](assets/queries/60d-trend-codes.kql) (template; replace `<TREND_START>` and `<TREND_END>` tokens. **`<TREND_START>` = `curEnd − 60d`** and **`<TREND_END>` = `curEnd` (today), exclusive** — the literal last 60 days. `bootstrap-report.ps1` prints the resolved values):

```kql
materialized_view('ErrorStatsMetrics')
| where EventInfo_Time >= datetime(<TREND_START>) and EventInfo_Time < datetime(<TREND_END>)
| where isnotempty(error_code) and error_code != 'success'
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time), error_code
| order by error_code asc, week asc
```

**Do NOT filter the partial in-progress week here.** The chart wants it as the final bar (the window ends today). The partial week is excluded from the regression/improvement **delta math** by `bucket-trends.js` via `--end=<TREND_CLASS_END> --include-partial-end` (see 3c), not at the source — a partial week driving the delta would read as a fake −99% improvement, which is exactly why classification and display are split in the JS.

#### 3b. Per-error-type trend (same rigor)

```kql
materialized_view('ErrorStatsMetrics')
| extend unified_error_type = MergeUiRequiredExceptions(error_type)
| where EventInfo_Time >= datetime(<TREND_START>) and EventInfo_Time < datetime(<TREND_END>)
| where isnotempty(unified_error_type)
| summarize errs = sum(countOverall),
            devs = dcount_hll(hll_merge(countDevicesHll))
     by week = startofweek(EventInfo_Time), unified_error_type
| order by unified_error_type asc, week asc
```

`MergeUiRequiredExceptions` is mandatory — without it the 6+ string variants of `UiRequiredException` (raw, fully-qualified, com.microsoft.identity.common.exception.*) each show as separate rows and skew the buckets.

#### 3c. Run the bucketer 4 times (cross-product of `{code, type} × {devices, requests}`)

`bucket-trends.js` defaults to grouping by `error_code`. For the type runs you MUST pass `--key=unified_error_type` so it picks up the right column from the type-trend JSON.

```pwsh
# Error codes — by devices, then by requests.
#   TREND_START     = curEnd - 60d           (literal 60d start)
#   TREND_CLASS_END = startofweek(today)     ("Trend delta cutoff" printed by bootstrap)
# --include-partial-end charts the current partial week while excluding it from deltas.
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <codes.json> --start=<TREND_START> --end=<TREND_CLASS_END> --include-partial-end
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <codes.json> --start=<TREND_START> --end=<TREND_CLASS_END> --include-partial-end --metric=reqs

# Error types — by devices, then by requests (note --key)
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <types.json> --start=<TREND_START> --end=<TREND_CLASS_END> --include-partial-end --key=unified_error_type
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js <types.json> --start=<TREND_START> --end=<TREND_CLASS_END> --include-partial-end --key=unified_error_type --metric=reqs
```

`--end` is `<TREND_CLASS_END>` = `startofweek(today)` (exclusive) — the Sunday that opens the current in-progress week. Weeks at or after it (the partial current week) are excluded from delta classification; `--include-partial-end` keeps that week in the emitted `series` so the chart ends today. The script also auto-detects partial end-buckets and warns if `--end` is omitted, but passing it explicitly is safer.

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

To catch these, **always** run [`assets/queries/wow-movers.kql`](assets/queries/wow-movers.kql) **as a separate pass after the 60d bucketing**:

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
state file. Its known limit: with 7–9 weeks of history you cannot distinguish "elevated for 7 weeks"
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

**⚠️ Two different WoW bases exist — do not conflate them.** The report headline ΔWoW is a **rolling 7-day** window (`[CUR_START, CUR_END)` vs the 7 days before). The classifier's `WoW` is **calendar Sun–Sat weeks**. They legitimately disagree — `authorization_pending` read **+3.5%** rolling and **−37.1%** weekly on the same data. Use novelty as *history and context* ("flat for seven weeks, first step this week"), **never** as a competing delta number, or the report will appear to contradict its own tables.

> **The division of labour, stated plainly so you do not have to derive it:**
>
> | Use the **rolling 7-day** numbers for… | Use the **calendar-week** classifier for… |
> |---|---|
> | Every KPI tile, table cell, and Δ% chip | Which rows are promoted (`attention` set) |
> | Any number a reader can see | Which label a row carries (NEW / ACCELERATING / …) |
> | The sentence "X rose N% this week" | The sentence "…and it has been climbing for six weeks" |
>
> **Rule: every *number* in the report comes from the rolling window; the classifier contributes
> *selection and narrative*, never a figure.** The one place the two meet is a row that is
> `ACCELERATING` on calendar weeks while the rolling delta is flat or negative — keep it in
> "Getting worse", keep the heading verbatim, and resolve it in the row body by stating both
> numbers and letting the sparkline settle it. Do not invent a hedged sub-group for these.

---

### Step 4 — Code attribution (deep PR correlation)

> ⚠️ **HARD RULE — Originator pre-check.** Before claiming `Originator: Broker` on any card, you MUST run [`assets/queries/error-message-and-location.kql`](assets/queries/error-message-and-location.kql) for that error code (or type) and read **(a) the throw-site stack and (b) the top 3 `error_message` strings**. Most broker error codes flow through `common/ExceptionAdapter.{getExceptionFromTokenErrorResponse, exceptionFromAuthorizationResult, clientExceptionFromException}` — which intentionally bridge eSTS responses into broker exceptions. **If the throw site is in any of those three methods AND the error_message starts with `AADSTS`, the originator is eSTS, not broker.** See the AADSTS reference table in [`assets/docs/kusto-cheatsheet.md`](assets/docs/kusto-cheatsheet.md). Cards that skip this step must be marked low-confidence, not high.
>
> **Window:** use the FULL 7-day rolling window (`<CUR_START>` → `<CUR_END>`) on `PipelineInfo_IngestionTime`, NOT a narrower 3–5 day slice — low-volume types (e.g. `SSLHandshakeException`, `IntuneAppProtectionPolicyRequiredException`) routinely return zero rows in a sub-window slice. If a code/type still returns nothing, fall back to the prior 14 days (`<PREV_START>` → `<CUR_END>`) before declaring "no data".

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

Slice on **all 7 dimensions** for each spike. **Preferred for 2-week WoW attribution: one union query that covers all 7 dims for all regressions in a single round-trip** — see [`assets/queries/attr-union-by-dim.kql`](assets/queries/attr-union-by-dim.kql). Typical payload for 8 codes × 2 weeks × 7 dims is ~800 KB, well under the MCP limit. Pipe the result into `summarize-attribution.js --union <file.json>` (which prints per-dim top-N share + Δ devices + Δ requests for every code). Fall back to the per-dim form ([`attr-codes-by-dim.kql`](assets/queries/attr-codes-by-dim.kql)) only when (a) you need a wider time window, or (b) the union response exceeds payload size.

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
| 7 | OS version | [`assets/queries/os-version-slice.kql`](assets/queries/os-version-slice.kql) — raw `android_spans`, group by `DeviceInfo_OsVersion` | **On-demand only** — slice OS-version when EITHER (a) the wrapper class is in `ExceptionAdapter.clientExceptionFromException` (catch-all wrapping a system exception, where the OEM/version often is the cause), OR (b) the error code is one of `Code:-6`, `Code:-10`, `Code:-11`, `unknown_crypto_error`, `io_error`, `null_pointer_error`. Otherwise mark the dim row as "not sliced this week — no OEM concentration suspected" and move on. Slicing OS-version on every card wastes a raw-spans query without changing the verdict. |

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
9. **Fabricated-sparkline heuristic (v8)** — warns when a `data-trend` array's peak value is < 100 (almost certainly hand-rolled rather than sourced from real data). See [`assets/queries/wow-table-sparkline-series.kql`](assets/queries/wow-table-sparkline-series.kql) for the canonical KQL that pulls real 8-week series for every code in the WoW tables. Its `<SPARK_START>` / `<SPARK_END>` tokens are the last **8 complete** Sun-Sat weeks (`<SPARK_END>` = `startofweek(today)`, exclusive) — deliberately distinct from the trend-chart's `<TREND_START>` / `<TREND_END>` (literal last 60 days ending today). Per-row sparklines stay on complete weeks so a partial final point doesn't create a misleading dip in every WoW row.
10. **Rolling-window header integrity (check 11)** — the meta-line "Last 7 days" dates must agree with the filename's end-date, so a stale stub can never be published as fresh.
11. **Noise-gate checks 13–18** — these enforce Step 3e's classification end-to-end and are the reason
    Section 2 stays readable:
    - **13** — Section 2 boilerplate uniformity: each attention row needs its own specific one-line body.
    - **14** — the top attention row must not be flat; Section 2 leads with what is `NEW`, never with the highest-volume row.
    - **15** — `VOLATILE` / `RECOVERY` rows must not headline a `Δ WoW` percentage (`suppressRatio`).
    - **16** — every visible attention row carries an inline 9-week `.item-spark`.
    - **17** — Section 2 visible-row budget: ≤ 8 rows including wins.
    - **18** — the 60-day section is a detector, not a catalog: at most 6 charts outside a collapsed fold.

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
- **Do NOT filter the partial in-progress week at the source in the 60-day trend queries** — the chart ends today and wants that partial week as its final bar. Exclude it from the regression/improvement **delta math** instead by running `bucket-trends.js --end=<startofweek(today)> --include-partial-end`: the `--end` cutoff drops the partial week from first/last/delta classification while `--include-partial-end` keeps it in the emitted `series`. Skipping `--end` (or the cutoff) would make `bucket-trends.js` show every error as a fake −99% improvement. The per-row `wow-table-sparkline-series.kql` is the exception — it keeps 8 complete weeks (`<SPARK_END>` = `startofweek(today)`, with the partial week filtered at the source) so no WoW row ends on a misleading partial dip.
- **Never carry a numeric telemetry value forward between runs.** Every KPI, table cell, delta %, device/request count, sparkline point, and verdict number must be re-pulled from Kusto for *this* run — never copied from a previous report, from a checkpoint/summary, from notes, or from memory. Telemetry shifts between runs and stale numbers read as fabricated. Near-miss precedent: a `no_tokens_found` count was about to be carried as ~23.7M when the actual current-window value was ~4.86M — a ~5× error that only the re-pull caught. If a number isn't backed by a query result file in this run's `_data/<end-date>/`, it does not go in the report.
- **Never hardcode the "Generated" date.** It is the *run* date in **UTC**, auto-stamped by `bootstrap-report.ps1` (which uses `(Get-Date).ToUniversalTime()`). If you rebuild the body programmatically, derive it live with a **UTC-date** formatter (`new Date().toISOString().slice(0,10)` in Node, `[datetime]::UtcNow.ToString('yyyy-MM-dd')` in PowerShell) — never paste a literal, and stay on UTC so the assembler can never stamp a different day than `bootstrap-report.ps1`. The v8 "Generated 2026-06-15 on a 2026-06-18 file" bug came from a hardcoded string in the assembler. (Reporting-week / baseline / 60d window dates are author-set and verified against the user's intended Sunday bucket — see template-readme "Date fields".)
- **Originator pre-check is mandatory.** A card cannot claim `Originator: Broker` without first running [`assets/queries/error-message-and-location.kql`](assets/queries/error-message-and-location.kql) and reading the throw site + top 3 `error_message` strings. If the throw site is in `common/ExceptionAdapter.{getExceptionFromTokenErrorResponse, exceptionFromAuthorizationResult}` AND the message starts with `AADSTS`, the originator is **eSTS, not broker** — see the AADSTS reference in [`assets/docs/kusto-cheatsheet.md`](assets/docs/kusto-cheatsheet.md).
- **WoW-movers pass is mandatory.** The 60d bucketer's `--peak-floor` silently drops sub-10K-device codes, so [`assets/queries/wow-movers.kql`](assets/queries/wow-movers.kql) MUST be run as a separate pass for both `error_code` and `error_type` (per Step 3d). Its output is **merged into the single regression callout** and then grouped by Step 3e's novelty labels. Do not render a separate "emerging" callout. Skipping the pass is how the Apr 26 `Failed to parse JWT` spike (7 → 3,461 devs over 7 weeks) hid for two reports running.
- **Novelty classification is mandatory, and Section 2 is ordered by it — never by volume.** Run [`classify-novelty.js`](assets/scripts/classify-novelty.js) (Step 3e) and lead with `NEW`. Ranking the attention list by device count is a known, reported defect: it put `IntuneAppProtectionPolicyRequiredException` (ΔWoW **+0.1%**, classifier says `ONGOING` and *falling*) at #1 while the genuinely new `ipc_*` family sat at #6/#9/#10. If the `NEW` bucket is empty, write "nothing new this week" — do not backfill it with `ONGOING` items.
- **Section 2's visible rows are the classifier's `attention` set plus at most 2 wins — nothing else.**
  `NEW` + `ACCELERATING` visible with sparklines; `ONGOING` inside a collapsed `<details class="fold">`.
  Budget: **≤ 8 visible rows total, wins included** (validator check 17 warns above it and counts wins).
  The failure mode this replaces is measured, not hypothetical: 13 visible rows, 0 charts, and the
  60-day section below carrying 38.
- **Every visible attention row carries a 9-week `.item-spark`.** Validator check 16 hard-fails
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

- [ ] New `oncall-wow-report-YYYY-MM-DD.html` (where `YYYY-MM-DD` is the resolved `curEnd` — the end-date of the rolling 7-day window) exists at `$env:USERPROFILE\android-oce-reports\` (NOT at repo root). If a file for this end-date already existed, the chat session explicitly stated what changed before regenerating.
- [ ] All sections present and populated (incl. 🚚 Traffic Attribution — even if “None this week”)
- [ ] **60-day trend bucketing run on the full cross-product** — `{error_code, error_type} × {devices, requests}` = 4 runs — union of regressions reported. Per-request retry storms (e.g. small device pool, exploding request count) are flagged on both axes. Source KQL spans the literal last 60 days ending today (no source-side partial-week filter); the partial current week is excluded from delta classification via `bucket-trends.js --end=<startofweek(today)> --include-partial-end` and charted as the final bar.
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
- [ ] **Sparklines rendered.** Every `.kpi` tile in the Top-line health section has a `data-spark` array with 8–9 weekly values. Every row in the 60-day trend tables and both WoW tables (codes + types) has a `data-trend` mini-spark. Note the 60-day trend `data-trend` arrays now end on the current partial week (≈9 points incl. the in-progress bar), while the WoW-table sparklines keep 8 complete weeks. The validator's chart-coverage check passes (KPI coverage ≥1/2 of tiles, total elements ≥15). Past failure mode: the v7 body rebuild dropped all sparklines silently — see `template-readme.md` § "Sparklines are MANDATORY".
- [ ] **Code-attribution depth.** Every `.attr-card`'s Code attribution block uses the full 8-field `<div class="origin-row">` structure (Originator / Top throw site / Wrapper / Caller hot-spots / Underlying cause / Top error_messages / Likely PRs / Next step) per [`assets/docs/code-attribution-template.md`](assets/docs/code-attribution-template.md). A `pr-list`-only stub is **not acceptable** — the validator hard-fails this. Past failure mode (v7 third pass): all 10 cards shipped with PR-only stubs and lost the throw-site / wrapper / underlying-cause analysis.
- [ ] No stale text from previous weeks. (`Select-String -Pattern 'EXAMPLE CONTENT BELOW'` returns 0 — that's the unfinished-section sentinel. The template no longer ships `{{TOKEN}}` placeholders since v2; if the file still contains any `{{`, that's also a leftover.)
- [ ] `get_errors` clean on the HTML file.
