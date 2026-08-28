# Authenticator query pack

Canonical KQL for the **Authenticator app** half of the weekly OCE report.
Read [`../../docs/authapp-kusto-cheatsheet.md`](../../docs/authapp-kusto-cheatsheet.md) before
writing or editing anything here.

**Cluster** `https://idsharedeus2.eastus2.kusto.windows.net`
**Database** `d496be22d62a46b0a3cf67ea2e736fd8`

> ⚠️ These are **not** the Broker cluster/database, and Authenticator conventions are **not**
> Broker conventions. Do not carry `dcount_hll(hll_merge(...))`, `percentile_tdigest(...)`,
> `MergeAccountType`, or `EventInfo_Time` into these queries — none of them apply here.

## Token convention

All files use angle-bracket tokens, replaced before execution.
`bootstrap-report.ps1 -App authapp` prints every resolved value.

| Token | Meaning |
|---|---|
| `<CUR_START>` | `curEnd − 7d` — start of the reporting window |
| `<CUR_END>` | `curEnd` — **exclusive** upper bound |
| `<PREV_START>` | `curEnd − 14d` — start of the baseline window (its end is always `<CUR_START>`) |
| `<TREND_START>` / `<TREND_END>` | literal last 60 days ending today — partial final week **included** |
| `<SPARK_START>` / `<SPARK_END>` | last 8 **complete** Sun-Sat weeks — `<SPARK_END>` = `startofweek(curEnd)`, partial week **excluded at the source** |
| `<ERRORS_MV>` | a `*_Errors_MV_V1` view name |
| `<REASON_FILTER>` | optional `\| where Error in (...)` line, or blank |

## Files

| File | Returns | Used by report section |
|---|---|---|
| [`scenario-outcomes-wow.kql`](scenario-outcomes-wow.kql) | one row per (Scenario, Window) — Initiated / Succeeded / Failed / **Unknown**, rates, and device twins, for all 9 single-MV scenarios in one round-trip | 1 Health · 2 Scoreboard · 3 Attention · 6 Unknown |
| [`pn-completion-wow.kql`](pn-completion-wow.kql) | one row per (PN family, Window) — initiated, reacted, completion rate, Approved/Denied/Error split, for all 4 PN families | 2 Scoreboard · 7 PN split |
| [`scenario-60d-trend.kql`](scenario-60d-trend.kql) | `week, scenario, errs, devs` (+ extras) — **directly consumable by `bucket-trends.js --key=scenario`** | 4 60-day trend |
| [`scenario-sparkline-series.kql`](scenario-sparkline-series.kql) | 8 complete weeks per scenario — success rate + bad-outcome volume | sparklines in 2 / 4 / 6 |
| [`scenario-errors-wow.kql`](scenario-errors-wow.kql) | error reasons WoW for **one** scenario's `*_Errors_MV_V1` | 5 Attribution |
| [`scenario-errors-by-dim.kql`](scenario-errors-by-dim.kql) | the same reasons sliced across all 3 dims (AppVersion / OsLevel / DeviceInfoMake) in one round-trip | 5 Attribution |
| [`broker-api-responsiveness-wow.kql`](broker-api-responsiveness-wow.kql) | per-`BrokerApiName` volume, success rate, p50/p95/p99 — raw `brokeroperations` table | 8 Broker API |
| [`version-share-wow.kql`](version-share-wow.kql) | AppVersion share WoW — the denominator check | 1 Health · 10 Adoption |

## Query order for a run

1. `version-share-wow.kql` **first**. If the version mix moved materially, every downstream rate
   change has to be read against that. Running it last means re-reading every verdict.
2. `scenario-outcomes-wow.kql` + `pn-completion-wow.kql` — the scoreboard.
3. `scenario-60d-trend.kql` → `bucket-trends.js --key=scenario --end=<startofweek(curEnd)> --include-partial-end --peak-floor=1000`.
4. `scenario-sparkline-series.kql` — one pass, feeds every sparkline in the report.
5. For each scenario that regressed **and cleared the volume floor**: `scenario-errors-wow.kql`,
   then `scenario-errors-by-dim.kql` filtered to the reasons that actually moved.
6. `broker-api-responsiveness-wow.kql` — slowest query, run it while step 5 is being written up.

## The volume floor

A scenario with fewer than **~1,000 initiates** in the window is noise. A 12-initiate scenario
going 100% → 50% is two users, not an incident. Tag such rows `low-volume` and keep them out of
the regression callout. This has no Broker equivalent — Broker error codes are high-volume by
construction; Authenticator scenario funnels are not.

## Adding a query

Keep the header comment block: purpose, cluster, database, tokens, and any trap specific to the
view. The header is the only documentation a future run will read before pasting the query.
