#!/usr/bin/env node
/**
 * agg.js — Per-error per-dimension top-N rollup with WoW deltas.
 *
 * Companion to bucket-trends.js / summarize-attribution.js. Whereas
 * summarize-attribution.js is for the cross-dimension cartesian roll-up
 * across many dims, this script is the daily workhorse: take one
 * "per-week × per-error × per-(one dim)" Kusto JSON file, print a
 * human-readable per-error breakdown of the top-N values of that dim
 * with previous-week vs current-week counts and a Δ%.
 *
 * Designed for the Spike Attribution cards. Run once per dim per error
 * cluster (span_name, calling_package_name, broker_version, etc.),
 * paste the output into the card.
 *
 * Input shape: a Kusto MCP JSON file produced by:
 *
 *   let codes = dynamic([...]);
 *   materialized_view('ErrorStatsMetrics')
 *   | where EventInfo_Time between (datetime(<prev_week>) .. datetime(<this_week_end>))
 *   | where error_code in (codes)            // or unified_error_type in (types)
 *   | extend wk = startofweek(EventInfo_Time)
 *   | where wk < datetime(<reporting_week_end_sunday>)   // drop partial end!
 *   | summarize devs = dcount_hll(hll_merge(countDevicesHll)),
 *               errs = sum(countOverall)
 *        by wk, error_code, <ONE_DIMENSION>
 *   | order by error_code asc, wk asc, devs desc
 *
 * Usage:
 *   node agg.js <input.json> <error_key> <dim_col> [<dim_col2> ...] [--top=N] [--metric=devs|reqs]
 *
 *   error_key: "error_code" or "ut" (when extended from MergeUiRequiredExceptions)
 *   dim_col:   the column you grouped by (e.g. span_name, calling_package_name)
 *              if you pass multiple, they are joined with " | " into a composite key
 *   --top=5    (default) top-N rows per error
 *   --metric=devs (default) | reqs
 */
const fs = require('fs');

const args = process.argv.slice(2);
const positional = args.filter(a => !a.startsWith('--'));
const file = positional[0];
const errKey = positional[1] || 'error_code';
const dimCols = positional.slice(2);
const topN = +((args.find(a => a.startsWith('--top=')) || '').split('=')[1] || 5);
const metric = ((args.find(a => a.startsWith('--metric=')) || '').split('=')[1] || 'devs').toLowerCase();

if (!file || dimCols.length === 0) {
  console.error('Usage: node agg.js <input.json> <error_key> <dim_col> [<dim_col2> ...] [--top=N] [--metric=devs|reqs]');
  process.exit(1);
}
if (!['devs', 'reqs'].includes(metric)) {
  console.error("--metric must be 'devs' or 'reqs'");
  process.exit(1);
}

function load(file) {
  const j = JSON.parse(fs.readFileSync(file, 'utf8'));
  const items = j.results.items.slice(1);
  const schema = Object.keys(j.results.items[0]);
  return { items, schema };
}

function pct(a, b) {
  if (!b) return a ? '+inf' : '0';
  return ((a - b) / b * 100).toFixed(1) + '%';
}

const { items, schema } = load(file);
const wkIdx = schema.indexOf('wk');
const errIdx = schema.indexOf(errKey);
const valIdx = schema.indexOf(metric === 'devs' ? 'devs' : 'errs');
const dimIdxs = dimCols.map(c => {
  const i = schema.indexOf(c);
  if (i < 0) {
    console.error(`Column '${c}' not found in schema: ${schema.join(', ')}`);
    process.exit(2);
  }
  return i;
});
if (wkIdx < 0 || errIdx < 0 || valIdx < 0) {
  console.error(`Required columns missing. schema=${schema.join(', ')} need wk, ${errKey}, ${metric === 'devs' ? 'devs' : 'errs'}`);
  process.exit(2);
}

// group: err -> dimkey -> wk -> value
const m = {};
const wks = new Set();
for (const r of items) {
  const wk = r[wkIdx], err = r[errIdx], val = r[valIdx];
  const dimKey = dimIdxs.map(i => (r[i] === null || r[i] === undefined || r[i] === '') ? '(blank)' : r[i]).join(' | ');
  wks.add(wk);
  m[err] = m[err] || {};
  m[err][dimKey] = m[err][dimKey] || {};
  m[err][dimKey][wk] = (m[err][dimKey][wk] || 0) + val;
}
const sortedWks = [...wks].sort();
if (sortedWks.length < 2) {
  console.warn(`[agg] WARN: only ${sortedWks.length} week bucket(s) in input — need >= 2 for WoW deltas.`);
}
const prevWk = sortedWks[0], curWk = sortedWks[sortedWks.length - 1];

console.log(`# ${file}  (dim: ${dimCols.join(' + ')}, metric: ${metric})`);
console.log(`#  WoW: ${prevWk.slice(0, 10)}  ->  ${curWk.slice(0, 10)}\n`);

for (const err of Object.keys(m).sort()) {
  const rows = Object.entries(m[err]).map(([k, v]) => ({
    key: k,
    prev: v[prevWk] || 0,
    cur:  v[curWk]  || 0,
  }));
  const total = rows.reduce((s, r) => s + r.cur, 0);
  rows.sort((a, b) => b.cur - a.cur);
  console.log(`## ${err}  (cur-week ${metric}=${total.toLocaleString()})`);
  for (const r of rows.slice(0, topN)) {
    const share = total ? (r.cur / total * 100).toFixed(1) : '0';
    console.log(
      '  ' + share.padStart(5) + '%' +
      '  Δ ' + pct(r.cur, r.prev).padStart(8) +
      '  prev=' + String(r.prev).padStart(11) +
      '  cur=' + String(r.cur).padStart(11) +
      '  ' + r.key
    );
  }
  console.log('');
}
