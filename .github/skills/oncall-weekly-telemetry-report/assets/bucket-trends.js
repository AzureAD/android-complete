#!/usr/bin/env node
/**
 * bucket-trends.js — Bucket every error code into 60-day trend categories.
 *
 * Input: a Kusto MCP JSON result file from a query of the form:
 *
 *   materialized_view('ErrorStatsMetrics')
 *   | where EventInfo_Time between (datetime(<start>) .. datetime(<end_exclusive>))
 *   | where isnotempty(error_code) and error_code != 'success'
 *   | summarize errs=sum(countOverall),
 *               devs=dcount_hll(hll_merge(countDevicesHll))
 *       by week=startofweek(EventInfo_Time), error_code
 *   | where week < datetime(<reporting_week_end_sunday>)   // drop partial end-week!
 *   | order by error_code asc, week asc
 *
 * (Use dcount_hll on countDevicesHll, NOT sum(countDevices) — see kusto-cheatsheet.md.)
 *
 * Usage:
 *   node bucket-trends.js <mcp-output.json>
 *       [--start=YYYY-MM-DD] [--end=YYYY-MM-DD]    # inclusive start, EXCLUSIVE end (week-bucket)
 *       [--peak-floor=N] [--metric=devs|reqs]
 *
 * --start defaults to the second-earliest week in the data (drops partial start week).
 * --end   defaults to the most recent week, but the script will WARN-AND-DROP any week
 *         where (latest EventInfo_Time in the bucket - week-start) < 6 days, because that
 *         is a partial end-week and will turn every error into a fake -99% improvement.
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
 */
const fs = require('fs');

const args = process.argv.slice(2);
const file = args.find(a => !a.startsWith('--'));
const startArg = (args.find(a => a.startsWith('--start=')) || '').split('=')[1];
const endArg   = (args.find(a => a.startsWith('--end='))   || '').split('=')[1];
const metric = ((args.find(a => a.startsWith('--metric=')) || '').split('=')[1] || 'devs').toLowerCase();
if (!['devs', 'reqs'].includes(metric)) {
  console.error(`--metric must be 'devs' or 'reqs', got '${metric}'`);
  process.exit(1);
}
const defaultFloor = metric === 'reqs' ? 100000 : 10000;
const peakFloor = +((args.find(a => a.startsWith('--peak-floor=')) || '').split('=')[1] || defaultFloor);
const metricIdx = metric === 'reqs' ? 0 : 1;  // [errs, devs] tuple

if (!file) {
  console.error('Usage: node bucket-trends.js <mcp-output.json> [--start=YYYY-MM-DD] [--end=YYYY-MM-DD] [--peak-floor=N] [--metric=devs|reqs]');
  process.exit(1);
}

const d = JSON.parse(fs.readFileSync(file, 'utf8'));
const items = d.results.items.slice(1); // first row is the schema
const series = {};
for (const [w, code, errs, devs] of items) {
  if (!series[code]) series[code] = {};
  series[code][w] = [errs, devs];
}
const weeks = [...new Set(items.map(r => r[0]))].sort();
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

const keep = weeks.filter(w => w >= startISO && (endISO ? w < endISO : true) && w !== droppedPartial);
console.log('All weeks:   ', weeks);
console.log('Trend weeks: ', keep, `(${keep.length} complete)`);
console.log('Metric:      ', metric, `(peak floor=${peakFloor.toLocaleString()})`);
if (keep.length < 4) {
  console.warn(`[bucket-trends] WARN: only ${keep.length} kept weeks — trend buckets will be unstable. Need >= 4 for meaningful regression/improvement classification.`);
}

const buckets = { regression: [], spike: [], improvement: [], flat: [] };
for (const [code, wd] of Object.entries(series)) {
  const vals = keep.map(w => (wd[w] || [0, 0])[metricIdx]);
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
  buckets[cat].push({ code, first, last, peak, delta: +(delta * 100).toFixed(1), series: vals });
}

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
