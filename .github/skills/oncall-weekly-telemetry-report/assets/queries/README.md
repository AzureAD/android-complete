# `assets/queries/` — canonical KQL templates

Each `.kql` here is a paste-and-replace template for one of the queries the OCE
report needs. The reporting window is a **rolling 7-day window** ending at
start-of-day UTC on `-EndDate` (default: today). See
[`../scripts/bootstrap-report.ps1`](../scripts/bootstrap-report.ps1) for the
canonical window computation, and SKILL.md § "Inputs to confirm" for the
rationale (AB#3683194).

## Placeholder convention

| Token | Meaning |
|---|---|
| `<CUR_END>` | Exclusive upper bound of the current 7-day window (e.g. `2026-07-09` on a run at any local time on 2026-07-09 UTC). |
| `<CUR_START>` | `<CUR_END> - 7d` (e.g. `2026-07-02`). Inclusive lower bound of the current window. |
| `<PREV_START>` | `<CUR_END> - 14d` (e.g. `2026-06-25`). Inclusive lower bound of the prior 7-day baseline window. The baseline window is `[PREV_START, CUR_START)`. |
| `<TREND_START>` | First calendar day of the 60-day trend chart window: `CUR_END - 60d` (literal 60 days ending today). |
| `<TREND_END>` | Exclusive upper bound of the 60-day trend chart window: `CUR_END` (today). The final Sun-Sat bucket is the current in-progress (partial) week — charted, but excluded from delta classification. |
| `<TREND_CLASS_END>` | Delta-classification cutoff = `startofweek(CUR_END)` (the Sunday that opens the current in-progress week). Passed to `bucket-trends.js` as `--end` (with `--include-partial-end`). On a `2026-07-09` (Thu) run, that's `2026-07-05`. Bootstrap prints it as "Trend delta cutoff". |
| `<SPARK_START>` | First Sunday of the WoW-table sparkline series: `startofweek(CUR_END) - 56d`. **Sunday-aligned; 8 complete weeks.** |
| `<SPARK_END>` | Sunday that OPENS the current in-progress week, exclusive: `startofweek(CUR_END)`. The `| where week < datetime(<SPARK_END>)` filter keeps the WoW-row sparklines on 8 complete weeks. |
| `<CODES_LIST>` | Comma-separated KQL string list, e.g. `'invalid_resource', 'null_pointer_error'` |
| `<TYPES_LIST>` | Same shape but for `unified_error_type`. |
| `<DIM>` | A single column name, replaced per dimension run. |

**The primary/WoW queries emit two rows per key (bucket = `prevStart` or `curStart`).** The JS helpers (`agg.js`, `summarize-attribution.js`) sort the bucket label lexicographically and treat the smaller value as "prev" and the larger as "cur" — so any pair of sortable datetimes works.

**The 60-day trend queries emit Sun-Sat weekly buckets over the literal last 60 days ending today** (`[CUR_END - 60d, CUR_END)`). `startofweek()` is Sunday-aligned in Kusto. Do **not** filter the partial in-progress week at the source — the chart wants it as the final bar. Exclude it from the delta math via `bucket-trends.js --end=<TREND_CLASS_END> --include-partial-end` (`<TREND_CLASS_END>` = `startofweek(CUR_END)`); otherwise a partial "last" week reads as a fake −99% improvement on every code. The `wow-table-sparkline-series.kql` file is the exception: it keeps 8 complete weeks via `| where week < datetime(<SPARK_END>)` so no WoW row ends on a partial dip.

## File index

| File | Purpose | Section it feeds |
|---|---|---|
| [`reliability-auth-only.kql`](reliability-auth-only.kql) | Auth-only requests/devices for the current + prior 7-day windows | Top-line health, denominator caveat |
| [`broker-version-share.kql`](broker-version-share.kql) | Per-version share for the WoW window — **evidence for denominator caveat** | Denominator caveat callout, broker adoption |
| [`broker-version-share-wow.kql`](broker-version-share-wow.kql) | Single WoW snapshot of version share — fastest evidence for cohort transitions | Denominator caveat callout |
| [`60d-trend-codes.kql`](60d-trend-codes.kql) | Feeds `bucket-trends.js` for codes (Sun-Sat weekly buckets over the literal last 60 days ending today; final bar = partial current week) | 60-day trend analysis |
| [`60d-trend-types.kql`](60d-trend-types.kql) | Feeds `bucket-trends.js` for types (Sun-Sat weekly buckets over the literal last 60 days ending today; final bar = partial current week) | 60-day trend analysis |
| [`wow-movers.kql`](wow-movers.kql) | **MANDATORY second pass** — catches small-base codes that spiked sharply in the current window (below the 60d bucketer's reporting threshold). Run for both `error_code` and `error_type`. **Merge its output rows into the single 🔴 WoW regressions callout** alongside the standard WoW table; tag rows that were absent or near-zero in the prior window with `NEW`. Do not render a separate "emerging" callout. | 🔴 WoW regressions callout (Section 2) |
| [`attr-union-by-dim.kql`](attr-union-by-dim.kql) | **PREFERRED for WoW.** All 7 dims for N codes (or types) in ONE round-trip; pipe through `summarize-attribution.js --union`. | Spike attribution cards |
| [`attr-codes-by-dim.kql`](attr-codes-by-dim.kql) | Per-dim form (run 7 times). Fall back to this only when the union exceeds payload size. | Spike attribution cards |
| [`attr-types-by-dim.kql`](attr-types-by-dim.kql) | Per-dim form for type regressions | Spike attribution cards |
| [`type-subcode-decomposition.kql`](type-subcode-decomposition.kql) | 8th dim for type cards | Type spike-attribution cards |
| [`error-message-and-location.kql`](error-message-and-location.kql) | **MANDATORY** for every broker-tagged regression. Accepts BOTH `<CODES_LIST>` and `<TYPES_LIST>` so codes + types can be sliced in one round-trip. | Code attribution block |
| [`os-version-slice.kql`](os-version-slice.kql) | OS / OEM concentration (raw `android_spans`). **On-demand only** per Step 5 — don't slice every card. | OS-version dim in attribution cards (when applicable) |
| [`latency.kql`](latency.kql) | p50/p95/p99 by hot span for the WoW window | Latency section |
| [`app-share.kql`](app-share.kql) | Top calling apps for the WoW window | Traffic analysis |
| [`wow-table-sparkline-series.kql`](wow-table-sparkline-series.kql) | 8-week per-code/per-type sparkline series (Sun-Sat weekly buckets, `<SPARK_START>`/`<SPARK_END>` tokens — 8 complete weeks) for the WoW table `data-trend` arrays. **MANDATORY** — sparkline arrays must come from real data, never fabricated. | Section 6/7 tables |
