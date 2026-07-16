#!/usr/bin/env node
/**
 * bucket-trends.js -- Bucket every error code into 60-day trend categories.
 *
 * This tool operates ONLY on the 60-day trend section, which still uses Sun-Sat
 * weekly buckets (Kusto startofweek()-aligned). The primary/WoW section uses a
 * rolling 7-day window and does NOT go through this script -- it consumes the
 * two-bucket outputs of reliability-auth-only.kql / wow-movers.kql / etc.
 * directly.
 *
 * Input: a Kusto MCP JSON result file from a query of the form:
 *
 *   materialized_view('ErrorStatsMetrics')
 *   | where EventInfo_Time >= datetime(<trend_start>) and EventInfo_Time < datetime(<trend_end>)
 *   | where isnotempty(error_code) and error_code != 'success'
 *   | summarize errs=sum(countOverall),
 *               devs=dcount_hll(hll_merge(countDevicesHll))
 *       by week=startofweek(EventInfo_Time), error_code
 *   | order by error_code asc, week asc
 *
 * (Use dcount_hll on countDevicesHll, NOT sum(countDevices) -- see ../docs/kusto-cheatsheet.md.)
 *
 * LITERAL 60-day window ending today:
 *   The 60d trend now spans [today-60d, today), so the query no longer filters the
 *   partial in-progress week at the source -- the CHART wants it as the final bar.
 *   Instead this script splits two week sets:
 *     * classification weeks (complete Sun-Sat only) drive first/last/delta/spike/
 *       peak-floor -- a partial week as "last" would read as a fake -99% improvement.
 *     * display weeks (adds the partial current week) drive the emitted `series`
 *       arrays and the JSON sidecar, so the sparkline/chart ends today.
 *   Pass `--end=<startofweek(today)> --include-partial-end` for this behavior.
 *   Without --include-partial-end the script behaves as before (display == classify).
 *
 * <end> convention: startofweek(today) -- i.e. the Sunday that OPENS the currently
 * in-progress week. Every complete week strictly before that Sunday is classified;
 * with --include-partial-end the in-progress week (bucket == <end>) is still charted.
 * See assets/scripts/bootstrap-report.ps1 for how this is computed ($trendClassEnd).
 *
 * Usage:
 *   node bucket-trends.js <mcp-output.json>
 *       [--start=YYYY-MM-DD] [--end=YYYY-MM-DD]    # inclusive start, EXCLUSIVE end (week-bucket)
 *       [--include-partial-end] [--peak-floor=N] [--metric=devs|reqs]
 *
 * --start defaults to the second-earliest week in the data (drops partial start week).
 * --end   defaults to the most recent week, but the script will WARN-AND-DROP any week
 *         where (latest EventInfo_Time in the bucket - week-start) < 6 days, because that
 *         is a partial in-progress week and will turn every error into a fake -99% improvement.
 * --include-partial-end  keep the partial current week (bucket >= --end) in the emitted
 *         `series` arrays / JSON sidecar for charting, while still EXCLUDING it from the
 *         delta/spike/first/last classification. No-op without --end.
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
 *                               "weeks": [iso, ...],          // DISPLAY weeks (incl. partial end)
 *                               "classifyWeeks": [iso, ...],  // complete weeks used for deltas
 *                               "includePartialEnd": bool,
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
// Compute the total devices/requests per bucket as a proxy for completeness.
// If the most recent bucket is < 30% of the median of the prior 3 buckets, it's
// almost certainly partial — drop it and warn. This catches the common case of
// running the report at 09:00 UTC Sunday and getting 9 hours of data in the
// "last week" bucket. (Caveat: real fleet collapses also look like this; warn,
// don't crash.)
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
if (!endArg && weeks.length >= 4) {
  const last = totals[totals.length - 1];
  const prevMedian = medianOf(totals.slice(-4, -1).map(x => x.t));
  if (prevMedian > 0 && last.t < prevMedian * 0.3) {
    droppedPartial = last.w;
    console.warn(`[bucket-trends] WARN: dropping likely-partial end bucket ${last.w} (total=${last.t.toLocaleString()} vs median-of-prior-3=${prevMedian.toLocaleString()}). Pass --end=YYYY-MM-DD to override or filter in KQL.`);
  }
}

// classKeep = complete Sun-Sat weeks used for delta/spike/first/last classification.
// A partial week here would produce a fake -99% improvement, so it is always excluded.
const classKeep = weeks.filter(w => w >= startISO && (endISO ? w < endISO : true) && w !== droppedPartial);
// displayKeep = weeks emitted in `series` / the JSON sidecar (what the chart draws).
// With --include-partial-end it adds the partial current week (bucket >= endISO up to
// endISO inclusive) so the chart ends today; otherwise it mirrors classKeep.
const displayKeep = includePartialEnd
  ? weeks.filter(w => w >= startISO && (endISO ? w <= endISO : true))
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
