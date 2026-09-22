#!/usr/bin/env node
/**
 * bucket-trends.js -- Bucket every error code into 60-day trend categories.
 *
 * This tool operates ONLY on the 60-day trend section. Its buckets are ROLLING
 * 7-day windows anchored at curEnd (Kusto `bin_at(t, 7d, datetime(curEnd))`), NOT
 * Sun-Sat calendar weeks. Consequently the FINAL bucket is [curEnd-7d, curEnd) --
 * exactly the same window the primary/WoW section reports on -- so a key's WoW here
 * equals its WoW in the headline table by construction.
 *
 * ⚠️ HISTORY -- DO NOT REVERT TO startofweek(). Buckets used to be Sun-Sat calendar
 * weeks cut off at startofweek(curEnd). That basis LAGGED the report's rolling window
 * by up to a full week, so anything that turned in the last ~6 days was invisible to
 * the novelty gate downstream. Real case (2026-08-01 run, classifier week 07/19-07/26
 * vs report window 07/25-08/01, ONE day of overlap):
 *     authorization_pending   report +63.2%   classifier -37.1%   -> filed "ONGOING"
 *     expired_token           report +26.7%   classifier -51.0%   -> filed "ONGOING"
 * Both were real risers the OCE had already spotted by hand. Re-bucketing on bin_at
 * promoted both to ACCELERATING and simultaneously DEMOTED access_denied (a former
 * false positive, actually -53.2%). Alignment adds real signal and removes phantom
 * signal; it is not merely "more alerts".
 *
 * Input: a Kusto MCP JSON result file from a query of the form:
 *
 *   materialized_view('ErrorStatsMetrics')
 *   | where EventInfo_Time >= datetime(<trend_start>) and EventInfo_Time < datetime(<trend_end>)
 *   | where isnotempty(error_code) and error_code != 'success'
 *   | summarize errs=sum(countOverall),
 *               devs=dcount_hll(hll_merge(countDevicesHll))
 *       by week=bin_at(EventInfo_Time, 7d, datetime(<trend_end>)), error_code
 *   | order by error_code asc, week asc
 *
 * (Use dcount_hll on countDevicesHll, NOT sum(countDevices) -- see ../docs/kusto-cheatsheet.md.)
 *
 * WHICH BUCKET IS PARTIAL:
 *   60 is not a multiple of 7, so stepping back from curEnd in 7-day strides leaves
 *   the OLDEST bucket (curEnd-63d) covering only 4 days. That is the safe end to be
 *   partial on. Pass `--start=<curEnd-60d>` to drop it, leaving 8 complete rolling
 *   weeks. The NEWEST bucket is always complete, so classify == display and there is
 *   no fake -99%-improvement failure mode to guard against.
 *
 * ⚠️ ALWAYS PASS --end=<curEnd>, NOT JUST --start.
 *   The partial-end auto-drop heuristic below is guarded by `if (!endArg ...)`. Under
 *   rolling alignment the last bucket is genuinely complete, so leaving --end off would
 *   let a REAL 70% collapse be silently discarded as "looks partial". Passing
 *   --end=<curEnd> disables the heuristic; it filters nothing, because every bucket
 *   label is < curEnd by construction.
 *
 * LEGACY FLAGS (no-ops under rolling alignment, retained so old invocations don't crash):
 *   --include-partial-end   There is no partial end bucket any more. classifyWeeks
 *                           always equals weeks. Safe to omit; safe to pass.
 *
 * Usage:
 *   node bucket-trends.js <mcp-output.json>
 *       --start=YYYY-MM-DD --end=YYYY-MM-DD        # inclusive start, EXCLUSIVE end (bucket label)
 *       [--peak-floor=N] [--metric=devs|reqs]
 *
 * --start  drops the 4-day partial oldest bucket. Defaults to the second-earliest
 *          bucket in the data, which achieves the same thing -- but pass it explicitly.
 * --end    should be curEnd. See the warning above; omitting it re-enables a heuristic
 *          that is actively wrong for this bucketing scheme.
 *
 * --metric=devs  (default) buckets on weekly device counts (catches errors hitting more users)
 * --metric=reqs  buckets on weekly request counts        (catches per-device retry storms)
 *
 * Run BOTH metrics and union the regression sets. Reporting on devices alone misses
 * retry-storm spikes (e.g. kdfv2_key_derivation_error: 262 -> 5,374 reqs on ~57 devices).
 *
 * Buckets (computed across the kept weeks, defaulting to all-but-the-first):
 *   regression:  delta > +15%  (and not a single-week spike)
 *   spike:       peak >= 3 x mean(other weeks) and peak > 1.5 x max(first,last)
 *   improvement: delta < -15%
 *   flat:        otherwise
 *
 * Output flags (NEW v8):
 *   --summary               Suppress the verbose header (week list, partial-bucket
 *                           detection). Print only the bucket counts + the per-bucket
 *                           rows. Recommended for the standard skill workflow.
 *   --json=<path>           Also write a structured JSON sidecar with the bucketed
 *                           result for programmatic consumption (e.g. by a future
 *                           sparkline-data-generator script). The sidecar shape is:
 *                             {
 *                               "metric": "devs" | "reqs",
 *                               "weeks": [iso, ...],          // rolling 7d bucket starts
 *                               "classifyWeeks": [iso, ...],  // == weeks under rolling alignment
 *                               "includePartialEnd": bool,    // legacy; no effect
 *                               "buckets": {
 *                                 "regression": [ { code, first, last, peak, delta, series: [N,N,...] }, ... ],
 *                                 "spike":       [...],
 *                                 "improvement": [...],
 *                                 "flat":        [...]
 *                               }
 *                             }
 *                           (`series` follows `weeks` (display); first/last/delta/peak
 *                            follow `classifyWeeks` (complete).)
 */
