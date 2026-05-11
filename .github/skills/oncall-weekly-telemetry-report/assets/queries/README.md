# `assets/queries/` — canonical KQL templates

Each `.kql` here is a paste-and-replace template for one of the queries the OCE
weekly report needs. Token convention:

| Token | Meaning |
|---|---|
| `<START>` | Sunday of the earliest week in the window, ISO date e.g. `2026-03-08` |
| `<END>` | Sunday immediately AFTER the reporting week (EXCLUSIVE upper bound). For a 2026-05-03 report use `2026-05-10`. |
| `<PREV_WEEK>` | Sunday of the prior week (the WoW baseline). |
| `<CODES_LIST>` | Comma-separated KQL string list, e.g. `'invalid_resource', 'null_pointer_error'` |
| `<TYPES_LIST>` | Same shape but for `unified_error_type`. |
| `<DIM>` | A single column name, replace per dimension run. |

**The `<END>` filter is mandatory.** Always include `| where week < datetime(<END>)` after the `summarize` so the partial in-progress week is dropped at the source. Otherwise `bucket-trends.js` will see a fake −99% improvement on every code (the partial bucket will look like a fleet-wide collapse).

## File index

| File | Purpose | Section it feeds |
|---|---|---|
| [`reliability-auth-only.kql`](reliability-auth-only.kql) | Per-week auth-only requests/devices | Top-line health, denominator caveat |
| [`broker-version-share.kql`](broker-version-share.kql) | Per-week per-version share — **evidence for denominator caveat** | Denominator caveat callout, broker adoption |
| [`broker-version-share-wow.kql`](broker-version-share-wow.kql) | Single WoW snapshot of version share — fastest evidence for cohort transitions | Denominator caveat callout |
| [`60d-trend-codes.kql`](60d-trend-codes.kql) | Feeds `bucket-trends.js` for codes | 60-day trend analysis |
| [`60d-trend-types.kql`](60d-trend-types.kql) | Feeds `bucket-trends.js` for types | 60-day trend analysis |
| [`wow-movers.kql`](wow-movers.kql) | **MANDATORY second pass** — catches small-base codes that spiked sharply this week (below the 60d bucketer's reporting threshold). Run for both `error_code` and `error_type`. **Merge its output rows into the single 🔴 WoW regressions callout** alongside the standard WoW table; tag rows that were absent or near-zero last week with `NEW`. Do not render a separate "emerging" callout. | 🔴 WoW regressions callout (Section 2) |
| [`attr-union-by-dim.kql`](attr-union-by-dim.kql) | **PREFERRED for 2-week WoW.** All 7 dims for N codes (or types) in ONE round-trip; pipe through `summarize-attribution.js --union`. | Spike attribution cards |
| [`attr-codes-by-dim.kql`](attr-codes-by-dim.kql) | Per-dim form (run 7 times). Fall back to this only when the union exceeds payload size or the time window is wider than 2 weeks. | Spike attribution cards |
| [`attr-types-by-dim.kql`](attr-types-by-dim.kql) | Per-dim form for type regressions | Spike attribution cards |
| [`type-subcode-decomposition.kql`](type-subcode-decomposition.kql) | 8th dim for type cards | Type spike-attribution cards |
| [`error-message-and-location.kql`](error-message-and-location.kql) | **MANDATORY** for every broker-tagged regression. Now accepts BOTH `<CODES_LIST>` and `<TYPES_LIST>` so codes + types can be sliced in one round-trip. | Code attribution block |
| [`os-version-slice.kql`](os-version-slice.kql) | OS / OEM concentration (raw `android_spans`). **On-demand only** per Step 5 — don't slice every card. | OS-version dim in attribution cards (when applicable) |
| [`latency.kql`](latency.kql) | p50/p95/p99 by hot span | Latency section |
| [`app-share.kql`](app-share.kql) | Top calling apps by week | Traffic analysis |
