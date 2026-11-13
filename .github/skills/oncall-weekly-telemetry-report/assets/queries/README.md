# `assets/queries/` — canonical KQL templates

Each `.kql` here is a paste-and-replace template for one of the queries the OCE
report needs. The reporting window is a **rolling 7-day window** ending at
start-of-day UTC on `-EndDate` (default: today). See
[`../scripts/bootstrap-report.ps1`](../scripts/bootstrap-report.ps1) for the
canonical window computation, and SKILL.md § "Inputs to confirm" for the
rationale.

## Placeholder convention

| Token | Meaning |
|---|---|
| `<CUR_END>` | Exclusive upper bound of the current 7-day window (e.g. `2026-07-09` on a run at any local time on 2026-07-09 UTC). |
| `<CUR_START>` | `<CUR_END> - 7d` (e.g. `2026-07-02`). Inclusive lower bound of the current window. |
| `<PREV_START>` | `<CUR_END> - 14d` (e.g. `2026-06-25`). Inclusive lower bound of the prior 7-day baseline window. The baseline window is `[PREV_START, CUR_START)`. |
| `<TREND_START>` | First calendar day of the 60-day trend chart window: `CUR_END - 60d` (literal 60 days ending today). |
| `<TREND_END>` | Exclusive upper bound of the 60-day trend chart window: `CUR_END` (today), and the `bin_at(..., 7d, datetime(<TREND_END>))` anchor. The newest bucket is `[CUR_END - 7d, CUR_END)` and is complete. |
| `<SPARK_START>` | First label of the WoW-table sparkline series: `CUR_END - 56d`. |
| `<SPARK_END>` | Exclusive upper bound and `bin_at` anchor for sparklines: `CUR_END`. The sparkline window is 8 complete rolling weeks. |
| `<CODES_LIST>` | Comma-separated KQL string list, e.g. `'invalid_resource', 'null_pointer_error'` |
| `<TYPES_LIST>` | Same shape but for `unified_error_type`. |
| `<DIM>` | A single column name, replaced per dimension run. |

**The primary/WoW queries emit two rows per key (bucket = `prevStart` or `curStart`).** The JS helpers (`agg.js`, `summarize-attribution.js`) sort the bucket label lexicographically and treat the smaller value as "prev" and the larger as "cur" — so any pair of sortable datetimes works.

**The 60-day trend queries emit rolling 7-day buckets anchored at `<TREND_END>` over the literal last 60 days ending today** (`[CUR_END - 60d, CUR_END)`). Use `bin_at(<time column>, 7d, datetime(<TREND_END>))`. The newest bucket is exactly `[CUR_END - 7d, CUR_END)`, so classifier WoW equals the displayed WoW by construction. Because 60 is not a multiple of 7, the oldest bucket covers only 4 days and is dropped by `bucket-trends.js --start=<TREND_START>`. Always pass `--end=<TREND_END>` too; it disables the legacy partial-end auto-drop heuristic and filters no rows under rolling alignment. The `wow-table-sparkline-series.kql` file uses the same curEnd anchor and keeps 8 complete rolling weeks.

## File index

| File | Purpose | Section it feeds |
|---|---|---|
| [`reliability-auth-only.kql`](reliability-auth-only.kql) | Auth-only requests/devices for the current + prior 7-day windows | Top-line health, denominator caveat |
| [`broker-version-share.kql`](broker-version-share.kql) | Per-version share for the WoW window — **evidence for denominator caveat** | Denominator caveat callout, broker adoption |
| [`broker-version-share-wow.kql`](broker-version-share-wow.kql) | Single WoW snapshot of version share — fastest evidence for cohort transitions | Denominator caveat callout |
| [`60d-trend-codes.kql`](60d-trend-codes.kql) | Feeds `bucket-trends.js` for codes (curEnd-anchored rolling 7-day buckets; final bucket == displayed WoW window) | 60-day trend analysis |
| [`60d-trend-types.kql`](60d-trend-types.kql) | Feeds `bucket-trends.js` for types (curEnd-anchored rolling 7-day buckets; final bucket == displayed WoW window) | 60-day trend analysis |
| [`wow-movers.kql`](wow-movers.kql) | **MANDATORY second pass** — catches small-base codes that spiked sharply in the current window (below the 60d bucketer's reporting threshold). Run for both `error_code` and `error_type`. **Merge its output rows into the same regression callout** alongside the standard WoW table, then group them by `classify-novelty.js` labels (🆕 New / 📈 Ongoing / 🔁 Volatile / ↩️ Recovery) — **never by device count**. Do not render a separate "emerging" callout. | Section 2 regression callouts |
| [`attr-union-by-dim.kql`](attr-union-by-dim.kql) | **PREFERRED for WoW.** All 7 dims for N codes (or types) in ONE round-trip; pipe through `summarize-attribution.js --union`. | Spike attribution cards |
| [`attr-codes-by-dim.kql`](attr-codes-by-dim.kql) | Per-dim form (run 7 times). Fall back to this only when the union exceeds payload size. | Spike attribution cards |
| [`attr-types-by-dim.kql`](attr-types-by-dim.kql) | Per-dim form for type regressions | Spike attribution cards |
| [`type-subcode-decomposition.kql`](type-subcode-decomposition.kql) | 8th dim for type cards | Type spike-attribution cards |
| [`error-message-and-location.kql`](error-message-and-location.kql) | **MANDATORY** for every broker-tagged regression. Accepts BOTH `<CODES_LIST>` and `<TYPES_LIST>` so codes + types can be sliced in one round-trip. | Code attribution block |
| [`os-version-slice.kql`](os-version-slice.kql) | OS / OEM concentration (raw `android_spans`). **On-demand only** per Step 5 — don't slice every card. | OS-version dim in attribution cards (when applicable) |
| [`latency.kql`](latency.kql) | p50/p95/p99 by hot span for the WoW window | Latency section |
| [`app-share.kql`](app-share.kql) | Top calling apps for the WoW window | Traffic analysis |
| [`wow-table-sparkline-series.kql`](wow-table-sparkline-series.kql) | 8-week per-code/per-type sparkline series (curEnd-anchored rolling buckets, `<SPARK_START>`/`<SPARK_END>` tokens) for the WoW table `data-trend` arrays. **MANDATORY** — sparkline arrays must come from real data, never fabricated. | Section 6/7 tables |