const fs = require('fs');

const args = process.argv.slice(2);
const file = args.find(a => !a.startsWith('--'));
const startArg = (args.find(a => a.startsWith('--start=')) || '').split('=')[1];
const endArg   = (args.find(a => a.startsWith('--end='))   || '').split('=')[1];
const metric = ((args.find(a => a.startsWith('--metric=')) || '').split('=')[1] || 'devs').toLowerCase();
const summary = args.includes('--summary');
const includePartialEnd = args.includes('--include-partial-end');
const jsonArg = (args.find(a => a.startsWith('--json=')) || '').split('=')[1];
if (!['devs', 'reqs'].includes(metric)) {
  console.error(`--metric must be 'devs' or 'reqs', got '${metric}'`);
  process.exit(1);
}
const defaultFloor = metric === 'reqs' ? 100000 : 10000;
const peakFloor = +((args.find(a => a.startsWith('--peak-floor=')) || '').split('=')[1] || defaultFloor);
const metricIdx = metric === 'reqs' ? 0 : 1;  // [errs, devs] tuple
const keyCol = ((args.find(a => a.startsWith('--key=')) || '').split('=')[1] || 'error_code');

if (!file) {
  console.error('Usage: node bucket-trends.js <mcp-output.json> [--start=YYYY-MM-DD] [--end=YYYY-MM-DD] [--include-partial-end] [--peak-floor=N] [--metric=devs|reqs] [--key=error_code|unified_error_type] [--summary] [--json=path]');
  process.exit(1);
}

const d = JSON.parse(fs.readFileSync(file, 'utf8'));
// Schema row can be either an object {col: type} (MCP) or a string array [col, col, ...]
// (from assets/scripts/run-kql.ps1). Detect and locate the key column index so we
// don't assume positional order.
const schemaRow = d.results.items[0];
let colNames;
if (Array.isArray(schemaRow)) {
  colNames = schemaRow.map(String);
} else if (schemaRow && typeof schemaRow === 'object') {
  colNames = Object.keys(schemaRow);
} else {
  throw new Error('First row of results.items must be the schema row');
}
const iWeek = colNames.indexOf('week');
const iCode = colNames.indexOf(keyCol);
const iErrs = colNames.indexOf('errs');
const iDevs = colNames.indexOf('devs');
if (iWeek < 0 || iCode < 0 || iErrs < 0 || iDevs < 0) {
  throw new Error(`Schema must include week, ${keyCol}, errs, devs. Got [${colNames.join(', ')}]`);
}

const items = d.results.items.slice(1);
const series = {};
for (const r of items) {
  const w = r[iWeek], code = r[iCode], errs = r[iErrs], devs = r[iDevs];
  if (!series[code]) series[code] = {};
  series[code][w] = [errs, devs];
}
const weeks = [...new Set(items.map(r => r[iWeek]))].sort();
const startISO = startArg ? `${startArg}T00:00:00Z` : weeks[1]; // drop partial start week by default
const endISO   = endArg   ? `${endArg}T00:00:00Z`   : null;     // exclusive cutoff

// --- Partial end-week detection ---------------------------------------------
// LEGACY GUARD. Under rolling bin_at(t, 7d, curEnd) bucketing the newest bucket is
// always a complete 7 days, so this heuristic should never fire in the normal skill
// workflow -- and it MUST NOT, because a real 70% fleet collapse looks identical to
// a partial bucket and would be silently discarded. It stays only to protect ad-hoc
// invocations that omit --end. Passing --end=<curEnd> disables it (see the warning
// emitted just below).
function bucketTotal(w) {
  let t = 0;
  for (const wd of Object.values(series)) {
    const v = wd[w];
    if (v) t += v[metricIdx];
  }
  return t;
}
const totals = weeks.map(w => ({ w, t: bucketTotal(w) }));
const medianOf = arr => { const s = [...arr].sort((a,b)=>a-b); return s[Math.floor(s.length/2)] || 0; };
let droppedPartial = null;
if (!endArg) {
  console.warn('[bucket-trends] WARN: no --end given. Buckets are rolling 7-day windows anchored at curEnd, so the newest bucket is COMPLETE; the partial-end auto-drop heuristic below can therefore discard a genuine collapse. Pass --end=<curEnd> (it filters nothing) for the standard skill workflow.');
}
if (!endArg && weeks.length >= 4) {
  const last = totals[totals.length - 1];
  const prevMedian = medianOf(totals.slice(-4, -1).map(x => x.t));
  if (prevMedian > 0 && last.t < prevMedian * 0.3) {
    droppedPartial = last.w;
    console.warn(`[bucket-trends] WARN: dropping likely-partial end bucket ${last.w} (total=${last.t.toLocaleString()} vs median-of-prior-3=${prevMedian.toLocaleString()}). If this is a REAL collapse, re-run with --end=<curEnd> to keep it.`);
  }
}

// classKeep = buckets used for delta/spike/first/last classification.
// Under rolling alignment every kept bucket is a complete 7 days; --start drops the
// 4-day partial OLDEST bucket (curEnd-63d).
const classKeep = weeks.filter(w => w >= startISO && (endISO ? w < endISO : true) && w !== droppedPartial);
// displayKeep = buckets emitted in `series` / the JSON sidecar (what the chart draws).
// --include-partial-end is a legacy no-op under rolling alignment (there is no partial
// end bucket), so this normally mirrors classKeep exactly.
const displayKeep = (includePartialEnd && endISO)
  ? weeks.filter(w => w >= startISO && w <= endISO)
  : classKeep;
if (!summary) {
  console.log('All weeks:      ', weeks);
  console.log('Classify weeks: ', classKeep, `(${classKeep.length} complete)`);
  if (includePartialEnd && displayKeep.length !== classKeep.length) {
    console.log('Display weeks:  ', displayKeep, `(${displayKeep.length}, incl. partial current week)`);
  }
  console.log('Metric:         ', metric, `(peak floor=${peakFloor.toLocaleString()})`);
}
if (classKeep.length < 4) {
  console.warn(`[bucket-trends] WARN: only ${classKeep.length} classify weeks — trend buckets will be unstable. Need >= 4 for meaningful regression/improvement classification.`);
}

const buckets = { regression: [], spike: [], improvement: [], flat: [] };
for (const [code, wd] of Object.entries(series)) {
  const vals = classKeep.map(w => (wd[w] || [0, 0])[metricIdx]);        // classification
  const displayVals = displayKeep.map(w => (wd[w] || [0, 0])[metricIdx]); // charted series
  const peak = Math.max(...vals);
  if (peak < peakFloor) continue;
  const first = vals[0] || 1, last = vals[vals.length - 1];
  const f = first || 1;
  const delta = (last - f) / f;
  const sumOthers = vals.reduce((s, x) => s + x, 0) - peak;
  const meanOthers = sumOthers / Math.max(1, vals.length - 1);
  const isSpike = peak >= 3 * meanOthers && peak > Math.max(first, last) * 1.5;
  let cat;
  if (isSpike) cat = 'spike';
  else if (delta > 0.15) cat = 'regression';
  else if (delta < -0.15) cat = 'improvement';
  else cat = 'flat';
  buckets[cat].push({ code, first, last, peak, delta: +(delta * 100).toFixed(1), series: displayVals });
}

// Compact bucket-count line (always emitted, summary or verbose)
const countLine = ['regression','spike','improvement','flat']
  .map(k => `${k}=${buckets[k].length}`).join('  ');
console.log(`\nBucket counts (metric=${metric}, key=${keyCol}, peak-floor=${peakFloor.toLocaleString()}):  ${countLine}`);

for (const k of ['regression', 'improvement', 'spike', 'flat']) {
  console.log(`\n=== ${k.toUpperCase()} (${buckets[k].length}) ===`);
  buckets[k]
    .sort((a, b) => b.peak - a.peak)
    .forEach(r => {
      console.log(
        `  ${r.code.padEnd(60)} first=${String(r.first).padStart(11)} last=${String(r.last).padStart(11)} peak=${String(r.peak).padStart(11)} d=${r.delta >= 0 ? '+' : ''}${r.delta}% series=${JSON.stringify(r.series)}`
      );
    });
}

// Optional structured JSON sidecar
if (jsonArg) {
  const sidecar = {
    metric,
    key: keyCol,
    peakFloor,
    weeks: displayKeep,
    classifyWeeks: classKeep,
    includePartialEnd,
    droppedPartial,
    buckets: Object.fromEntries(
      Object.entries(buckets).map(([k, arr]) => [
        k,
        arr.sort((a, b) => b.peak - a.peak)
      ])
    )
  };
  fs.writeFileSync(jsonArg, JSON.stringify(sidecar, null, 2));
  console.log(`\nWrote JSON sidecar -> ${jsonArg}`);
}
